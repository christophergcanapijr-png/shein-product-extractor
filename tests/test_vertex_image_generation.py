from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.routes import images
from backend.services import vertex_image_service
from backend.services.vertex_image_service import (
    _should_clean_source,
    _temperature_for_prompt_strength,
    _should_use_virtual_try_on,
    _preset_view_instruction,
    build_vertex_image_prompt,
)


def test_vertex_image_prompt_includes_product_context() -> None:
    prompt = build_vertex_image_prompt(
        "white dress on a hanger",
        "vinted",
        {
            "sku": "SKU123",
            "title": "Long white lace dress",
            "colour": "White",
            "category": "Dress",
            "measurements": {"Longueur": "130 cm"},
        },
        has_guide_image=True,
        prompt_strength=95,
    )

    assert "white dress on a hanger" in prompt
    assert "natural Vinted listing photo style" in prompt
    assert "SKU123" in prompt
    assert "Longueur: 130 cm" in prompt
    assert "Do not add text, logos, watermarks" in prompt
    assert "guide image" in prompt
    assert "PROMPT LOCK STRENGTH: 95/100" in prompt


def test_reference_prompt_omits_conflicting_product_text_context() -> None:
    prompt = build_vertex_image_prompt(
        "Place the selected dress on the mannequin",
        "reference",
        {
            "sku": "SKU123",
            "title": "Blue dress",
            "colour": "Blue",
            "category": "Dress",
        },
        has_reference_image=True,
        has_product_source_image=True,
    )

    assert "selected product source image controls the exact garment colour" in prompt
    assert "Colour: Blue" not in prompt
    assert "Title: Blue dress" not in prompt


def test_mannequin_prompts_skip_virtual_try_on() -> None:
    assert not _should_use_virtual_try_on("Place the dress on the plastic mannequin")
    assert _should_use_virtual_try_on("Place the dress on a real model wearing it")


def test_mannequin_and_floor_presets_clean_source() -> None:
    assert _should_clean_source("anything", "mannequinFront")
    assert _should_clean_source("anything", "floorBack")
    assert _should_clean_source("Place the dress on the mannequin", None)
    assert not _should_clean_source("Replace the clothes of the girl", None)


def test_back_and_side_presets_add_view_control() -> None:
    back = _preset_view_instruction("backView", has_guide_image=True)
    side = _preset_view_instruction("sideView", has_guide_image=False)

    assert "true BACK VIEW" in back
    assert "mannequin must face away" in back
    assert "true RIGHT-SIDE VIEW" in side
    assert "No side-angle guide image" in side


def test_prompt_strength_controls_temperature() -> None:
    assert _temperature_for_prompt_strength(100) == 0.25
    assert _temperature_for_prompt_strength(90) == 0.35
    assert _temperature_for_prompt_strength(70) == 0.5
    assert _temperature_for_prompt_strength(10) == 0.8


def test_conversation_mannequin_edit_cleans_selected_product_source(monkeypatch) -> None:
    async def fake_load_product_image(product):
        return b"raw-product", "image/jpeg"

    def fake_clean_product_source(client, types, product_reference):
        assert product_reference == (b"raw-product", "image/jpeg")
        return b"clean-product", "image/png"

    def fake_conversation_edit(
        client,
        types,
        prompt,
        product_reference,
        reference_image,
        guide_image=None,
        conversation_image=None,
        example_image=None,
        prompt_preset=None,
        prompt_strength=95,
        negative_prompt=None,
    ):
        assert product_reference == (b"clean-product", "image/png")
        assert reference_image == (b"scene", "image/jpeg")
        return SimpleNamespace()

    monkeypatch.setattr(vertex_image_service, "_get_vertex_client", lambda: object())
    monkeypatch.setattr(vertex_image_service, "load_product_image", fake_load_product_image)
    monkeypatch.setattr(
        vertex_image_service,
        "_clean_product_source_image",
        fake_clean_product_source,
    )
    monkeypatch.setattr(
        vertex_image_service,
        "_generate_conversation_edit",
        fake_conversation_edit,
    )
    monkeypatch.setattr(
        vertex_image_service,
        "_extract_generated_image",
        lambda response: (b"png-bytes", "image/png"),
    )

    content, content_type = asyncio.run(
        vertex_image_service.generate_vertex_image(
            "Place the dress on the plastic mannequin",
            "reference",
            {"main_image_url": "/downloads/dress.png"},
            reference_image=(b"scene", "image/jpeg"),
            mode="conversation",
            prompt_preset="mannequinFront",
        )
    )

    assert content == b"png-bytes"
    assert content_type == "image/png"


def test_generate_image_route_returns_vertex_image(monkeypatch) -> None:
    async def fake_generate_vertex_image(
        prompt,
        style,
        product,
        reference_image=None,
        guide_image=None,
        prompt_preset=None,
        mode="guided",
        prompt_strength=95,
        negative_prompt=None,
        conversation_image=None,
        example_image=None,
    ):
        assert prompt == "show the dress clearly"
        assert style == "catalog"
        assert product is None
        assert reference_image is None
        assert guide_image is None
        assert mode == "conversation"
        assert prompt_strength == 95
        assert negative_prompt is None
        assert conversation_image is None
        assert example_image is None
        return b"png-bytes", "image/png"

    monkeypatch.setattr(images, "generate_vertex_image", fake_generate_vertex_image)

    response = asyncio.run(
        images.generate_image(
            prompt="show the dress clearly",
            product_id=None,
            style="catalog",
            prompt_preset=None,
            selected_image_url=None,
            reference_image=None,
            guide_image=None,
        )
    )

    assert response.body == b"png-bytes"
    assert response.media_type == "image/png"


def test_generate_image_route_accepts_reference_upload(monkeypatch) -> None:
    async def fake_read():
        return b"reference-bytes"

    fake_upload = SimpleNamespace(
        filename="mannequin.jpg",
        content_type="image/jpeg",
        read=fake_read,
    )

    async def fake_generate_vertex_image(
        prompt,
        style,
        product,
        reference_image=None,
        guide_image=None,
        prompt_preset=None,
        mode="guided",
        prompt_strength=95,
        negative_prompt=None,
        conversation_image=None,
        example_image=None,
    ):
        assert prompt == "place the dress on the mannequin"
        assert reference_image == (b"reference-bytes", "image/jpeg")
        assert guide_image is None
        assert mode == "conversation"
        assert prompt_strength == 95
        assert negative_prompt is None
        assert conversation_image is None
        assert example_image is None
        return b"png-bytes", "image/png"

    monkeypatch.setattr(images, "generate_vertex_image", fake_generate_vertex_image)

    response = asyncio.run(
        images.generate_image(
            prompt="place the dress on the mannequin",
            product_id=None,
            style="mockup",
            prompt_preset=None,
            selected_image_url=None,
            reference_image=fake_upload,
            guide_image=None,
        )
    )

    assert response.body == b"png-bytes"


def test_generate_image_route_accepts_guide_upload(monkeypatch) -> None:
    async def fake_read_scene():
        return b"scene-bytes"

    async def fake_read_guide():
        return b"guide-bytes"

    scene_upload = SimpleNamespace(
        filename="room.jpg",
        content_type="image/jpeg",
        read=fake_read_scene,
    )
    guide_upload = SimpleNamespace(
        filename="floor-guide.png",
        content_type="image/png",
        read=fake_read_guide,
    )

    async def fake_generate_vertex_image(
        prompt,
        style,
        product,
        reference_image=None,
        guide_image=None,
        prompt_preset=None,
        mode="guided",
        prompt_strength=95,
        negative_prompt=None,
        conversation_image=None,
        example_image=None,
    ):
        assert reference_image == (b"scene-bytes", "image/jpeg")
        assert guide_image == (b"guide-bytes", "image/png")
        assert mode == "conversation"
        assert prompt_strength == 95
        assert negative_prompt is None
        assert conversation_image is None
        assert example_image is None
        return b"png-bytes", "image/png"

    monkeypatch.setattr(images, "generate_vertex_image", fake_generate_vertex_image)

    response = asyncio.run(
        images.generate_image(
            prompt="floor front",
            product_id=None,
            style="mockup",
            prompt_preset="floorFront",
            selected_image_url=None,
            reference_image=scene_upload,
            guide_image=guide_upload,
        )
    )

    assert response.body == b"png-bytes"


def test_generate_image_route_accepts_previous_conversation_image(monkeypatch) -> None:
    async def fake_read_previous():
        return b"previous-bytes"

    previous_upload = SimpleNamespace(
        filename="previous.png",
        content_type="image/png",
        read=fake_read_previous,
    )

    async def fake_generate_vertex_image(
        prompt,
        style,
        product,
        reference_image=None,
        guide_image=None,
        prompt_preset=None,
        mode="guided",
        prompt_strength=95,
        negative_prompt=None,
        conversation_image=None,
        example_image=None,
    ):
        assert prompt == "make the hem shorter"
        assert mode == "conversation"
        assert prompt_strength == 95
        assert negative_prompt is None
        assert conversation_image == (b"previous-bytes", "image/png")
        assert example_image is None
        return b"png-bytes", "image/png"

    monkeypatch.setattr(images, "generate_vertex_image", fake_generate_vertex_image)

    response = asyncio.run(
        images.generate_image(
            prompt="make the hem shorter",
            product_id=None,
            style="reference",
            mode="conversation",
            prompt_preset=None,
            selected_image_url=None,
            reference_image=None,
            guide_image=None,
            conversation_image=previous_upload,
        )
    )

    assert response.body == b"png-bytes"


def test_generate_image_route_accepts_example_result_upload(monkeypatch) -> None:
    async def fake_read_example():
        return b"example-bytes"

    example_upload = SimpleNamespace(
        filename="good-gemini-result.png",
        content_type="image/png",
        read=fake_read_example,
    )

    async def fake_generate_vertex_image(
        prompt,
        style,
        product,
        reference_image=None,
        guide_image=None,
        prompt_preset=None,
        mode="guided",
        prompt_strength=95,
        negative_prompt=None,
        conversation_image=None,
        example_image=None,
    ):
        assert prompt == "make it realistic like the example"
        assert mode == "conversation"
        assert prompt_strength == 95
        assert negative_prompt is None
        assert example_image == (b"example-bytes", "image/png")
        return b"png-bytes", "image/png"

    monkeypatch.setattr(images, "generate_vertex_image", fake_generate_vertex_image)

    response = asyncio.run(
        images.generate_image(
            prompt="make it realistic like the example",
            product_id=None,
            style="reference",
            mode="conversation",
            prompt_preset=None,
            selected_image_url=None,
            reference_image=None,
            guide_image=None,
            example_image=example_upload,
            conversation_image=None,
        )
    )

    assert response.body == b"png-bytes"
