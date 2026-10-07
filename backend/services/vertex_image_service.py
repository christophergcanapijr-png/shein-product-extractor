from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Any

from backend.config import settings
from backend.errors import AppError
from backend.services.image_service import fetch_image


logger = logging.getLogger(__name__)

STYLE_PROMPTS = {
    "mockup": "clean ecommerce mockup, full product visible, neutral background",
    "vinted": "natural Vinted listing photo style, realistic home lighting",
    "catalog": "professional catalog photo, clean studio lighting",
    "lifestyle": "realistic lifestyle product photo, tasteful outfit context",
    "reference": "Use the attached reference scene exactly; do not apply an extra photo style.",
}


MANNEQUIN_WORDS = (
    "mannequin",
    "plastic mannequin",
    "dress form",
    "display form",
    "dummy",
    "bust form",
    "tailor form",
)

HUMAN_MODEL_WORDS = (
    "human model",
    "real model",
    "person wearing",
    "woman wearing",
    "girl wearing",
    "wearing the dress",
    "try on",
)

SOURCE_CLEAN_PRESETS = {
    "mannequinFront",
    "sideView",
    "backView",
    "floorFront",
    "floorBack",
}

DEFAULT_NEGATIVE_IMAGE_PROMPT = (
    "no human hands, no human arms, no fingers, no skin, no face, no hair, "
    "no feet, no shoes, no bag, no jewelry, no floating body parts, no extra "
    "person, no random model, no changed dress, no different color, no different "
    "pattern, no changed neckline, no changed sleeves, no altered silhouette, "
    "no changed background, no warped mannequin stand, no changed wooden neck knob, "
    "no changed metal floor base, no changed floor, no changed rug, no changed bed, "
    "no changed dresser, no changed wardrobe, no changed lamp, no blur, no crop, "
    "no zoom, no extra objects, no text, no watermark, no logo"
)

STRICT_IMAGE_EDITOR_SYSTEM_PROMPT = (
    "You are a strict fashion product image editor, not a creative image generator. "
    "Your job is controlled product replacement. Identify the mannequin torso or "
    "target placement area in the background image. Replace only the outfit/clothing "
    "area with the selected product reference. Preserve the selected product's exact "
    "color, print, shape, neckline, sleeve shape, fabric texture, opacity, length, "
    "and silhouette. Preserve the background image's room, lighting, crop, camera "
    "angle, floor, furniture, mannequin stand, metal base, and wooden neck knob. "
    "Do not invent fashion details. Do not beautify, redesign, recolor, restyle, "
    "or creatively reinterpret the dress. Absolute negatives: do not generate human "
    "skin, hands, arms, fingers, face, hair, feet, shoes, bags, jewelry, extra people, "
    "floating limbs, or body-part artifacts unless the user explicitly asks for a "
    "human model clothing replacement."
)


def _should_use_virtual_try_on(prompt: str) -> bool:
    text = prompt.casefold()
    if any(word in text for word in MANNEQUIN_WORDS):
        return False
    return any(word in text for word in HUMAN_MODEL_WORDS)


def _should_clean_source(prompt: str, prompt_preset: str | None = None) -> bool:
    if prompt_preset in SOURCE_CLEAN_PRESETS:
        return True
    text = prompt.casefold()
    if "replace the clothes" in text or "girl" in text or "human model" in text:
        return False
    return any(word in text for word in (*MANNEQUIN_WORDS, "floor", "flat", "side view", "back view"))


def _clamp_prompt_strength(prompt_strength: int | None) -> int:
    try:
        strength = int(prompt_strength if prompt_strength is not None else 95)
    except (TypeError, ValueError):
        return 95
    return max(0, min(100, strength))


def _temperature_for_prompt_strength(prompt_strength: int | None) -> float:
    strength = _clamp_prompt_strength(prompt_strength)
    if strength >= 95:
        return 0.25
    if strength >= 85:
        return 0.35
    if strength >= 70:
        return 0.5
    if strength >= 45:
        return 0.65
    return 0.8


def _prompt_lock_instruction(
    prompt_strength: int | None,
    negative_prompt: str | None = None,
) -> str:
    strength = _clamp_prompt_strength(prompt_strength)
    creativity = max(0, 100 - strength)
    negatives = (negative_prompt or DEFAULT_NEGATIVE_IMAGE_PROMPT).strip()
    return (
        f"PROMPT LOCK STRENGTH: {strength}/100. "
        f"CREATIVE FREEDOM: {creativity}/100. "
        "When prompt lock is high, obey the instruction literally and avoid artistic alternatives. "
        "If there is conflict between the user's text and the selected product image, follow the selected product image. "
        "If there is conflict between the user's text and the background reference, preserve the background reference. "
        f"NEGATIVE PROMPT / FORBIDDEN OUTPUTS: {negatives}"
    )


def _preset_view_instruction(prompt_preset: str | None, has_guide_image: bool) -> str:
    if prompt_preset == "backView":
        guide = (
            "Use the guide image as the main visual reference for back-facing direction, "
            "dress back length, and placement. "
            if has_guide_image
            else "No back-angle guide image was provided, so infer the back view from the dress while still forcing the mannequin to face away. "
        )
        return (
            "VIEW CONTROL: The final output must be a true BACK VIEW. The mannequin must face away from the camera. "
            "Do not show the front chest, front neckline, face/front torso, front pose, or front-facing dress panel. "
            f"{guide}"
            "Keep the same room/background unless the user says otherwise."
        )
    if prompt_preset == "sideView":
        guide = (
            "Use the guide image as the main visual reference for right-side direction, dress length, and placement. "
            if has_guide_image
            else "No side-angle guide image was provided, so infer the side view from the dress while still forcing the mannequin profile. "
        )
        return (
            "VIEW CONTROL: The final output must be a true RIGHT-SIDE VIEW. The mannequin must face camera-right in profile. "
            "Do not show a front-facing pose, front chest, or centered front view. "
            f"{guide}"
            "Keep the same room/background unless the user says otherwise."
        )
    if prompt_preset == "floorBack":
        return (
            "VIEW CONTROL: The final output must show the dress BACK SIDE facing up, flat on the floor. "
            "Do not show the front side of the dress."
        )
    if prompt_preset == "floorFront":
        return (
            "VIEW CONTROL: The final output must show the dress FRONT SIDE facing up, flat on the floor. "
            "Do not show the back side of the dress."
        )
    return ""


def _product_context(product: dict[str, Any] | None) -> str:
    if not product:
        return ""
    facts = [
        f"SKU: {product.get('sku')}",
        f"Title: {product.get('title')}",
        f"Colour: {product.get('colour')}",
        f"Category: {product.get('category')}",
    ]
    measurements = product.get("measurements") or {}
    if measurements:
        facts.append(
            "Measurements: "
            + ", ".join(f"{label}: {value}" for label, value in measurements.items())
        )
    return "\n".join(str(fact) for fact in facts if fact and not fact.endswith("None"))


def build_vertex_image_prompt(
    prompt: str,
    style: str,
    product: dict[str, Any] | None = None,
    has_reference_image: bool = False,
    has_guide_image: bool = False,
    has_product_source_image: bool = False,
    prompt_strength: int | None = 95,
    negative_prompt: str | None = None,
) -> str:
    style_prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["mockup"])
    context = _product_context(product)
    parts = [
        "Generate one realistic product image for resale listing preparation.",
        style_prompt,
        "Do not add text, logos, watermarks, UI, labels, or brand marks.",
        "Keep the product clear, large, sharp, and centered.",
        _prompt_lock_instruction(prompt_strength, negative_prompt),
        f"User request: {prompt.strip()}",
    ]
    if has_reference_image:
        parts.append(
            "Use the uploaded reference image as the target scene/composition. "
            "Place the selected product onto the mannequin or display form in "
            "that reference image while preserving the room, lighting, and pose. "
            "If the scene contains a mannequin, keep it as a mannequin only. "
            "Do not generate a real person, model, face, hair, skin, arms, hands, "
            "legs, or body parts."
        )
    if has_guide_image:
        parts.append(
            "A separate guide image may be included only for dress length, width, "
            "orientation, or placement. Do not use the guide image as the background."
        )
    if has_product_source_image:
        parts.append(
            "The selected product source image controls the exact garment colour, "
            "print, design, neckline, sleeves, silhouette, opacity, and fabric look. "
            "If any title, colour label, category, or text instruction conflicts with "
            "the selected product source image, ignore the text and follow the source "
            "image exactly."
        )
    if context and not has_product_source_image:
        parts.append(f"Verified product context:\n{context}")
    return "\n\n".join(parts)


def _get_vertex_client() -> Any:
    if not settings.vertex_ai_project:
        raise AppError(
            "vertex_project_missing",
            "Set VERTEX_AI_PROJECT in .env to use Vertex AI image generation.",
            status_code=400,
        )
    try:
        from google import genai

        return genai.Client(
            vertexai=True,
            project=settings.vertex_ai_project,
            location=settings.vertex_ai_location,
        )
    except Exception as exc:
        raise AppError(
            "vertex_auth_failed",
            (
                "Vertex AI authentication is not ready. Install Google Cloud CLI, "
                "run gcloud auth application-default login, enable Vertex AI API, "
                "then restart the app."
            ),
            status_code=401,
            retryable=True,
        ) from exc


async def load_product_image(product: dict[str, Any]) -> tuple[bytes, str]:
    image_url = str(product.get("main_image_url") or "").strip()
    if not image_url:
        raise AppError(
            "product_image_missing",
            "This product has no selected image to use as the dress reference.",
            status_code=422,
        )
    if image_url.startswith("/downloads/"):
        image_path = (settings.downloads_path / Path(image_url).name).resolve()
        downloads_root = settings.downloads_path.resolve()
        if not image_path.is_file() or image_path.parent != downloads_root:
            raise AppError(
                "product_image_missing",
                "The selected local product image could not be found.",
                status_code=404,
            )
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        return await asyncio.to_thread(image_path.read_bytes), mime_type
    if image_url.startswith("https://"):
        return await fetch_image(image_url)
    raise AppError(
        "product_image_invalid",
        "The selected product image is not a supported image reference.",
        status_code=422,
    )


def _extract_generated_image(response: Any) -> tuple[bytes, str]:
    response_texts: list[str] = []
    finish_reasons: list[str] = []
    prompt_feedback = getattr(response, "prompt_feedback", None)
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        finish_reason = getattr(candidate, "finish_reason", None)
        if finish_reason:
            finish_reasons.append(str(finish_reason))
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            text = getattr(part, "text", None)
            if text:
                response_texts.append(str(text))
            inline_data = getattr(part, "inline_data", None)
            image_bytes = getattr(inline_data, "data", None)
            mime_type = getattr(inline_data, "mime_type", None) or "image/png"
            if image_bytes:
                return image_bytes, mime_type

    images = getattr(response, "generated_images", None) or []
    if not images:
        detail_bits = []
        if response_texts:
            detail_bits.append("Gemini text response: " + " ".join(response_texts)[:600])
        if finish_reasons:
            detail_bits.append("Finish reason: " + ", ".join(finish_reasons))
        if prompt_feedback:
            detail_bits.append("Prompt feedback: " + str(prompt_feedback)[:600])
        raise AppError(
            "vertex_no_image",
            (
                "Vertex AI answered but did not return an image."
                + (f" {' '.join(detail_bits)}" if detail_bits else "")
            ),
            status_code=502,
            details={
                "text": " ".join(response_texts)[:2_000],
                "finish_reasons": finish_reasons,
                "prompt_feedback": str(prompt_feedback)[:2_000] if prompt_feedback else "",
            },
            retryable=True,
        )
    image = getattr(images[0], "image", None)
    image_bytes = getattr(image, "image_bytes", None)
    mime_type = getattr(image, "mime_type", None) or "image/png"
    if not image_bytes:
        raise AppError(
            "vertex_empty_image",
            "Vertex AI returned an empty image.",
            status_code=502,
            retryable=True,
        )
    return image_bytes, mime_type


def _strip_gemini_logo(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    try:
        from backend.services.logo_service import clean_product_image

        result = clean_product_image(image_bytes)
        if result.logo.found:
            logger.info(
                "Removed Gemini logo from generated image (%s, %.1f ms)",
                result.logo.corner,
                result.logo.elapsed_ms,
            )
        if result.metadata.stripped_bytes:
            logger.info(
                "Stripped %s bytes of metadata from generated image",
                result.metadata.stripped_bytes,
            )
        return result.data, result.content_type or mime_type
    except Exception as exc:
        logger.info("Image cleanup skipped: %s", exc)
    return image_bytes, mime_type


def _generate_gemini_reference_edit(
    client: Any,
    types: Any,
    final_prompt: str,
    product_reference: tuple[bytes, str],
    reference_image: tuple[bytes, str],
    guide_image: tuple[bytes, str] | None = None,
    prompt_strength: int | None = 95,
    negative_prompt: str | None = None,
) -> Any:
    product_bytes, product_mime_type = product_reference
    scene_bytes, scene_mime_type = reference_image
    parts = [
        types.Part(
            text=(
                f"{final_prompt}\n\n"
                f"{_prompt_lock_instruction(prompt_strength, negative_prompt)}\n\n"
                "You will receive images in this order. Image A is the exact selected "
                "dress/product. Image B is the target background/scene. "
                "If Image C is provided, use it only as a guide for dress length, "
                "width, orientation, and placement; never use Image C as the background. "
                "Use only the garment from Image A as the clothing source. Image A is "
                "the highest priority reference for the garment. Preserve Image A's "
                "exact colour, print, design, neckline, sleeves, silhouette, opacity, "
                "fabric look, and length. Do not recolour the dress. "
                "If Image A contains a person or model wearing the garment, "
                "ignore and remove that person completely; copy only the dress. "
                "Do not use or invent a different dress, colour, pattern, "
                "silhouette, or product. "
                "Place only the Image A dress onto Image B, preserving the room "
                "lighting and composition. If Image B contains a mannequin, keep "
                "the mannequin as an object, not a human. No real person, woman, "
                "girl, face, hair, skin, arms, hands, legs, feet, or realistic body "
                "parts unless the user explicitly asks to replace clothes on a human "
                "model. Do not change the room, camera angle, crop, or background. "
                "Return only the final edited image."
            )
        ),
        types.Part(text="Image A: exact selected dress/product source."),
        types.Part(
            inlineData=types.Blob(
                data=product_bytes,
                mimeType=product_mime_type,
            )
        ),
        types.Part(text="Image B: target background/mannequin/scene reference."),
        types.Part(
            inlineData=types.Blob(
                data=scene_bytes,
                mimeType=scene_mime_type,
            )
        ),
    ]
    if guide_image:
        guide_bytes, guide_mime_type = guide_image
        parts.extend(
            [
                types.Part(
                    text=(
                        "Image C: guide reference only for length, width, "
                        "orientation, and placement. Do not use as background."
                    )
                ),
                types.Part(
                    inlineData=types.Blob(
                        data=guide_bytes,
                        mimeType=guide_mime_type,
                    )
                ),
            ]
        )
    return client.models.generate_content(
        model=settings.vertex_ai_recontext_model,
        contents=[
            types.Content(
                role="user",
                parts=parts,
            ),
        ],
        config=types.GenerateContentConfig(
            responseModalities=["TEXT", "IMAGE"],
            temperature=_temperature_for_prompt_strength(prompt_strength),
            top_p=0.95,
            imageConfig=types.ImageConfig(
                aspectRatio="3:4",
                imageSize="1K",
                outputMimeType="image/png",
                personGeneration="ALLOW_ADULT",
            ),
        ),
    )


def _generate_conversation_edit(
    client: Any,
    types: Any,
    prompt: str,
    product_reference: tuple[bytes, str] | None,
    reference_image: tuple[bytes, str] | None,
    guide_image: tuple[bytes, str] | None = None,
    conversation_image: tuple[bytes, str] | None = None,
    example_image: tuple[bytes, str] | None = None,
    prompt_preset: str | None = None,
    prompt_strength: int | None = 95,
    negative_prompt: str | None = None,
) -> Any:
    view_instruction = _preset_view_instruction(prompt_preset, guide_image is not None)
    prompt_lock = _prompt_lock_instruction(prompt_strength, negative_prompt)
    opening_instruction = (
        "Act like a controlled Gemini.com fashion image editor. Follow the user's "
        "instruction directly and literally. Use the attached images as labeled "
        "visual references, not as a loose collage. Do not add listing/product "
        "constraints unless the user asks. If a previous generated image is included, "
        "treat it as the current canvas for the user's follow-up edit. If an ideal "
        "result example is included, match its realism, clean mannequin presentation, "
        "natural fabric drape, lighting consistency, and lack of human body-part "
        "artifacts. Do not copy the example dress unless the user asks; use it only "
        "as an output-quality reference."
    )
    instruction_text = "\n\n".join(
        part
        for part in (
            opening_instruction,
            prompt_lock,
            view_instruction,
            f"Execution Instructions:\n{prompt.strip()}",
        )
        if part
    )
    parts = [
        types.Part(text=instruction_text)
    ]
    if conversation_image:
        image_bytes, mime_type = conversation_image
        parts.extend(
            [
                types.Part(text="Current canvas / previous generated image."),
                types.Part(
                    inlineData=types.Blob(data=image_bytes, mimeType=mime_type)
                ),
            ]
        )
    if product_reference:
        image_bytes, mime_type = product_reference
        parts.extend(
            [
                types.Part(
                    text=(
                        "Design Texture Reference Source: selected product / dress source image. "
                        "This controls the exact garment design. Do not invent a different garment."
                    )
                ),
                types.Part(
                    inlineData=types.Blob(data=image_bytes, mimeType=mime_type)
                ),
            ]
        )
    if reference_image:
        image_bytes, mime_type = reference_image
        parts.extend(
            [
                types.Part(
                    text=(
                        "Background Context Anchor: scene / mannequin / room reference image. "
                        "Preserve this background, mannequin, stand, floor, furniture, crop, lighting, and perspective."
                    )
                ),
                types.Part(
                    inlineData=types.Blob(data=image_bytes, mimeType=mime_type)
                ),
            ]
        )
    if guide_image:
        image_bytes, mime_type = guide_image
        parts.extend(
            [
                types.Part(
                    text=(
                        "Optional guide reference image. For side/back presets, use this "
                        "to control viewpoint direction, dress length, width, and placement. "
                        "Do not use this as the background unless the user explicitly says so."
                    )
                ),
                types.Part(
                    inlineData=types.Blob(data=image_bytes, mimeType=mime_type)
                ),
            ]
        )
    if example_image:
        image_bytes, mime_type = example_image
        parts.extend(
            [
                types.Part(
                    text=(
                        "Ideal result example from Gemini.com. Use only as a quality/style "
                        "reference for realism, clean mannequin fit, lighting, fabric drape, "
                        "and no hands, arms, feet, hair, face, shoes, bags, or human artifacts."
                    )
                ),
                types.Part(
                    inlineData=types.Blob(data=image_bytes, mimeType=mime_type)
                ),
            ]
        )
    return client.models.generate_content(
        model=settings.vertex_ai_recontext_model,
        contents=[
            types.Content(
                role="user",
                parts=parts,
            )
        ],
        config=types.GenerateContentConfig(
            system_instruction=STRICT_IMAGE_EDITOR_SYSTEM_PROMPT,
            responseModalities=["TEXT", "IMAGE"],
            temperature=_temperature_for_prompt_strength(prompt_strength),
            top_p=0.95,
            imageConfig=types.ImageConfig(
                aspectRatio="3:4",
                imageSize="1K",
                outputMimeType="image/png",
                personGeneration="ALLOW_ADULT",
            ),
        ),
    )


def _clean_product_source_image(
    client: Any,
    types: Any,
    product_reference: tuple[bytes, str],
) -> tuple[bytes, str]:
    product_bytes, product_mime_type = product_reference
    response = client.models.generate_content(
        model=settings.vertex_ai_recontext_model,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        text=(
                            "Create a clean product-only reference image from Image A. "
                            "Extract only the dress/garment. Remove the human/model, face, "
                            "hair, skin, arms, hands, legs, feet, shoes, bag, jewelry, "
                            "phone, props, shadows from body parts, and all accessories. "
                            "Preserve the exact garment color, print, neckline, sleeves, "
                            "fabric texture, transparency, silhouette, and length. If body "
                            "parts cover the garment, reconstruct the missing garment area "
                            "naturally based on the visible dress. Put the garment alone on "
                            "a plain neutral background. Return only the cleaned garment image."
                        )
                    ),
                    types.Part(text="Image A: source photo containing the dress/garment."),
                    types.Part(
                        inlineData=types.Blob(
                            data=product_bytes,
                            mimeType=product_mime_type,
                        )
                    ),
                ],
            )
        ],
        config=types.GenerateContentConfig(
            responseModalities=["TEXT", "IMAGE"],
            temperature=0.2,
            top_p=0.95,
            imageConfig=types.ImageConfig(
                aspectRatio="3:4",
                imageSize="1K",
                outputMimeType="image/png",
                personGeneration="ALLOW_ADULT",
            ),
        ),
    )
    return _strip_gemini_logo(*_extract_generated_image(response))


def _generate_virtual_try_on(
    client: Any,
    types: Any,
    product_reference: tuple[bytes, str],
    reference_image: tuple[bytes, str],
) -> Any:
    product_bytes, product_mime_type = product_reference
    scene_bytes, scene_mime_type = reference_image
    return client.models.recontext_image(
        model=settings.vertex_ai_try_on_model,
        source=types.RecontextImageSource(
            personImage=types.Image(
                imageBytes=scene_bytes,
                mimeType=scene_mime_type,
            ),
            productImages=[
                types.ProductImage(
                    productImage=types.Image(
                        imageBytes=product_bytes,
                        mimeType=product_mime_type,
                    ),
                )
            ],
        ),
        config=types.RecontextImageConfig(
            numberOfImages=1,
            outputMimeType="image/png",
            personGeneration="ALLOW_ADULT",
            safetyFilterLevel="BLOCK_ONLY_HIGH",
        ),
    )


async def generate_vertex_image(
    prompt: str,
    style: str = "mockup",
    product: dict[str, Any] | None = None,
    reference_image: tuple[bytes, str] | None = None,
    guide_image: tuple[bytes, str] | None = None,
    prompt_preset: str | None = None,
    mode: str = "guided",
    prompt_strength: int | None = 95,
    negative_prompt: str | None = None,
    conversation_image: tuple[bytes, str] | None = None,
    example_image: tuple[bytes, str] | None = None,
) -> tuple[bytes, str]:
    if not prompt.strip():
        raise AppError(
            "image_prompt_required",
            "Enter a prompt before generating an image.",
            status_code=422,
        )

    from google.genai import types

    final_prompt = build_vertex_image_prompt(
        prompt,
        style,
        product,
        has_reference_image=reference_image is not None,
        has_guide_image=guide_image is not None,
        has_product_source_image=bool(reference_image and product),
        prompt_strength=prompt_strength,
        negative_prompt=negative_prompt,
    )
    product_reference = await load_product_image(product) if reference_image and product else None

    def call_vertex() -> Any:
        client = _get_vertex_client()
        source_reference = product_reference
        if source_reference and _should_clean_source(prompt, prompt_preset):
            try:
                source_reference = _clean_product_source_image(
                    client,
                    types,
                    source_reference,
                )
                logger.info("Cleaned selected product source before image edit.")
            except Exception as exc:
                logger.info(
                    "Source cleaning failed; using original selected product image: %s",
                    exc,
                )
        if mode == "conversation":
            return _generate_conversation_edit(
                client,
                types,
                prompt,
                source_reference,
                reference_image,
                guide_image,
                conversation_image,
                example_image,
                prompt_preset,
                prompt_strength,
                negative_prompt,
            )
        if reference_image and product_reference:
            if _should_use_virtual_try_on(prompt):
                try:
                    return _generate_virtual_try_on(
                        client,
                        types,
                        product_reference,
                        reference_image,
                    )
                except Exception as exc:
                    logger.info(
                        "Vertex virtual try-on failed; falling back to Gemini image edit: %s",
                        exc,
                    )
            else:
                logger.info("Skipping virtual try-on for mannequin/display-form prompt.")
            return _generate_gemini_reference_edit(
                client,
                types,
                final_prompt,
                source_reference,
                reference_image,
                guide_image,
                prompt_strength,
                negative_prompt,
            )
        return client.models.generate_images(
            model=settings.vertex_ai_image_model,
            prompt=final_prompt,
            config=types.GenerateImagesConfig(
                numberOfImages=1,
                aspectRatio="3:4",
                outputMimeType="image/png",
                addWatermark=False,
                enhancePrompt=_clamp_prompt_strength(prompt_strength) < 85,
                safetyFilterLevel="BLOCK_ONLY_HIGH",
                personGeneration="ALLOW_ADULT",
            ),
        )

    try:
        response = await asyncio.to_thread(call_vertex)
    except AppError:
        raise
    except Exception as exc:
        details: dict[str, Any] = {"reason": type(exc).__name__}
        response = getattr(exc, "response", None)
        if response is not None:
            text = getattr(response, "text", None)
            if text:
                details["response"] = str(text)[:2_000]
        raise AppError(
            "vertex_image_generation_failed",
            f"Vertex AI image generation failed: {exc}",
            status_code=502,
            details=details,
            retryable=True,
        ) from exc

    return _strip_gemini_logo(*_extract_generated_image(response))
