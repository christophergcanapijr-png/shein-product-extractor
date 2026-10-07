from __future__ import annotations

import asyncio
import json
import re

import pytest
from pydantic import ValidationError

from backend.schemas import ListingPair, ListingVersion
from backend.services import gemini_service
from backend.services.gemini_service import (
    FastListingDraft,
    _fast_prompt,
    _full_prompt,
    _replace_generic_output_label,
    apply_required_listing_format,
    fast_draft_titles_are_detailed,
    facts_are_descriptive_enough,
    extract_measurements_from_image,
    generate_listing,
    refresh_listing_with_measurements,
    listing_from_fast_draft,
    local_listing_from_product,
    load_reference_image,
    validate_gemini_payload,
)


def valid_payload() -> dict:
    tags = [f"#style{i}" for i in range(20)]
    return {
        "fictional_brand": "Velmora",
        "english": {
            "title": "Elegant pink midi dress size S",
            "description": "Pink midi dress with verified Size S measurements. Perfect condition.",
            "hashtags": tags,
        },
        "french": {
            "title": "Robe midi rose taille S",
            "description": "Robe midi rose avec mesures vérifiées en taille S. Parfait état.",
            "hashtags": [f"#mode{i}" for i in range(20)],
        },
    }


def test_valid_json_response_is_accepted() -> None:
    listing = validate_gemini_payload(json.dumps(valid_payload()))
    assert listing.english.title.startswith("Elegant")
    assert len(listing.french.hashtags) == 20
    assert listing.fictional_brand == "Velmora"


def test_markdown_fenced_json_is_repaired_locally() -> None:
    listing = validate_gemini_payload(
        "```json\n" + json.dumps(valid_payload()) + "\n```"
    )
    assert "Parfait état." in listing.french.description


def test_required_condition_phrase_is_applied_locally() -> None:
    payload = valid_payload()
    payload["english"]["description"] = "A concise listing."
    listing = validate_gemini_payload(payload)

    formatted = apply_required_listing_format(listing, {"measurements": {}}, "dress")

    assert formatted.english.description.endswith("Perfect condition.")


def test_forbidden_hashtags_are_removed() -> None:
    payload = valid_payload()
    payload["english"]["hashtags"] = ["#Vinted", "#France", "#New"] + [
        f"#ok{i}" for i in range(12)
    ]
    with pytest.raises(ValidationError):
        validate_gemini_payload(payload)


def test_fictional_brand_is_normalised_to_one_word() -> None:
    payload = valid_payload()
    payload["fictional_brand"] = "Two Words"

    listing = validate_gemini_payload(payload)

    assert listing.fictional_brand == "TwoWords"


def test_dress_description_uses_exact_extracted_size_s_measurements() -> None:
    listing = validate_gemini_payload(valid_payload())
    product = {
        "measurements": {
            "Poitrine": "75 cm",
            "Tour de taille": "70-96 cm",
            "Longueur": "129 cm",
        }
    }

    formatted = apply_required_listing_format(listing, product, "dress")

    blocks = formatted.english.description.split("\n\n")
    assert blocks[0] == formatted.english.title
    assert blocks.count(formatted.english.title) == 1
    assert blocks[1] == (
        "Bust: 75 cm · Waist: 70-96 cm · Length: 129 cm"
    )
    assert blocks[2] == "prices are negotiable :)"
    assert blocks[3] == "Perfect condition."
    assert "Poitrine : 75 cm" in formatted.french.description
    assert "prix négociable :)" in formatted.french.description
    assert "prices are negotiable :)" not in formatted.french.description
    assert formatted.french.description.endswith("Parfait état.")


def test_earrings_description_uses_only_extracted_length_and_width() -> None:
    listing = validate_gemini_payload(valid_payload())
    product = {
        "measurements": {
            "Poitrine": "75 cm",
            "Longueur": "6 cm",
            "Largeur": "2.5 cm",
        }
    }

    formatted = apply_required_listing_format(listing, product, "earrings")

    assert "Length: 6 cm" in formatted.english.description
    assert "Width: 2.5 cm" in formatted.english.description
    assert "75 cm" not in formatted.english.description


def test_bag_description_includes_verified_measurements_and_one_size() -> None:
    draft = FastListingDraft(
        fictional_brand="y2k",
        english_title=(
            "Women's shoulder bag with chain strap details, black, style y2k gothic"
        ),
        french_title=(
            "Sac bandoulière femme avec chaîne et rabat, noir, style y2k gothic"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#handbag", "#womenshandbag", "#y2k", "#shoulderbag", "#chic"],
        french_hashtags=["#sac", "#sacfemme", "#y2k", "#sacbandouliere", "#chic"],
    )
    listing = listing_from_fast_draft(draft, "bag")
    formatted = apply_required_listing_format(
        listing,
        {"measurements": {"Longueur": "30 cm", "Largeur": "20 cm"}},
        "bag",
    )

    assert formatted.fictional_brand in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
    }
    assert ", style " in formatted.english.title.casefold()
    assert "style" in formatted.french.title.casefold()
    assert "size s" not in formatted.english.title.casefold()
    assert "size m" not in formatted.english.title.casefold()
    assert "leather" not in formatted.english.title.casefold()
    assert "30 cm" not in formatted.english.title
    assert formatted.english.title.casefold().endswith("one size fits all")
    assert formatted.french.title.casefold().endswith("taille unique")
    assert formatted.english.description.split("\n\n") == [
        formatted.english.title,
        "Length: 30 cm · Width: 20 cm",
        "prices are negotiable :)",
        "Perfect condition.",
    ]
    assert formatted.french.description.split("\n\n") == [
        formatted.french.title,
        "Longueur : 30 cm · Largeur : 20 cm",
        "prix négociable :)",
        "Parfait état.",
    ]
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)


def test_bag_description_omits_measurement_line_when_none_present() -> None:
    draft = FastListingDraft(
        fictional_brand="y2k",
        english_title=(
            "Women's shoulder bag with chain strap details, black, style y2k gothic"
        ),
        french_title=(
            "Sac bandoulière femme avec chaîne et rabat, noir, style y2k gothic"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#handbag", "#womenshandbag", "#y2k", "#shoulderbag", "#chic"],
        french_hashtags=["#sac", "#sacfemme", "#y2k", "#sacbandouliere", "#chic"],
    )
    listing = listing_from_fast_draft(draft, "bag")
    formatted = apply_required_listing_format(listing, {"measurements": {}}, "bag")

    assert formatted.english.description.split("\n\n") == [
        formatted.english.title,
        "prices are negotiable :)",
        "Perfect condition.",
    ]


def test_plant_description_skips_size_but_includes_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="boho",
        english_title=(
            "Indoor plant with decorative ceramic pot, green, style boho elegant"
        ),
            french_title=(
                "Plante d'interieur avec pot decoratif et cache-pot, vert, style boho elegant"
            ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#plant", "#indoorplant", "#houseplant", "#succulent", "#boho"],
        french_hashtags=["#plante", "#planteinterieur", "#planteverte", "#succulente", "#boho"],
    )
    listing = listing_from_fast_draft(draft, "plant")
    formatted = apply_required_listing_format(
        listing,
        {"measurements": {"Longueur": "30 cm"}},
        "plant",
    )

    assert formatted.fictional_brand == "boho"
    assert ", style " in formatted.english.title.casefold()
    assert "size s" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n") == [
        formatted.english.title,
        "Length: 30 cm",
        "prices are negotiable :)",
        "Perfect condition.",
    ]
    assert formatted.french.description.split("\n\n") == [
        formatted.french.title,
        "Longueur : 30 cm",
        "prix négociable :)",
        "Parfait état.",
    ]
    assert "30 cm" in formatted.english.description


def test_local_bag_listing_skips_size_and_uses_style_token() -> None:
    listing = local_listing_from_product(
        {
            "sku": "BAG-1",
            "title": "Black women's shoulder bag with chain",
            "category": "Handbags",
            "colour": "Black",
            "measurements": {"Longueur": "28 cm"},
        },
        "bag",
    )

    assert "size s" not in listing.english.title.casefold()
    assert "size m" not in listing.english.title.casefold()
    assert listing.english.title.casefold().endswith("one size fits all")
    assert listing.french.title.casefold().endswith("taille unique")
    assert "bag" in listing.english.title.casefold() or "handbag" in listing.english.title.casefold()
    assert "sac" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert "28 cm" in listing.english.description
    assert "28 cm" not in listing.english.title
    assert listing.fictional_brand in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
    }


def test_bag_prompts_forbid_size_in_title_but_keep_measurements() -> None:
    product = {
        "sku": "BAG-1",
        "title": "Black women's handbag",
        "category": "Handbags",
        "colour": "Black",
        "measurements": {"Longueur": "28 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "bag", None)
    full_prompt = _full_prompt(product, "bag", None)

    for prompt in (fast_prompt, full_prompt):
        assert "style y2k gothic" in prompt.casefold()
        assert "Do not generate an image" in prompt
        assert "handbag" in prompt.casefold()
        assert "prices are negotiable :)" in prompt
        assert "Perfect condition" in prompt
        assert "one size fits all" in prompt.casefold()
        assert "28 cm" in prompt


def test_short_valid_hashtag_list_is_completed_locally_without_another_ai_call() -> None:
    payload = valid_payload()
    payload["english"]["hashtags"] = [f"#custom{i}" for i in range(15)]
    payload["french"]["hashtags"] = [f"#personnalise{i}" for i in range(15)]
    listing = validate_gemini_payload(payload)

    formatted = apply_required_listing_format(
        listing, {"measurements": {}}, "dress"
    )

    assert len(formatted.english.hashtags) == 20
    assert len(formatted.french.hashtags) == 20


def test_full_product_image_is_loaded_for_gemini(monkeypatch) -> None:
    expected = (b"high-resolution-image", "image/webp")

    async def fake_fetch_image(url: str) -> tuple[bytes, str]:
        assert url == "https://img.ltwebstatic.com/full-product.webp"
        return expected

    monkeypatch.setattr(gemini_service, "fetch_image", fake_fetch_image)

    result = asyncio.run(
        load_reference_image(
            {
                "main_image_url": (
                    "https://img.ltwebstatic.com/full-product.webp"
                )
            }
        )
    )

    assert result == expected


def test_reference_image_upgrades_shein_thumbnail(monkeypatch) -> None:
    expected = (b"full-resolution-image", "image/webp")

    async def fake_fetch_image(url: str) -> tuple[bytes, str]:
        assert url == "https://img.ltwebstatic.com/v4/j/pi/example.webp"
        return expected

    monkeypatch.setattr(gemini_service, "fetch_image", fake_fetch_image)

    result = asyncio.run(
        load_reference_image(
            {
                "main_image_url": (
                    "https://img.ltwebstatic.com/v4/j/pi/example"
                    "_thumbnail_220x293.webp"
                )
            }
        )
    )

    assert result == expected


def test_extract_measurements_from_image_parses_gemini_response(monkeypatch) -> None:
    async def fake_fetch_image(url: str) -> tuple[bytes, str]:
        assert url == "https://img.example.com/diagram.jpg"
        return (b"diagram-bytes", "image/jpeg")

    async def fake_generate_measurements_json(prompt, reference_image):
        assert reference_image == (b"diagram-bytes", "image/jpeg")
        return json.dumps(
            {
                "measurements": {
                    "Longueur": "16 cm",
                    "Largeur": "26 cm",
                    "Longueur des brides": "120 cm",
                },
                "weight": "0.20 kg",
            }
        )

    monkeypatch.setattr(gemini_service, "fetch_image", fake_fetch_image)
    monkeypatch.setattr(
        gemini_service, "_generate_measurements_json", fake_generate_measurements_json
    )

    result = asyncio.run(
        extract_measurements_from_image("https://img.example.com/diagram.jpg")
    )

    assert result == {
        "measurements": {
            "Longueur": "16 cm",
            "Largeur": "26 cm",
            "Longueur des brides": "120 cm",
        },
        "weight": "0.20 kg",
    }


def test_extract_measurements_from_image_handles_markdown_fenced_json(monkeypatch) -> None:
    async def fake_fetch_image(url: str) -> tuple[bytes, str]:
        return (b"diagram-bytes", "image/jpeg")

    async def fake_generate_measurements_json(prompt, reference_image):
        return "```json\n{\"measurements\": {\"Largeur\": \"26 cm\"}, \"weight\": null}\n```"

    monkeypatch.setattr(gemini_service, "fetch_image", fake_fetch_image)
    monkeypatch.setattr(
        gemini_service, "_generate_measurements_json", fake_generate_measurements_json
    )

    result = asyncio.run(
        extract_measurements_from_image("https://img.example.com/diagram.jpg")
    )

    assert result == {"measurements": {"Largeur": "26 cm"}, "weight": None}


def test_extract_measurements_from_image_returns_empty_when_nothing_readable(
    monkeypatch,
) -> None:
    async def fake_fetch_image(url: str) -> tuple[bytes, str]:
        return (b"plain-photo", "image/jpeg")

    async def fake_generate_measurements_json(prompt, reference_image):
        return json.dumps({"measurements": {}, "weight": None})

    monkeypatch.setattr(gemini_service, "fetch_image", fake_fetch_image)
    monkeypatch.setattr(
        gemini_service, "_generate_measurements_json", fake_generate_measurements_json
    )

    result = asyncio.run(
        extract_measurements_from_image("https://img.example.com/plain-photo.jpg")
    )

    assert result == {"measurements": {}, "weight": None}


def test_extract_measurements_from_image_raises_when_image_cannot_load(
    monkeypatch,
) -> None:
    async def fake_fetch_image(url: str) -> tuple[bytes, str]:
        raise gemini_service.AppError("image_domain_not_allowed", "no", status_code=400)

    monkeypatch.setattr(gemini_service, "fetch_image", fake_fetch_image)

    with pytest.raises(gemini_service.AppError) as error:
        asyncio.run(
            extract_measurements_from_image("https://not-allowed.example.com/a.jpg")
        )
    assert error.value.code == "measurement_image_unavailable"


def test_refresh_listing_with_measurements_inserts_line_into_saved_description() -> None:
    product = {
        "listing_type": "bag",
        "fictional_brand": "y2k",
        "measurements": {"Longueur": "16 cm", "Largeur": "26 cm"},
        "english_listing": {
            "title": "Women's crossbody bag with chain strap, brown, style y2k, one size fits all",
            "description": (
                "Women's crossbody bag with chain strap, brown, style y2k, "
                "one size fits all\n\nprices are negotiable :)\n\nPerfect condition."
            ),
            "hashtags": [f"#tag{i}" for i in range(20)],
        },
        "french_listing": {
            "title": "Sac croisé avec chaîne, marron, style y2k, taille unique",
            "description": (
                "Sac croisé avec chaîne, marron, style y2k, taille unique\n\n"
                "prix négociable :)\n\nParfait état."
            ),
            "hashtags": [f"#etiquette{i}" for i in range(20)],
        },
    }

    refreshed = refresh_listing_with_measurements(product)

    assert refreshed is not None
    assert "Length: 16 cm · Width: 26 cm" in refreshed.english.description
    assert "Longueur : 16 cm · Largeur : 26 cm" in refreshed.french.description
    assert refreshed.english.description.endswith("Perfect condition.")


def test_refresh_listing_with_measurements_returns_none_without_existing_listing() -> None:
    assert refresh_listing_with_measurements({"listing_type": "bag"}) is None


def test_refresh_listing_with_measurements_works_when_saved_hashtags_are_below_fifteen() -> None:
    # A saved listing can have fewer than 15 hashtags (e.g. after a manual
    # edit via the listing-edit endpoint, which has no minimum). Refreshing
    # measurements must not silently no-op just because ListingVersion's
    # generation-time validator would have rejected that hashtag count.
    for listing_type in ("mask", "necktie", "lamp", "shelf", "carpet", "cushion", "hat", "earrings"):
        title = "Sample title, style elegant"
        product = {
            "listing_type": listing_type,
            "fictional_brand": "y2k",
            "measurements": {"Longueur": "20 cm", "Largeur": "15 cm"},
            "english_listing": {
                "title": title,
                "description": f"{title}\n\nprices are negotiable :)\n\nPerfect condition.",
                "hashtags": ["#one", "#two"],
            },
            "french_listing": {
                "title": title,
                "description": f"{title}\n\nprix négociable :)\n\nParfait état.",
                "hashtags": ["#un", "#deux"],
            },
        }

        refreshed = refresh_listing_with_measurements(product)

        assert refreshed is not None, f"{listing_type} refresh unexpectedly returned None"
        assert "20 cm" in refreshed.english.description, listing_type
        assert "15 cm" in refreshed.english.description, listing_type
        assert len(refreshed.english.hashtags) >= 20, listing_type


def test_selected_image_gemini_failure_uses_local_fallback(monkeypatch) -> None:
    async def fake_load_reference_image(product):
        return (b"product-photo", "image/jpeg")

    async def fake_generate_json_text(prompt, reference_image):
        raise RuntimeError("gemini unavailable")

    monkeypatch.setattr(gemini_service, "load_reference_image", fake_load_reference_image)
    monkeypatch.setattr(gemini_service, "_generate_json_text", fake_generate_json_text)

    listing = asyncio.run(
        generate_listing(
            {
                "sku": "SKU1",
                "title": "Long black split skirt",
                "category": "Skirt",
                "colour": "Black",
                "measurements": {"Longueur": "90 cm"},
                "additional_details": {
                    "selected_description_image_url": "/downloads/black-skirt.png",
                },
            },
            "skirt",
        )
    )

    assert listing.english.title
    assert "gemini" not in listing.english.title.casefold()


def test_fast_title_quality_requires_detailed_titles() -> None:
    short_draft = FastListingDraft.model_construct(
        fictional_brand="Velora",
        english_title="Pink floral dress elegant style size S",
        french_title="Robe florale rose style elegant taille S",
        english_hashtags=["#dress", "#pink", "#floral", "#elegant", "#style"],
        french_hashtags=["#robe", "#rose", "#fleurs", "#elegant", "#style"],
    )
    detailed_draft = FastListingDraft(
        fictional_brand="Velora",
        english_title=(
            "Long floral wrap midi dress in soft pink with a V-neck, elegant boho style, size S"
        ),
        french_title=(
            "Robe longue portefeuille florale rose poudré à col V, style bohème élégant, taille S"
        ),
        english_hashtags=["#dress", "#pink", "#floral", "#elegant", "#style"],
        french_hashtags=["#robe", "#rose", "#fleurs", "#elegant", "#style"],
    )

    assert not fast_draft_titles_are_detailed(short_draft, "dress")
    assert fast_draft_titles_are_detailed(detailed_draft, "dress")


def test_fast_draft_builds_full_listing_locally() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Floral pink midi wrap dress with V-neck and fitted waist, elegant boho style, size S"
        ),
        french_title=(
            "Robe midi portefeuille florale rose à col V et taille ajustée, style bohème élégant, taille S"
        ),
        english_description=(
            "This floral midi dress has a balanced silhouette and a polished "
            "pink finish that works for daytime plans and evening occasions. "
            "Its visible neckline and shaping create an elegant look, while "
            "simple accessories can keep the outfit refined or make it more "
            "expressive."
        ),
        french_description=(
            "Cette robe midi fleurie présente une silhouette équilibrée et "
            "une teinte rose soignée adaptée aux sorties en journée comme aux "
            "occasions du soir. Son encolure visible et sa coupe composent une "
            "allure élégante, facile à associer à des accessoires discrets ou "
            "plus affirmés."
        ),
        english_hashtags=[
            "#floral",
            "#pinkdress",
            "#midi",
            "#elegant",
            "#summerdress",
            "#Vinted",
            "#new",
            "#occasion",
        ],
        french_hashtags=[
            "#robefleurie",
            "#roberose",
            "#robemidi",
            "#elegante",
            "#robeete",
            "#France",
            "#neuve",
            "#occasion",
        ],
    )

    listing = listing_from_fast_draft(draft, "dress")
    listing = apply_required_listing_format(
        listing,
        {"measurements": {"Longueur": "120 cm"}},
        "dress",
    )

    assert len(listing.english.hashtags) == 20
    assert len(listing.french.hashtags) == 20
    assert "#Vinted" not in listing.english.hashtags
    assert "#France" not in listing.french.hashtags
    assert listing.english.description.endswith("Perfect condition.")
    assert "Length: 120 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert "balanced silhouette" not in listing.english.description


def test_french_long_dress_title_is_reordered_locally() -> None:
    payload = valid_payload()
    payload["french"]["title"] = (
        "Longue robe florale bleue style élégant taille S"
    )
    listing = validate_gemini_payload(payload)

    formatted = apply_required_listing_format(
        listing, {"measurements": {}}, "dress"
    )

    assert formatted.french.title.startswith("Robe longue ")
    assert not formatted.french.title.startswith("Longue robe")
    assert formatted.french.description.startswith(
        formatted.french.title + "\n\n"
    )


def test_dress_titles_always_end_with_size_s() -> None:
    payload = valid_payload()
    payload["english"]["title"] = (
        "Size S long floral evening dress in blue with elegant style details"
    )
    payload["french"]["title"] = (
        "Taille S robe longue florale bleue avec détails style élégant"
    )
    listing = validate_gemini_payload(payload)

    formatted = apply_required_listing_format(
        listing,
        {"measurements": {}},
        "dress",
    )

    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert len(formatted.english.title) <= 100
    assert len(formatted.french.title) <= 100


def test_dress_m_description_uses_exact_extracted_size_m_measurements() -> None:
    listing = validate_gemini_payload(valid_payload())
    product = {
        "measurements": {
            "Poitrine": "92 cm",
            "Tour de taille": "78-104 cm",
            "Longueur": "132 cm",
        }
    }

    formatted = apply_required_listing_format(listing, product, "dress_m")

    blocks = formatted.english.description.split("\n\n")
    assert formatted.english.title.endswith("size M")
    assert formatted.french.title.endswith("taille M")
    assert blocks[0] == formatted.english.title
    assert blocks.count(formatted.english.title) == 1
    assert blocks[1] == (
        "Bust: 92 cm · Waist: 78-104 cm · Length: 132 cm"
    )
    assert blocks[2] == "prices are negotiable :)"
    assert blocks[3] == "Perfect condition."
    assert "Poitrine : 92 cm" in formatted.french.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.french.description.endswith("Parfait état.")


def test_dress_m_titles_always_end_with_size_m() -> None:
    payload = valid_payload()
    payload["english"]["title"] = (
        "Size S long floral evening dress in blue with elegant style details"
    )
    payload["french"]["title"] = (
        "Taille S robe longue florale bleue avec détails style élégant"
    )
    listing = validate_gemini_payload(payload)

    formatted = apply_required_listing_format(
        listing,
        {"measurements": {}},
        "dress_m",
    )

    assert formatted.english.title.endswith("size M")
    assert formatted.french.title.endswith("taille M")
    assert "size S" not in formatted.english.title
    assert "taille S" not in formatted.french.title
    assert len(formatted.english.title) <= 100
    assert len(formatted.french.title) <= 100


def test_dress_m_prompts_use_size_m() -> None:
    product = {
        "sku": "SKU1",
        "title": "Pink floral dress from page text",
        "category": "Dress",
        "colour": "Pink",
        "measurements": {"Longueur": "120 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "dress_m", None)
    full_prompt = _full_prompt(product, "dress_m", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size M" in prompt
        assert "taille M" in prompt
        assert "Size M" in prompt
    assert "Listing type: DRESS M" in full_prompt
    assert "DRESS M titles" in fast_prompt


def test_local_dress_m_listing_ends_with_size_m() -> None:
    listing = local_listing_from_product(
        {
            "sku": "SKU-M",
            "title": "Long black off-shoulder maxi dress with ruffle details",
            "category": "Dress",
            "colour": "Black",
            "measurements": {"Poitrine": "92 cm", "Longueur": "132 cm"},
            "additional_details": {},
        },
        "dress_m",
    )

    assert listing.english.title.endswith("size M")
    assert listing.french.title.endswith("taille M")
    assert "Bust: 92 cm" in listing.english.description
    assert "#sizeM" in listing.english.hashtags
    assert "#tailleM" in listing.french.hashtags


def test_dress_title_removes_bad_size_noise_and_generic_padding() -> None:
    payload = valid_payload()
    payload["english"]["title"] = (
        "dress in NudeTaille: L, style elegant, "
        "with a polished silhouette size S"
    )
    listing = validate_gemini_payload(payload)

    formatted = apply_required_listing_format(
        listing,
        {"measurements": {"Longueur": "133 cm"}},
        "dress",
    )

    assert "Taille" not in formatted.english.title
    assert "Taille: L" not in formatted.english.title
    assert "size L" not in formatted.english.title
    assert "polished silhouette" not in formatted.english.title
    assert "flowing full-length shape" not in formatted.english.title
    assert "refined cut details" not in formatted.english.title
    assert "nude" in formatted.english.title.casefold()
    assert ", style " in formatted.english.title.casefold()
    assert "in an elegant style" not in formatted.english.title.casefold()
    assert formatted.english.title.endswith("size S")


def test_short_french_dress_title_keeps_clean_style_without_forced_filler() -> None:
    payload = valid_payload()
    payload["french"]["title"] = (
        "Robe longue green, style elegant taille S"
    )
    listing = validate_gemini_payload(payload)

    formatted = apply_required_listing_format(
        listing,
        {"measurements": {}},
        "dress",
    )

    assert formatted.french.title.startswith("Robe longue")
    assert "vert" in formatted.french.title.casefold()
    assert "green" not in formatted.french.title.casefold()
    assert "coupe raffin" not in formatted.french.title
    assert "détails élégants" not in formatted.french.title
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.french.title.casefold()


def test_local_dress_fallback_cleans_french_colour_size_noise() -> None:
    listing = local_listing_from_product(
        {
            "sku": "sku-red-dress",
            "title": "Long dress",
            "category": "Dress",
            "colour": "Rouge foncéTaille: XXL",
            "measurements": {},
        },
        "dress",
    )

    assert "dark red" in listing.english.title
    assert "Rouge" not in listing.english.title
    assert "Taille" not in listing.english.title
    assert "XXL" not in listing.english.title
    assert "polished silhouette" not in listing.english.title
    assert "flowing full-length shape" not in listing.english.title
    assert listing.english.title.endswith("size S")
    assert listing.fictional_brand != "Velmora"


def test_local_fallback_brand_varies_by_product() -> None:
    red = local_listing_from_product(
        {
            "sku": "SKU-RED-001",
            "title": "Long red dress",
            "category": "Dress",
            "colour": "red",
            "main_image_url": "/downloads/red.png",
            "measurements": {},
        },
        "dress",
    )
    blue = local_listing_from_product(
        {
            "sku": "SKU-BLUE-002",
            "title": "Long blue dress",
            "category": "Dress",
            "colour": "blue",
            "main_image_url": "/downloads/blue.png",
            "measurements": {},
        },
        "dress",
    )

    assert red.fictional_brand != "Velmora"
    assert blue.fictional_brand != "Velmora"
    assert red.fictional_brand != blue.fictional_brand


def test_skirt_listing_repeats_title_and_keeps_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="Old Money",
        english_title=(
            "Pleated navy midi skirt with high waist and flowing shape, elegant old money style, size S"
        ),
        french_title=(
            "Jupe midi plissée bleu marine à taille haute et coupe fluide, style old money élégant, taille S"
        ),
        english_description="Short skirt description.",
        french_description="Description courte de la jupe.",
        english_hashtags=["#skirt", "#pleated", "#navy", "#elegant", "#outfit"],
        french_hashtags=["#jupe", "#plissee", "#bleumarine", "#elegante", "#tenue"],
    )
    listing = listing_from_fast_draft(draft, "skirt")

    formatted = apply_required_listing_format(
        listing,
        {
            "measurements": {
                "Longueur": "82 cm",
                "Tour de taille": "70 cm",
            }
        },
        "skirt",
        skirt_length="midi",
    )

    blocks = formatted.english.description.split("\n\n")
    french_blocks = formatted.french.description.split("\n\n")
    assert "midi" in formatted.english.title.casefold()
    assert formatted.english.title.endswith("size S")
    assert ", style " in formatted.english.title.casefold()
    assert "in an" not in formatted.english.title.casefold()
    assert "in a " not in formatted.english.title.casefold()
    assert "s-size belt" not in formatted.english.title.casefold()
    assert formatted.french.title.startswith("Jupe midi ")
    assert formatted.french.title.endswith("taille S")
    assert "ceinture" not in formatted.french.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert blocks == [
        formatted.english.title,
        "Length: 82 cm · Waist: 70 cm",
        "prices are negotiable :)",
        "Perfect condition.",
    ]
    assert french_blocks == [
        formatted.french.title,
        "Longueur : 82 cm · Tour de taille : 70 cm",
        "prix négociable :)",
        "État parfait",
    ]
    assert formatted.english.description.split("\n\n").count(formatted.english.title) == 1
    assert "82 cm" in formatted.english.description
    assert "70 cm" in formatted.english.description
    assert "Longueur : 82 cm" in formatted.french.description
    assert "Short skirt description." not in formatted.english.description
    assert "Description courte de la jupe." not in formatted.french.description
    assert "prices are negotiable :)" not in formatted.french.description
    assert 20 <= len(formatted.english.hashtags) <= 25
    assert formatted.fictional_brand == "oldmoney"


def test_short_skirt_titles_use_comma_style_and_size_s() -> None:
    tags = [f"#look{i}" for i in range(20)]
    listing = ListingPair(
        fictional_brand="elegant",
        english=ListingVersion(
            title="Long tobacco skirt, elegant style, size S",
            description="",
            hashtags=tags,
        ),
        french=ListingVersion(
            title="Jupe longue tabac, style élégant taille S",
            description="",
            hashtags=tags,
        ),
    )
    formatted = apply_required_listing_format(
        listing,
        {"title": "jupe longue tabac", "colour": "tabac", "category": "jupe"},
        "skirt",
        skirt_length="long",
    )

    assert formatted.french.title.startswith("Jupe longue")
    assert formatted.french.title.endswith("taille S")
    assert "ceinture" not in formatted.french.title.casefold()
    assert "tabac" in formatted.french.title.casefold()
    assert formatted.english.title.endswith("size S")
    assert ", style " in formatted.english.title.casefold()
    assert "in an" not in formatted.english.title.casefold()
    assert "s-size belt" not in formatted.english.title.casefold()
    assert "tobacco" in formatted.english.title.casefold()
    assert "defined waist" not in formatted.english.title.casefold()
    assert "floor-grazing" not in formatted.english.title.casefold()
    assert "tombé fluide" not in formatted.french.title.casefold()
    assert len(formatted.english.title) <= 100
    assert len(formatted.french.title) <= 100


def test_chatgpt_skirt_titles_are_preserved() -> None:
    tags = [f"#look{i}" for i in range(20)]
    listing = ListingPair(
        fictional_brand="gothic",
        english=ListingVersion(
            title="Long split skirt with buckle details, black, style gothic streetwear size S",
            description="",
            hashtags=tags,
        ),
        french=ListingVersion(
            title="Jupe longue fendue avec boucles, noire, style gothic streetwear taille S",
            description="",
            hashtags=tags,
        ),
    )
    formatted = apply_required_listing_format(
        listing,
        {"title": "jupe longue fendue", "colour": "black", "category": "jupe"},
        "skirt",
        skirt_length="long",
    )

    assert formatted.english.title.endswith("size S")
    assert ", style " in formatted.english.title.casefold()
    assert "gothic" in formatted.english.title.casefold()
    assert "s-size belt" not in formatted.english.title.casefold()
    assert "skirt" in formatted.english.title.casefold()
    assert "fendue" in formatted.french.title.casefold()
    assert "boucles" in formatted.french.title.casefold()
    assert formatted.french.title.endswith("taille S")
    assert "ceinture" not in formatted.french.title.casefold()
    assert "defined waist" not in formatted.english.title.casefold()
    assert "floor-grazing" not in formatted.english.title.casefold()


def test_skirt_prompts_require_size_s_and_forbid_belt() -> None:
    product = {
        "sku": "SKIRT-1",
        "title": "Long cream white maxi skirt",
        "category": "Skirts",
        "colour": "Cream",
        "measurements": {"Longueur": "82 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "skirt", "long")
    full_prompt = _full_prompt(product, "skirt", "long")

    for prompt in (fast_prompt, full_prompt):
        assert "style elegant size S" in prompt
        assert "Never mention a belt" in prompt or "never mention a belt" in prompt.casefold()
        assert "Do not generate an image" in prompt


def test_local_skirt_listing_uses_size_s_without_belt() -> None:
    listing = local_listing_from_product(
        {
            "sku": "SKIRT-NAVY-1",
            "title": "Pleated navy midi skirt",
            "category": "Skirts",
            "colour": "Navy",
            "measurements": {"Longueur": "82 cm", "Tour de taille": "70 cm"},
        },
        "skirt",
        skirt_length="midi",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert listing.french.title.startswith("Jupe")
    assert ", style " in listing.english.title.casefold()
    assert "s-size belt" not in listing.english.title.casefold()
    assert "in an" not in listing.english.title.casefold()
    assert "ceinture" not in listing.french.title.casefold()
    assert "navy" in listing.english.title.casefold()
    assert "midi" in listing.english.title.casefold()
    assert listing.english.description.split("\n\n")[0] == listing.english.title
    assert "82 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert "#navyskirt" in listing.english.hashtags
    assert "#sizeS" in listing.english.hashtags
    assert all("belt" not in tag.casefold() for tag in listing.english.hashtags)
    assert all("dress" not in tag.casefold() for tag in listing.english.hashtags)


def test_skirt_hashtags_match_product_and_vary_each_generate() -> None:
    navy = local_listing_from_product(
        {
            "sku": "SKIRT-A",
            "title": "Pleated navy midi skirt",
            "category": "Skirts",
            "colour": "Navy",
            "measurements": {},
            "_hashtag_nonce": "first",
        },
        "skirt",
        skirt_length="midi",
    )
    black = local_listing_from_product(
        {
            "sku": "SKIRT-B",
            "title": "Long split black skirt",
            "category": "Skirts",
            "colour": "Black",
            "measurements": {},
            "_hashtag_nonce": "second",
        },
        "skirt",
        skirt_length="long",
    )
    navy_again = local_listing_from_product(
        {
            "sku": "SKIRT-A",
            "title": "Pleated navy midi skirt",
            "category": "Skirts",
            "colour": "Navy",
            "measurements": {},
            "_hashtag_nonce": "third",
        },
        "skirt",
        skirt_length="midi",
    )

    assert "#navyskirt" in navy.english.hashtags
    assert "#pleatedskirt" in navy.english.hashtags
    assert "#blackskirt" in black.english.hashtags
    assert "#splitskirt" in black.english.hashtags
    assert navy.english.hashtags != black.english.hashtags
    assert navy.english.hashtags != navy_again.english.hashtags


def test_skirt_brand_skips_already_used_names() -> None:
    reserved = set(gemini_service.STYLE_BRAND_TOKENS)
    listing = local_listing_from_product(
        {
            "sku": "SKIRT-USED-1",
            "title": "Pleated navy midi skirt",
            "category": "Skirts",
            "colour": "Navy",
            "measurements": {},
        },
        "skirt",
        skirt_length="midi",
        reserved_brands=reserved,
    )

    assert listing.fictional_brand.casefold() not in reserved
    other = local_listing_from_product(
        {
            "sku": "SKIRT-USED-2",
            "title": "Long cream maxi skirt",
            "category": "Skirts",
            "colour": "Cream",
            "measurements": {},
        },
        "skirt",
        skirt_length="long",
        reserved_brands={*reserved, listing.fictional_brand},
    )
    assert other.fictional_brand.casefold() != listing.fictional_brand.casefold()
    assert other.fictional_brand.casefold() not in reserved


def test_hat_listing_uses_hat_template_and_style_token() -> None:
    draft = FastListingDraft(
        fictional_brand="Wild West",
        english_title=(
            "Brown cowboy hat with curved brim, style western cowgirl, one size fits all"
        ),
        french_title=(
            "Chapeau cowboy marron avec bord incurve, style western cowgirl, taille unique"
        ),
        english_description="Extra paragraph should not appear.",
        french_description="Paragraphe inutile a supprimer.",
        english_hashtags=["#hat", "#cowboyhat", "#brown", "#western", "#cowgirl"],
        french_hashtags=["#chapeau", "#chapeaucowboy", "#marron", "#western", "#cowgirl"],
    )
    listing = listing_from_fast_draft(draft, "hat")

    formatted = apply_required_listing_format(
        listing,
        {
            "measurements": {
                "Poitrine": "88 cm",
                "Longueur": "32 cm",
                "Largeur": "28 cm",
            }
        },
        "hat",
    )

    assert formatted.fictional_brand == "wildwest"
    assert formatted.english.title.endswith("one size fits all")
    assert formatted.french.title.endswith("taille unique")
    assert "Length: 32 cm" in formatted.english.description
    assert "Width: 28 cm" in formatted.english.description
    assert "88 cm" not in formatted.english.description
    assert "prices are negotiable :)" in formatted.english.description
    assert "prix" in formatted.french.description
    assert "négociable :)" in formatted.french.description
    assert "Extra paragraph should not appear." not in formatted.english.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert formatted.french.description.endswith("Parfait état.")
    assert len(formatted.english.hashtags) == 20
    assert len(formatted.french.hashtags) == 20
    assert len(formatted.french.hashtags) == 20


def test_mask_listing_uses_one_size_and_length_width() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "black mask with sequins, Halloween costume style, one size fits all"
        ),
        french_title=(
            "masque de soirée noir à paillettes, style costume Halloween, taille unique"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=[
            "#blackbag",
            "#clutch",
            "#vinted",
            "#mask",
            "#sequinmask",
        ],
        french_hashtags=["#sac", "#clutch", "#masque", "#y2k", "#tailleunique"],
    )
    listing = listing_from_fast_draft(draft, "mask")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Black sequin masquerade mask",
            "colour": "Black",
            "measurements": {
                "Poitrine": "88 cm",
                "Longueur": "18 cm",
                "Largeur": "14 cm",
            },
        },
        "mask",
    )

    assert formatted.fictional_brand in {
        "boho",
        "elegant",
        "western",
        "cowboy",
        "cowgirl",
        "y2k",
        "streetwear",
        "grunge",
        "wildwest",
        "sophisticated",
    }
    assert formatted.english.title.endswith("one size fits all")
    assert formatted.french.title.endswith("taille unique")
    assert "mask" in formatted.english.title.casefold()
    assert ", style " in formatted.english.title.casefold()
    assert "halloween costume style" not in formatted.english.title.casefold()
    assert "leather" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "Length: 18 cm" in formatted.english.description
    assert "Width: 14 cm" in formatted.english.description
    assert "88 cm" not in formatted.english.description
    assert "XX" not in formatted.english.description
    assert "prices are negotiable :)" in formatted.english.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("bag" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("clutch" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert "#mask" in formatted.english.hashtags or any(
        "mask" in tag.casefold() for tag in formatted.english.hashtags
    )
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_mask_listing_uses_one_size_and_keeps_length_width() -> None:
    listing = local_listing_from_product(
        {
            "sku": "MASK-1",
            "title": "Black sequin masquerade mask",
            "category": "Masks",
            "colour": "Black",
            "measurements": {"Longueur": "18 cm", "Largeur": "14 cm"},
        },
        "mask",
    )

    assert listing.english.title.endswith("one size fits all")
    assert listing.french.title.endswith("taille unique")
    assert "mask" in listing.english.title.casefold()
    assert "masque" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "halloween costume style" not in listing.english.title.casefold()
    assert "18 cm" in listing.english.description
    assert "14 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho",
        "elegant",
        "western",
        "cowboy",
        "cowgirl",
        "y2k",
        "streetwear",
        "grunge",
        "wildwest",
        "sophisticated",
    }


def test_mask_prompts_require_one_size_and_forbid_bag_copy() -> None:
    product = {
        "sku": "MASK-1",
        "title": "Black sequin masquerade mask",
        "category": "Masks",
        "colour": "Black",
        "measurements": {"Longueur": "18 cm", "Largeur": "14 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "mask", None)
    full_prompt = _full_prompt(product, "mask", None)

    for prompt in (fast_prompt, full_prompt):
        assert "one size fits all" in prompt
        assert "Sequin masquerade mask with cutout details" in prompt
        assert "Do not generate an image" in prompt
        assert "sequinmask" in prompt.casefold()
        assert "Halloween costume style" in prompt or "halloween costume style" in prompt.casefold()
    assert "Listing type: MASK" in full_prompt
    assert "18 cm" in fast_prompt


def test_shelf_listing_uses_one_size_and_length_width() -> None:
    draft = FastListingDraft(
        fictional_brand="Avelisse",
        english_title=(
            "Set of 4 simple brown vintage-style wall shelves for storage, one size"
        ),
        french_title=(
            "Lot de 4 etageres murales simples marron vintage-style, taille unique"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#blackbag", "#handbag", "#vinted", "#shelf", "#vintage"],
        french_hashtags=["#sac", "#handbag", "#etagere", "#vintage", "#tailleunique"],
    )
    listing = listing_from_fast_draft(draft, "shelf")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Set of 4 brown wall shelves",
            "colour": "Brown",
            "measurements": {
                "Poitrine": "88 cm",
                "Longueur": "40 cm",
                "Largeur": "12 cm",
            },
        },
        "shelf",
    )

    assert formatted.fictional_brand == "Avelisse"
    assert formatted.english.title.endswith(", one size")
    assert formatted.french.title.endswith(", taille unique")
    assert "shelf" in formatted.english.title.casefold() or "shelves" in formatted.english.title.casefold()
    assert ", style " in formatted.english.title.casefold()
    assert "vintage-style" not in formatted.english.title.casefold()
    assert ", brown," in formatted.english.title.casefold()
    assert "wood" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "Length: 40 cm" in formatted.english.description
    assert "Width: 12 cm" in formatted.english.description
    assert "88 cm" not in formatted.english.description
    assert "XX" not in formatted.english.description
    assert "prices are negotiable :)" in formatted.english.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("bag" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("handbag" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_shelf_title_over_100_characters_is_clipped() -> None:
    tags = [f"#shelf{i}" for i in range(20)]
    listing = ListingPair.model_construct(
        fictional_brand="Orvessa",
        english=ListingVersion.model_construct(
            title=(
                "Set of 4 wall shelves Melting wall clock with roman numerals, "
                "black and white, style surrea, one size"
            ),
            description="Ignore.",
            hashtags=tags,
        ),
        french=ListingVersion.model_construct(
            title=(
                "Lot de 4 etageres murales horloge murale fondante chiffres romains, "
                "noir et blanc, style vintage, taille unique"
            ),
            description="Ignorer.",
            hashtags=tags,
        ),
    )
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Melting wall clock with roman numerals",
            "colour": "Black",
        },
        "shelf",
    )

    assert len(formatted.english.title) <= 100
    assert formatted.english.title.endswith(", one size")
    assert len(formatted.french.title) <= 100
    assert formatted.french.title.endswith(", taille unique")
    ListingVersion(
        title=formatted.english.title,
        description=formatted.english.description,
        hashtags=formatted.english.hashtags,
    )


def test_local_shelf_listing_uses_invented_brand_and_one_size() -> None:
    listing = local_listing_from_product(
        {
            "sku": "SHELF-1",
            "title": "Set of 4 brown vintage wall shelves",
            "category": "Shelves",
            "colour": "Brown",
            "measurements": {"Longueur": "40 cm", "Largeur": "12 cm"},
        },
        "shelf",
    )

    assert listing.english.title.endswith(", one size")
    assert listing.french.title.endswith(", taille unique")
    assert "shelf" in listing.english.title.casefold() or "shelves" in listing.english.title.casefold()
    assert "étagère" in listing.french.title.casefold() or "etagere" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "vintage-style" not in listing.english.title.casefold()
    assert "40 cm" in listing.english.description
    assert "12 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand.casefold() not in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
        "vintage",
    }


def test_shelf_prompts_require_one_size_and_invented_brand() -> None:
    product = {
        "sku": "SHELF-1",
        "title": "Set of 4 brown wall shelves",
        "category": "Shelves",
        "colour": "Brown",
        "measurements": {"Longueur": "40 cm", "Largeur": "12 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "shelf", None)
    full_prompt = _full_prompt(product, "shelf", None)

    for prompt in (fast_prompt, full_prompt):
        assert "one size" in prompt
        assert "Set of 4 wall shelves with simple cut" in prompt
        assert "Do not generate an image" in prompt
        assert "wallshelf" in prompt.casefold()
        assert "vintage-style" in prompt.casefold()
    assert "Listing type: SHELF" in full_prompt
    assert "40 cm" in fast_prompt


def test_beanie_listing_uses_one_size_fits_all_with_verified_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Fuzzy plush striped cat-ear beanie, brown and cream, "
            "style y2k, size 36"
        ),
        french_title=(
            "Bonnet oreilles de chat en peluche raye, marron et creme, "
            "style y2k, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#new", "#beanie", "#cathear"],
        french_hashtags=["#ceinture", "#veste", "#nouveau", "#bonnet", "#oreillesdechat"],
    )
    listing = listing_from_fast_draft(draft, "beanie")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Fuzzy Plush Striped Cat Ear Beanie",
            "colour": "Brown and cream",
            "measurements": {
                "Longueur": "22 cm",
                "Largeur": "18 cm",
            },
        },
        "beanie",
    )

    assert formatted.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "western", "cowboy",
        "cowgirl", "wildwest",
    }
    assert formatted.english.title.endswith("one size fits all")
    assert formatted.french.title.endswith("taille unique")
    assert ", style " in formatted.english.title.casefold()
    assert "beanie" in formatted.english.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "22 cm" in formatted.english.description
    assert "18 cm" in formatted.english.description
    assert "prices are negotiable :)" in formatted.english.description
    assert "price negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("jacket" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_beanie_listing_uses_invented_brand_and_one_size_fits_all() -> None:
    listing = local_listing_from_product(
        {
            "sku": "BEANIE-1",
            "title": "Fuzzy Plush Striped Cat Ear Beanie",
            "category": "Beanies",
            "colour": "Brown and cream",
            "measurements": {"Longueur": "22 cm", "Largeur": "18 cm"},
        },
        "beanie",
    )

    assert listing.english.title.endswith("one size fits all")
    assert listing.french.title.endswith("taille unique")
    assert "beanie" in listing.english.title.casefold()
    assert "bonnet" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "22 cm" in listing.english.description
    assert "18 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "western", "cowboy",
        "cowgirl", "wildwest",
    }


def test_beanie_prompts_require_one_size_fits_all_and_invented_brand() -> None:
    product = {
        "sku": "BEANIE-1",
        "title": "Fuzzy Plush Striped Cat Ear Beanie",
        "category": "Beanies",
        "colour": "Brown and cream",
        "measurements": {"Longueur": "22 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "beanie", None)
    full_prompt = _full_prompt(product, "beanie", None)

    for prompt in (fast_prompt, full_prompt):
        assert "one size fits all" in prompt
        assert "Do not generate an image" in prompt
        assert "prices are negotiable :)" in prompt
    assert "Listing type: BEANIE" in full_prompt
    assert "22 cm" in fast_prompt


def test_shelf_replaces_style_token_brand_with_invented_name() -> None:
    product = {
        "sku": "SHELF-1",
        "title": "Set of 4 brown vintage wall shelves",
        "category": "Shelves",
        "colour": "Brown",
    }
    listing = local_listing_from_product(product, "shelf")
    listing.fictional_brand = "vintage"
    listing = _replace_generic_output_label(listing, product, "shelf")
    assert listing.fictional_brand.casefold() != "vintage"
    assert listing.fictional_brand.casefold() not in {
        "boho",
        "elegant",
        "y2k",
        "chic",
    }


def test_jeans_listing_uses_size_l_and_keeps_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="streetwear",
        english_title=(
            "Baggy black cargo jeans in a streetwear style with an L-size belt"
        ),
        french_title=(
            "Jean baggy noir cargo dans un style streetwear avec une ceinture taille L"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=[
            "#jeans",
            "#baggyjeans",
            "#mensjeans",
            "#sizeL",
            "#streetwear",
            "#womensjeans",
            "#trenchcoat",
            "#lsizebelt",
        ],
        french_hashtags=["#jean", "#jeanbaggy", "#jeanhomme", "#tailleL", "#streetwear"],
    )
    listing = listing_from_fast_draft(draft, "jeans")
    formatted = apply_required_listing_format(
        listing,
        {
            "sku": "JEANS-BLACK-1",
            "colour": "Black",
            "title": "Baggy cargo jeans black",
            "measurements": {
                "Longueur": "108 cm",
                "Tour de taille": "82 cm",
            },
        },
        "jeans",
    )

    assert formatted.fictional_brand in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
    }
    assert formatted.english.title.endswith("size L")
    assert formatted.french.title.endswith("taille L")
    assert formatted.french.title.startswith("Jean ")
    assert ", style " in formatted.english.title.casefold()
    assert "in a " not in formatted.english.title.casefold()
    assert "in an " not in formatted.english.title.casefold()
    assert "l-size belt" not in formatted.english.title.casefold()
    assert "s-size belt" not in formatted.english.title.casefold()
    assert "ceinture" not in formatted.french.title.casefold()
    assert "jean" in formatted.english.title.casefold()
    assert "trench" not in formatted.english.title.casefold()
    assert "coat" not in formatted.english.title.casefold()
    assert "size 40" not in formatted.english.title.casefold()
    assert "denim" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n") == [
        formatted.english.title,
        "Length: 108 cm · Waist: 82 cm",
        "prices are negotiable :)",
        "Perfect condition.",
    ]
    assert formatted.french.description.split("\n\n") == [
        formatted.french.title,
        "Longueur : 108 cm · Tour de taille : 82 cm",
        "prix négociable :)",
        "Parfait état.",
    ]
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert "#baggyjeans" in formatted.english.hashtags
    assert "#sizeL" in formatted.english.hashtags
    assert all("women" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("trench" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("coat" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)


def test_jeans_title_does_not_copy_a_coat_listing() -> None:
    payload = valid_payload()
    payload["english"]["title"] = (
        "Long cream white trench coat in an elegant style with an L-size belt"
    )
    payload["french"]["title"] = (
        "Trench long blanc crème dans un style élégant avec une ceinture taille L"
    )
    listing = validate_gemini_payload(payload)
    formatted = apply_required_listing_format(
        listing,
        {
            "sku": "JEANS-CREAM-2",
            "title": "Baggy cream cargo jeans",
            "colour": "Cream",
            "measurements": {},
        },
        "jeans",
    )

    assert "jean" in formatted.english.title.casefold()
    assert "trench" not in formatted.english.title.casefold()
    assert "coat" not in formatted.english.title.casefold()
    assert formatted.english.title.endswith("size L")
    assert "l-size belt" not in formatted.english.title.casefold()
    assert formatted.french.title.startswith("Jean ")
    assert "manteau" not in formatted.french.title.casefold()
    assert "trench" not in formatted.french.title.casefold()
    assert "ceinture" not in formatted.french.title.casefold()


def test_local_jeans_listing_uses_size_l_without_belt() -> None:
    listing = local_listing_from_product(
        {
            "sku": "JEANS-1",
            "title": "Baggy cargo jeans black",
            "category": "Jeans",
            "colour": "Black",
            "measurements": {"Longueur": "110 cm"},
        },
        "jeans",
    )

    assert listing.english.title.endswith("size L")
    assert listing.french.title.endswith("taille L")
    assert "jean" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "l-size belt" not in listing.english.title.casefold()
    assert "in an" not in listing.english.title.casefold()
    assert "ceinture" not in listing.french.title.casefold()
    assert "trench" not in listing.english.title.casefold()
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert "110 cm" in listing.english.description
    assert listing.fictional_brand == "street"
    assert "#mensjeans" in listing.english.hashtags
    assert "#sizeL" in listing.english.hashtags


def test_local_jeans_listing_always_says_baggy_even_for_wide_leg_source() -> None:
    listing = local_listing_from_product(
        {
            "sku": "JEANS-2",
            "title": "Wide-leg men's jeans with cargo pockets",
            "category": "Jeans",
            "colour": "Black",
            "measurements": {},
        },
        "jeans",
    )

    assert "baggy" in listing.english.title.casefold()
    assert "baggy" in listing.french.title.casefold()
    assert "wide" not in listing.english.title.casefold()
    assert "jambes larges" not in listing.french.title.casefold()
    assert "men" not in listing.english.title.casefold()
    assert "homme" not in listing.french.title.casefold()


def test_jeans_repair_pass_strips_wide_leg_and_mens_and_forces_baggy() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Wide-leg men's jeans with cargo pockets, black, "
            "style streetwear, size 40"
        ),
        french_title=(
            "Jean homme à jambes larges avec poches cargo, noir, "
            "style streetwear, taille 40"
        ),
        english_description="x",
        french_description="x",
        english_hashtags=["#belt", "#vinted", "#new", "#jeans", "#mensjeans", "#cargo"],
        french_hashtags=["#ceinture", "#nouveau", "#jean", "#jeanhomme", "#cargo"],
    )
    formatted = apply_required_listing_format(
        listing_from_fast_draft(draft, "jeans"),
        {"title": "Wide-leg men's jeans", "colour": "Black", "measurements": {}},
        "jeans",
    )

    assert formatted.english.title.endswith("size L")
    assert formatted.french.title.endswith("taille L")
    assert "baggy" in formatted.english.title.casefold()
    assert "baggy" in formatted.french.title.casefold()
    assert "wide" not in formatted.english.title.casefold()
    assert "jambes larges" not in formatted.french.title.casefold()
    assert "men" not in formatted.english.title.casefold()
    assert "homme" not in formatted.french.title.casefold()


def test_jeans_hashtags_vary_by_product_and_skip_other_types() -> None:
    payload = valid_payload()
    listing = validate_gemini_payload(payload)
    black = apply_required_listing_format(
        listing.model_copy(deep=True),
        {"sku": "AAA", "colour": "Black", "title": "Baggy black jeans", "measurements": {}},
        "jeans",
    )
    blue = apply_required_listing_format(
        listing.model_copy(deep=True),
        {"sku": "ZZZ", "colour": "Blue", "title": "Straight blue jeans", "measurements": {}},
        "jeans",
    )

    assert "#blackjeans" in black.english.hashtags
    assert "#bluejeans" in blue.english.hashtags
    assert black.english.hashtags != blue.english.hashtags
    assert all("coat" not in tag.casefold() for tag in black.english.hashtags)
    assert all("dress" not in tag.casefold() for tag in blue.english.hashtags)
    assert all("belt" not in tag.casefold() for tag in black.english.hashtags)


def test_jeans_prompts_require_size_l_and_forbid_belt() -> None:
    product = {
        "sku": "JEANS-1",
        "title": "Baggy black jeans",
        "category": "Jeans",
        "colour": "Black",
        "measurements": {},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "jeans", None)
    full_prompt = _full_prompt(product, "jeans", None)

    for prompt in (fast_prompt, full_prompt):
        assert "style streetwear size L" in prompt
        assert "Baggy jeans with cargo pockets" in prompt
        assert "Size L" in prompt
        assert "Never mention a belt" in prompt or "never mention a belt" in prompt.casefold()
        assert "Do not generate an image" in prompt
        assert "men's" in prompt.casefold() or "mens" in prompt.casefold()
        assert "baggyjeans" in prompt.casefold()
        assert "previous product" in prompt.casefold()
        assert "wide-leg" in prompt.casefold() or "wide leg" in prompt.casefold()
    assert "Listing type: JEANS" in full_prompt


def test_long_boots_listing_uses_size_38_and_includes_length_only() -> None:
    draft = FastListingDraft(
        fictional_brand="Avelisse",
        english_title=(
            "Knee-high heeled sequined boots, cream white, style old money elegant size 38"
        ),
        french_title=(
            "Bottes hautes à talons sequin, blanc crème, style old money elegant taille 38"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=[
            "#boots",
            "#kneehighboots",
            "#longboots",
            "#size38",
            "#oldmoney",
        ],
        french_hashtags=["#bottes", "#botteshautes", "#botteslongues", "#taille38", "#oldmoney"],
    )
    listing = listing_from_fast_draft(draft, "long_boots")
    formatted = apply_required_listing_format(
        listing,
        {
            "measurements": {
                "Longueur": "48 cm",
                "Tour de taille": "82 cm",
            }
        },
        "long_boots",
    )

    assert formatted.fictional_brand == "Avelisse"
    assert formatted.english.title.endswith("size 38")
    assert formatted.french.title.endswith("taille 38")
    assert formatted.french.title.startswith("Bottes")
    assert ", style " in formatted.english.title.casefold()
    assert "size m" not in formatted.english.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert "leather" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n") == [
        formatted.english.title,
        "Length: 48 cm",
        "prices are negotiable :)",
        "Perfect condition.",
    ]
    assert formatted.french.description.split("\n\n") == [
        formatted.french.title,
        "Longueur : 48 cm",
        "prix négociable :)",
        "Parfait état.",
    ]
    assert "48 cm" in formatted.english.description
    assert "82 cm" not in formatted.english.description
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert "#kneehighboots" in formatted.english.hashtags
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)


def test_local_long_boots_listing_keeps_size_38() -> None:
    listing = local_listing_from_product(
        {
            "sku": "BOOTS-1",
            "title": "Knee-high heeled sequin boots cream",
            "category": "Boots",
            "colour": "Cream",
            "measurements": {"Longueur": "48 cm"},
        },
        "long_boots",
    )

    assert listing.english.title.endswith("size 38")
    assert listing.french.title.endswith("taille 38")
    assert "bottes" in listing.french.title.casefold()
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert "48 cm" in listing.english.description
    assert listing.fictional_brand not in {
        "street",
        "elegant",
        "western",
        "y2k",
        "goth",
    }
    assert "#longboots" in listing.english.hashtags


def test_long_boots_prompts_require_size_38_and_forbid_image() -> None:
    product = {
        "sku": "BOOTS-1",
        "title": "Knee-high cream boots",
        "category": "Boots",
        "colour": "Cream",
        "measurements": {},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "long_boots", None)
    full_prompt = _full_prompt(product, "long_boots", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size 38" in prompt
        assert "taille 38" in prompt
        assert "Do not generate an image" in prompt
        assert "kneehighboots" in prompt.casefold()


def test_heels_listing_uses_size_38_and_includes_length_only() -> None:
    draft = FastListingDraft(
        fictional_brand="Avelisse",
        english_title=(
            "Heeled sandals with sequins, cream white, style old money size 38"
        ),
        french_title=(
            "Sandales à talons avec sequins, blanc crème, style old money taille 38"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=[
            "#heels",
            "#heeledsandals",
            "#sandals",
            "#size38",
            "#oldmoney",
        ],
        french_hashtags=["#talons", "#sandalesatalons", "#sandales", "#taille38", "#oldmoney"],
    )
    listing = listing_from_fast_draft(draft, "heels")
    formatted = apply_required_listing_format(
        listing,
        {
            "measurements": {
                "Longueur": "12 cm",
                "Tour de taille": "82 cm",
            }
        },
        "heels",
    )

    assert formatted.fictional_brand == "Avelisse"
    assert formatted.english.title.endswith("size 38")
    assert formatted.french.title.endswith("taille 38")
    assert formatted.french.title.startswith("Sandales")
    assert ", style " in formatted.english.title.casefold()
    assert "size m" not in formatted.english.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert "leather" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n") == [
        formatted.english.title,
        "Length: 12 cm",
        "prices are negotiable :)",
        "Perfect condition.",
    ]
    assert formatted.french.description.split("\n\n") == [
        formatted.french.title,
        "Longueur : 12 cm",
        "prix négociable :)",
        "Parfait état.",
    ]
    assert "12 cm" in formatted.english.description
    assert "82 cm" not in formatted.english.description
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert "#heeledsandals" in formatted.english.hashtags
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("france" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("new" not in tag.casefold() for tag in formatted.english.hashtags)


def test_local_heels_listing_keeps_size_38() -> None:
    listing = local_listing_from_product(
        {
            "sku": "HEELS-1",
            "title": "Cream heeled sequin sandals",
            "category": "Sandals",
            "colour": "Cream",
            "measurements": {"Longueur": "12 cm"},
        },
        "heels",
    )

    assert listing.english.title.endswith("size 38")
    assert listing.french.title.endswith("taille 38")
    assert "sandales" in listing.french.title.casefold()
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert "12 cm" in listing.english.description
    assert listing.fictional_brand not in {
        "street",
        "elegant",
        "western",
        "y2k",
        "goth",
        "oldmoney",
    }
    assert "#heeledsandals" in listing.english.hashtags


def test_heels_prompts_require_size_38_and_forbid_image() -> None:
    product = {
        "sku": "HEELS-1",
        "title": "Cream heeled sandals",
        "category": "Sandals",
        "colour": "Cream",
        "measurements": {},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "heels", None)
    full_prompt = _full_prompt(product, "heels", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size 38" in prompt
        assert "taille 38" in prompt
        assert "Heeled sandals with sequins, cream white, style old money size 38" in prompt
        assert "Do not generate an image" in prompt
        assert "heeledsandals" in prompt.casefold()


def test_coat_listing_uses_size_s_and_keeps_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Long cream white trench coat in an elegant style with an S-size belt"
        ),
        french_title=(
            "Trench long blanc crème dans un style élégant avec une ceinture taille S"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#coat", "#trenchcoat", "#sizeS", "#womenscoat", "#oldmoney", "#belt"],
        french_hashtags=["#manteau", "#trench", "#tailleS", "#manteaufemme", "#oldmoney"],
    )
    listing = listing_from_fast_draft(draft, "coat")
    formatted = apply_required_listing_format(
        listing,
        {
            "measurements": {
                "Longueur": "110 cm",
                "Longueur de manche": "62 cm",
                "Tour de taille": "88 cm",
            }
        },
        "coat",
    )

    assert formatted.fictional_brand in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
    }
    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.english.title.casefold()
    assert "in an elegant style" not in formatted.english.title.casefold()
    assert "s-size belt" not in formatted.english.title.casefold()
    assert "ceinture" not in formatted.french.title.casefold()
    assert "size 38" not in formatted.english.title.casefold()
    assert "size m" not in formatted.english.title.casefold()
    assert "wool" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "110 cm" in formatted.english.description
    assert "62 cm" in formatted.english.description
    assert "88 cm" in formatted.english.description
    assert "prices are negotiable :)" in formatted.english.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert "#sizeS" in formatted.english.hashtags


def test_local_coat_listing_uses_size_s_without_belt() -> None:
    listing = local_listing_from_product(
        {
            "sku": "COAT-1",
            "title": "Long cream trench coat with belt",
            "category": "Coats",
            "colour": "Cream",
            "measurements": {"Longueur": "110 cm"},
        },
        "coat",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert "coat" in listing.english.title.casefold() or "trench" in listing.english.title.casefold()
    assert "manteau" in listing.french.title.casefold() or "trench" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "s-size belt" not in listing.english.title.casefold()
    assert "in an" not in listing.english.title.casefold()
    assert "ceinture" not in listing.french.title.casefold()
    assert "110 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
    }


def test_coat_prompts_require_size_s_and_forbid_belt() -> None:
    product = {
        "sku": "COAT-1",
        "title": "Long cream trench coat",
        "category": "Coats",
        "colour": "Cream",
        "measurements": {"Longueur": "110 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "coat", None)
    full_prompt = _full_prompt(product, "coat", None)

    for prompt in (fast_prompt, full_prompt):
        assert "style elegant size S" in prompt
        assert "Long cream white trench coat with wrap details" in prompt
        assert "Do not generate an image" in prompt
        assert "trenchcoat" in prompt.casefold()
        assert "Never mention a belt" in prompt or "Never mention a belt." in prompt or "never mention a belt" in prompt.casefold()
    assert "Listing type: COAT" in full_prompt


def test_jacket_listing_uses_size_s_and_length_only() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Brown patterned faux leather biker jacket with zipper details, Y2K style, size S"
        ),
        french_title=(
            "Veste similicuir à motif avec zip et col, marron, style Y2K, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#cape", "#poncho", "#vinted", "#size36", "#jacket", "#y2k"],
        french_hashtags=["#cape", "#veste", "#taille36", "#y2k", "#blouson"],
    )
    listing = listing_from_fast_draft(draft, "jacket")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Brown patterned faux leather biker jacket",
            "colour": "Brown",
            "measurements": {
                "Longueur": "62 cm",
                "Longueur de manche": "59 cm",
                "Tour de taille": "86 cm",
            },
        },
        "jacket",
    )

    assert formatted.fictional_brand in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
    }
    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.english.title.casefold()
    assert "jacket" in formatted.english.title.casefold()
    assert "y2k style" not in formatted.english.title.casefold()
    assert "in an" not in formatted.english.title.casefold()
    assert "s-size belt" not in formatted.english.title.casefold()
    assert "ceinture" not in formatted.french.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert "leather" not in formatted.english.title.casefold()
    assert "similicuir" not in formatted.french.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "62 cm" in formatted.english.description
    assert "59 cm" not in formatted.english.description
    assert "86 cm" not in formatted.english.description
    assert "prices are negotiable :)" in formatted.english.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("cape" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("poncho" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert "#sizeS" in formatted.english.hashtags
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_jacket_listing_uses_size_s_and_includes_measurements() -> None:
    listing = local_listing_from_product(
        {
            "sku": "JACKET-1",
            "title": "Brown patterned faux leather biker jacket",
            "category": "Jackets",
            "colour": "Brown",
            "measurements": {"Longueur": "62 cm"},
        },
        "jacket",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert "jacket" in listing.english.title.casefold()
    assert "veste" in listing.french.title.casefold() or "blouson" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "leather" not in listing.english.title.casefold()
    assert "s-size belt" not in listing.english.title.casefold()
    assert "in an" not in listing.english.title.casefold()
    assert "ceinture" not in listing.french.title.casefold()
    assert "62 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
    }


def test_jacket_prompts_require_size_s_and_forbid_measurements() -> None:
    product = {
        "sku": "JACKET-1",
        "title": "Brown patterned biker jacket",
        "category": "Jackets",
        "colour": "Brown",
        "measurements": {"Longueur": "62 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "jacket", None)
    full_prompt = _full_prompt(product, "jacket", None)

    for prompt in (fast_prompt, full_prompt):
        assert "style y2k size S" in prompt
        assert "Patterned biker jacket with zipper details" in prompt
        assert "Do not generate an image" in prompt
        assert "bikerjacket" in prompt.casefold()
        assert "Never mention a belt" in prompt or "never mention a belt" in prompt.casefold()
    assert "Listing type: JACKET" in full_prompt
    assert '"measurements": {}' in fast_prompt


def test_organizer_listing_uses_size_s_and_includes_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "3-Tier metal countertop organizer with mesh baskets, black, "
            "style elegant, size S"
        ),
        french_title=(
            "Organiseur de comptoir en metal a 3 niveaux avec paniers, "
            "noir, style elegant, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#size36", "#kitchenorganizer", "#y2k"],
        french_hashtags=["#ceinture", "#veste", "#taille36", "#y2k", "#organiseur"],
    )
    listing = listing_from_fast_draft(draft, "organizer")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "3-Tier Metal Kitchen Countertop Organizer",
            "colour": "Black",
            "measurements": {
                "Longueur": "62 cm",
                "Largeur": "30 cm",
            },
        },
        "organizer",
    )

    assert formatted.fictional_brand in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
    }
    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.english.title.casefold()
    assert "organizer" in formatted.english.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert "metal" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "62 cm" in formatted.english.description
    assert "30 cm" in formatted.english.description
    assert "prices are negotiable :)" in formatted.english.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("jacket" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_organizer_listing_uses_size_s_and_includes_measurements() -> None:
    listing = local_listing_from_product(
        {
            "sku": "ORGANIZER-1",
            "title": "3-Tier Metal Kitchen Countertop Organizer",
            "category": "Kitchen organizers",
            "colour": "Black",
            "measurements": {"Longueur": "62 cm"},
        },
        "organizer",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert "organizer" in listing.english.title.casefold()
    assert "organiseur" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "62 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho",
        "bohemian",
        "chic",
        "elegant",
        "oldmoney",
        "y2k",
        "goth",
        "street",
        "grunge",
        "dark",
    }


def test_organizer_prompts_require_size_s_and_forbid_measurements() -> None:
    product = {
        "sku": "ORGANIZER-1",
        "title": "3-Tier Metal Kitchen Countertop Organizer",
        "category": "Kitchen organizers",
        "colour": "Black",
        "measurements": {"Longueur": "62 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "organizer", None)
    full_prompt = _full_prompt(product, "organizer", None)

    for prompt in (fast_prompt, full_prompt):
        assert "style elegant size S" in prompt
        assert "Do not generate an image" in prompt
        assert "prices are negotiable :)" in prompt
    assert "Listing type: ORGANIZER" in full_prompt
    assert '"measurements": {}' in fast_prompt


def test_non_length_width_dimension_labels_still_reach_the_description() -> None:
    # Regression test: the extraction-from-photo feature can invent labels
    # like "Hauteur" or "Diamètre" for items whose natural dimension isn't
    # length/width (lamps, chandeliers, round mirrors). These used to be
    # silently dropped because the filter only allow-listed labels
    # containing "longueur"/"largeur"/"length"/"width".
    for listing_type in ("lamp", "chandelier", "shelf", "carpet", "cushion", "necktie", "mask", "hat", "earrings", "bag"):
        listing = local_listing_from_product(
            {
                "sku": f"DIM-{listing_type}",
                "title": f"Test {listing_type}",
                "category": listing_type,
                "colour": "Gold",
                "measurements": {"Hauteur": "45 cm", "Diamètre": "60 cm"},
            },
            listing_type,
        )
        assert "45 cm" in listing.english.description, listing_type
        assert "60 cm" in listing.english.description, listing_type


def test_body_measurements_are_still_excluded_from_home_decor_descriptions() -> None:
    listing = local_listing_from_product(
        {
            "sku": "MASK-1",
            "title": "Sequin masquerade mask",
            "category": "mask",
            "colour": "Gold",
            "measurements": {"Poitrine": "90 cm", "Longueur": "20 cm"},
        },
        "mask",
    )
    assert "90 cm" not in listing.english.description
    assert "20 cm" in listing.english.description


def test_lamp_listing_uses_one_size_fits_all_with_verified_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Table lamp with a twisted 3D-printed design, white and natural "
            "wood, style modern, one size"
        ),
        french_title=(
            "Lampe de table avec design torsade imprime en 3D, blanc et "
            "bois naturel, style modern, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#new", "#tablelamp", "#modernlamp"],
        french_hashtags=["#ceinture", "#veste", "#nouveau", "#lampedetable", "#lampemoderne"],
    )
    listing = listing_from_fast_draft(draft, "lamp")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Table Lamp 3D Printed Twisted Design",
            "colour": "White",
            "measurements": {
                "Longueur": "35 cm",
                "Largeur": "15 cm",
            },
        },
        "lamp",
    )

    assert formatted.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "decorative", "modern",
        "minimalist", "vintage", "industrial", "scandinavian", "artdeco",
    }
    assert formatted.english.title.endswith("one size fits all")
    assert formatted.french.title.endswith("taille unique")
    assert ", style " in formatted.english.title.casefold()
    assert "lamp" in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "Length: 35 cm" in formatted.english.description
    assert "Width: 15 cm" in formatted.english.description
    assert "price negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("jacket" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("new" != tag.casefold().lstrip("#") for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_lamp_listing_uses_one_size_fits_all_and_invented_brand() -> None:
    listing = local_listing_from_product(
        {
            "sku": "LAMP-1",
            "title": "Table Lamp 3D Printed Twisted Design",
            "category": "Table lamps",
            "colour": "White",
            "measurements": {"Longueur": "35 cm", "Largeur": "15 cm"},
        },
        "lamp",
    )

    assert listing.english.title.endswith("one size fits all")
    assert listing.french.title.endswith("taille unique")
    assert "lamp" in listing.english.title.casefold()
    assert "lampe" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "Length: 35 cm" in listing.english.description
    assert "Width: 15 cm" in listing.english.description
    assert "price negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "decorative", "modern",
        "minimalist", "vintage", "industrial", "scandinavian", "artdeco",
    }


def test_lamp_prompts_require_one_size_fits_all_and_price_negotiable() -> None:
    product = {
        "sku": "LAMP-1",
        "title": "Table Lamp 3D Printed Twisted Design",
        "category": "Table lamps",
        "colour": "White",
        "measurements": {"Longueur": "35 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "lamp", None)
    full_prompt = _full_prompt(product, "lamp", None)

    for prompt in (fast_prompt, full_prompt):
        assert "one size fits all" in prompt
        assert "Do not generate an image" in prompt
        assert "price negotiable :)" in prompt
    assert "Listing type: LAMP" in full_prompt


def test_chandelier_listing_uses_one_size_fits_all_with_verified_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Multi-tiered gold metal and crystal ceiling light, gold, "
            "decorative style"
        ),
        french_title=(
            "Lustre a plusieurs niveaux en metal dore et cristal, dore, "
            "style decorative"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#new", "#chandelier", "#crystaldecor"],
        french_hashtags=["#ceinture", "#veste", "#nouveau", "#lustre", "#decocristal"],
    )
    listing = listing_from_fast_draft(draft, "chandelier")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Multi-Tiered Gold Crystal Chandelier",
            "colour": "Gold",
            "measurements": {
                "Longueur": "60 cm",
                "Largeur": "40 cm",
            },
        },
        "chandelier",
    )

    assert formatted.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "decorative", "modern",
        "minimalist", "vintage", "industrial", "scandinavian", "artdeco",
    }
    assert formatted.english.title.endswith("one size fits all")
    assert formatted.french.title.endswith("taille unique")
    assert ", style " in formatted.english.title.casefold()
    assert any(
        word in formatted.english.title.casefold()
        for word in ("chandelier", "pendant light", "ceiling light")
    )
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "Length: 60 cm" in formatted.english.description
    assert "Width: 40 cm" in formatted.english.description
    assert "price negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("jacket" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_chandelier_listing_uses_one_size_fits_all_and_invented_brand() -> None:
    listing = local_listing_from_product(
        {
            "sku": "CHANDELIER-1",
            "title": "Multi-Tiered Gold Crystal Chandelier",
            "category": "Chandeliers",
            "colour": "Gold",
            "measurements": {"Longueur": "60 cm", "Largeur": "40 cm"},
        },
        "chandelier",
    )

    assert listing.english.title.endswith("one size fits all")
    assert listing.french.title.endswith("taille unique")
    assert any(
        word in listing.english.title.casefold()
        for word in ("chandelier", "pendant light", "ceiling light")
    )
    assert "lustre" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "Length: 60 cm" in listing.english.description
    assert "Width: 40 cm" in listing.english.description
    assert "price negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "decorative", "modern",
        "minimalist", "vintage", "industrial", "scandinavian", "artdeco",
    }


def test_chandelier_prompts_require_one_size_fits_all_and_price_negotiable() -> None:
    product = {
        "sku": "CHANDELIER-1",
        "title": "Multi-Tiered Gold Crystal Chandelier",
        "category": "Chandeliers",
        "colour": "Gold",
        "measurements": {"Longueur": "60 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "chandelier", None)
    full_prompt = _full_prompt(product, "chandelier", None)

    for prompt in (fast_prompt, full_prompt):
        assert "one size fits all" in prompt
        assert "Do not generate an image" in prompt
        assert "price negotiable :)" in prompt
    assert "Listing type: CHANDELIER" in full_prompt


def test_carpet_listing_uses_one_size_fits_all_with_verified_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Light gray cat play tower with a geometric pattern, "
            "style elegant, one size"
        ),
        french_title=(
            "Tour a chats grise avec motif geometrique, style elegant, "
            "taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#blackbag", "#handbag", "#vinted", "#new", "#arearug", "#geometricrug"],
        french_hashtags=["#sacnoir", "#nouveau", "#tapisgeometrique", "#tapisdeco", "#decosalon"],
    )
    listing = listing_from_fast_draft(draft, "carpet")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Geometric Area Rug",
            "colour": "Light gray",
            "measurements": {
                "Longueur": "160 cm",
                "Largeur": "230 cm",
            },
        },
        "carpet",
    )

    assert formatted.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "decorative", "modern",
        "minimalist", "vintage", "industrial", "scandinavian", "artdeco",
    }
    assert formatted.english.title.endswith("one size fits all")
    assert formatted.french.title.endswith("taille unique")
    assert ", style " in formatted.english.title.casefold()
    assert any(
        word in formatted.english.title.casefold() for word in ("carpet", "rug")
    )
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "Length: 160 cm" in formatted.english.description
    assert "Width: 230 cm" in formatted.english.description
    assert "price negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("bag" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_carpet_listing_uses_one_size_fits_all_and_invented_brand() -> None:
    listing = local_listing_from_product(
        {
            "sku": "CARPET-1",
            "title": "Geometric Area Rug",
            "category": "Rugs",
            "colour": "Light gray",
            "measurements": {"Longueur": "160 cm", "Largeur": "230 cm"},
        },
        "carpet",
    )

    assert listing.english.title.endswith("one size fits all")
    assert listing.french.title.endswith("taille unique")
    assert any(word in listing.english.title.casefold() for word in ("carpet", "rug"))
    assert "tapis" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "Length: 160 cm" in listing.english.description
    assert "Width: 230 cm" in listing.english.description
    assert "price negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "decorative", "modern",
        "minimalist", "vintage", "industrial", "scandinavian", "artdeco",
    }


def test_carpet_prompts_require_one_size_fits_all_and_price_negotiable() -> None:
    product = {
        "sku": "CARPET-1",
        "title": "Geometric Area Rug",
        "category": "Rugs",
        "colour": "Light gray",
        "measurements": {"Longueur": "160 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "carpet", None)
    full_prompt = _full_prompt(product, "carpet", None)

    for prompt in (fast_prompt, full_prompt):
        assert "one size fits all" in prompt
        assert "Do not generate an image" in prompt
        assert "price negotiable :)" in prompt
    assert "Listing type: CARPET" in full_prompt


def test_cushion_listing_uses_one_size_fits_all_with_verified_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Ivory decorative handbag with a textured woven finish, "
            "style elegant, one size"
        ),
        french_title=(
            "Sac ivoire decoratif a texture tissee travaillee, style elegant, "
            "taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#blackbag", "#handbag", "#vinted", "#new", "#throwpillow", "#decorativestyle"],
        french_hashtags=["#sacnoir", "#nouveau", "#coussindecoratif", "#coussin", "#decosalon"],
    )
    listing = listing_from_fast_draft(draft, "cushion")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Decorative Cushion",
            "colour": "Ivory",
            "measurements": {
                "Longueur": "45 cm",
                "Largeur": "45 cm",
            },
        },
        "cushion",
    )

    assert formatted.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "decorative", "modern",
        "minimalist", "vintage", "industrial", "scandinavian", "artdeco",
    }
    assert formatted.english.title.endswith("one size fits all")
    assert formatted.french.title.endswith("taille unique")
    assert ", style " in formatted.english.title.casefold()
    assert any(
        word in formatted.english.title.casefold() for word in ("cushion", "pillow")
    )
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "Length: 45 cm" in formatted.english.description
    assert "Width: 45 cm" in formatted.english.description
    assert "price negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("bag" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_cushion_listing_uses_one_size_fits_all_and_invented_brand() -> None:
    listing = local_listing_from_product(
        {
            "sku": "CUSHION-1",
            "title": "Decorative Cushion",
            "category": "Cushions",
            "colour": "Ivory",
            "measurements": {"Longueur": "45 cm", "Largeur": "45 cm"},
        },
        "cushion",
    )

    assert listing.english.title.endswith("one size fits all")
    assert listing.french.title.endswith("taille unique")
    assert any(word in listing.english.title.casefold() for word in ("cushion", "pillow"))
    assert "coussin" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "Length: 45 cm" in listing.english.description
    assert "Width: 45 cm" in listing.english.description
    assert "price negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "old money", "oldmoney", "y2k",
        "gothic", "goth", "streetwear", "street", "grunge",
        "dark academia", "dark", "sophisticated", "decorative", "modern",
        "minimalist", "vintage", "industrial", "scandinavian", "artdeco",
    }


def test_cushion_prompts_require_one_size_fits_all_and_price_negotiable() -> None:
    product = {
        "sku": "CUSHION-1",
        "title": "Decorative Cushion",
        "category": "Cushions",
        "colour": "Ivory",
        "measurements": {"Longueur": "45 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "cushion", None)
    full_prompt = _full_prompt(product, "cushion", None)

    for prompt in (fast_prompt, full_prompt):
        assert "one size fits all" in prompt
        assert "Do not generate an image" in prompt
        assert "price negotiable :)" in prompt
    assert "Listing type: CUSHION" in full_prompt


def test_mirror_listing_uses_size_s_style_token_and_price_negotiable() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Asymmetrical organic-shaped wood wall mirror, natural, style "
            "boho, size 36"
        ),
        french_title=(
            "Miroir mural asymetrique en bois de forme organique, naturel, "
            "style boho, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#new", "#organicmirror", "#bohochic"],
        french_hashtags=["#ceinture", "#veste", "#nouveau", "#miroirunique", "#bohochic"],
    )
    listing = listing_from_fast_draft(draft, "mirror")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Asymmetrical Organic Shaped Wall Mirror",
            "colour": "Natural",
            "measurements": {"Longueur": "60 cm"},
        },
        "mirror",
    )

    assert formatted.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }
    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.english.title.casefold()
    assert "mirror" in formatted.english.title.casefold()
    assert "wood" not in formatted.english.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "60 cm" in formatted.english.description
    assert "price negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("jacket" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_mirror_listing_uses_size_s_and_style_token() -> None:
    listing = local_listing_from_product(
        {
            "sku": "MIRROR-1",
            "title": "Asymmetrical Organic Shaped Wall Mirror",
            "category": "Wall mirrors",
            "colour": "Natural",
            "measurements": {"Longueur": "60 cm"},
        },
        "mirror",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert "mirror" in listing.english.title.casefold()
    assert "miroir" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "60 cm" in listing.english.description
    assert "price negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }


def test_mirror_prompts_require_size_s_and_price_negotiable() -> None:
    product = {
        "sku": "MIRROR-1",
        "title": "Asymmetrical Organic Shaped Wall Mirror",
        "category": "Wall mirrors",
        "colour": "Natural",
        "measurements": {"Longueur": "60 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "mirror", None)
    full_prompt = _full_prompt(product, "mirror", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size S" in prompt
        assert "Do not generate an image" in prompt
        assert "price negotiable :)" in prompt
    assert "Listing type: MIRROR" in full_prompt
    assert '"measurements": {}' in fast_prompt


def test_sculpture_listing_uses_size_s_style_token_and_price_negotiable() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Delicate brown resin Justice sculpture with carved details, "
            "style elegant, size 36"
        ),
        french_title=(
            "Sculpture Justice delicate en resine marron aux details "
            "sculptes, style elegant, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#cape", "#poncho", "#fauxfur", "#vinted", "#new", "#sculpturedecor"],
        french_hashtags=["#cape", "#fourrure", "#nouveau", "#sculpturedeco", "#objetdart"],
    )
    listing = listing_from_fast_draft(draft, "sculpture")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Delicate Justice Sculpture",
            "colour": "Brown",
            "measurements": {"Longueur": "30 cm"},
        },
        "sculpture",
    )

    assert formatted.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }
    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.english.title.casefold()
    assert "sculpture" in formatted.english.title.casefold()
    assert "resin" not in formatted.english.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "30 cm" in formatted.english.description
    assert "price negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("cape" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("fur" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_sculpture_listing_uses_size_s_and_style_token() -> None:
    listing = local_listing_from_product(
        {
            "sku": "SCULPTURE-1",
            "title": "Delicate Justice Sculpture",
            "category": "Sculptures",
            "colour": "Brown",
            "measurements": {"Longueur": "30 cm"},
        },
        "sculpture",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert "sculpture" in listing.english.title.casefold()
    assert "sculpture" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "30 cm" in listing.english.description
    assert "price negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }


def test_sculpture_prompts_require_size_s_and_price_negotiable() -> None:
    product = {
        "sku": "SCULPTURE-1",
        "title": "Delicate Justice Sculpture",
        "category": "Sculptures",
        "colour": "Brown",
        "measurements": {"Longueur": "30 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "sculpture", None)
    full_prompt = _full_prompt(product, "sculpture", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size S" in prompt
        assert "Do not generate an image" in prompt
        assert "price negotiable :)" in prompt
    assert "Listing type: SCULPTURE" in full_prompt
    assert '"measurements": {}' in fast_prompt


def test_curtain_listing_uses_size_s_style_token_and_price_are_negotiable() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Black floral lace scalloped-edge sheer linen curtain, "
            "style gothic, size 36"
        ),
        french_title=(
            "Rideau voile en lin dentelle florale a bordure festonnee, noir, "
            "style gothic, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#new", "#blacklace", "#gothicstyle"],
        french_hashtags=["#ceinture", "#veste", "#nouveau", "#dentellenoire", "#stylegothique"],
    )
    listing = listing_from_fast_draft(draft, "curtain")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Black Floral Lace Scalloped Edge Sheer Curtain",
            "colour": "Black",
            "measurements": {"Longueur": "150 cm", "Largeur": "100 cm"},
        },
        "curtain",
    )

    assert formatted.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }
    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.english.title.casefold()
    assert any(
        word in formatted.english.title.casefold() for word in ("curtain", "drape")
    )
    assert "linen" not in formatted.english.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "150 cm" in formatted.english.description
    assert "100 cm" in formatted.english.description
    assert "price are negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("jacket" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_curtain_listing_uses_size_s_and_style_token() -> None:
    listing = local_listing_from_product(
        {
            "sku": "CURTAIN-1",
            "title": "Black Floral Lace Scalloped Edge Sheer Curtain",
            "category": "Curtains",
            "colour": "Black",
            "measurements": {"Longueur": "150 cm", "Largeur": "100 cm"},
        },
        "curtain",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert any(word in listing.english.title.casefold() for word in ("curtain", "drape"))
    assert "rideau" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "150 cm" in listing.english.description
    assert "price are negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }


def test_curtain_prompts_require_size_s_and_price_are_negotiable() -> None:
    product = {
        "sku": "CURTAIN-1",
        "title": "Black Floral Lace Scalloped Edge Sheer Curtain",
        "category": "Curtains",
        "colour": "Black",
        "measurements": {"Longueur": "150 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "curtain", None)
    full_prompt = _full_prompt(product, "curtain", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size S" in prompt
        assert "Do not generate an image" in prompt
        assert "price are negotiable :)" in prompt
    assert "Listing type: CURTAIN" in full_prompt
    assert '"measurements": {}' in fast_prompt


def test_necktie_listing_has_no_size_marker_and_keeps_measurements() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Dark green paisley-patterned silk necktie with a slim cut, "
            "dark green, style elegant"
        ),
        french_title=(
            "Cravate en soie a motifs cachemire a coupe fine, vert fonce, "
            "style elegant"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#new", "#necktie", "#paisleytie"],
        french_hashtags=["#ceinture", "#veste", "#nouveau", "#cravate", "#cachemire"],
    )
    listing = listing_from_fast_draft(draft, "necktie")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Dark Green Paisley Necktie",
            "colour": "Dark green",
            "measurements": {"Longueur": "145 cm", "Largeur": "8 cm"},
        },
        "necktie",
    )

    assert formatted.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "western", "cowboy", "y2k", "gothic",
        "goth", "streetwear", "street", "grunge", "wildwest", "cowgirl",
        "sophisticated",
    }
    assert ", style " in formatted.english.title.casefold()
    assert any(
        word in formatted.english.title.casefold() for word in ("necktie", "tie")
    )
    assert "silk" not in formatted.english.title.casefold()
    assert not re.search(r"\bsize\s+[sml]\b", formatted.english.title.casefold())
    assert "one size" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "Length: 145 cm" in formatted.english.description
    assert "Width: 8 cm" in formatted.english.description
    assert "price are negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("jacket" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_necktie_listing_has_no_size_and_invented_brand() -> None:
    listing = local_listing_from_product(
        {
            "sku": "NECKTIE-1",
            "title": "Dark Green Paisley Necktie",
            "category": "Neckties",
            "colour": "Dark green",
            "measurements": {"Longueur": "145 cm", "Largeur": "8 cm"},
        },
        "necktie",
    )

    assert any(word in listing.english.title.casefold() for word in ("necktie", "tie"))
    assert "cravate" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert not re.search(r"\bsize\s+[sml]\b", listing.english.title.casefold())
    assert "Length: 145 cm" in listing.english.description
    assert "Width: 8 cm" in listing.english.description
    assert "price are negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand.casefold() not in {
        "boho", "bohemian", "elegant", "western", "cowboy", "y2k", "gothic",
        "goth", "streetwear", "street", "grunge", "wildwest", "cowgirl",
        "sophisticated",
    }


def test_necktie_prompts_require_no_size_and_price_are_negotiable() -> None:
    product = {
        "sku": "NECKTIE-1",
        "title": "Dark Green Paisley Necktie",
        "category": "Neckties",
        "colour": "Dark green",
        "measurements": {"Longueur": "145 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "necktie", None)
    full_prompt = _full_prompt(product, "necktie", None)

    for prompt in (fast_prompt, full_prompt):
        assert "Do not generate an image" in prompt
        assert "price are negotiable :)" in prompt
    assert "Listing type: NECKTIE" in full_prompt
    assert '"Longueur": "145 cm"' in fast_prompt


def test_leg_warmer_listing_uses_size_s_style_token_and_price_negotiable() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Fluffy faux fur leg warmers with a cropped cut, rich brown, "
            "style boho, size 36"
        ),
        french_title=(
            "Jambieres en fausse fourrure moelleuse et courte, marron riche, "
            "style boho, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#new", "#fauxfur", "#legwarmers"],
        french_hashtags=["#ceinture", "#veste", "#nouveau", "#jambieres", "#faussefourrure"],
    )
    listing = listing_from_fast_draft(draft, "leg_warmer")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Fluffy Faux Fur Leg Warmers",
            "colour": "Rich brown",
            "measurements": {"Longueur": "40 cm"},
        },
        "leg_warmer",
    )

    assert formatted.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }
    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.english.title.casefold()
    assert "leg warmer" in formatted.english.title.casefold()
    assert "fur" not in formatted.english.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "40 cm" in formatted.english.description
    assert "prices are negotiable :)" in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_leg_warmer_listing_uses_size_s_and_style_token() -> None:
    listing = local_listing_from_product(
        {
            "sku": "LEGWARMER-1",
            "title": "Fluffy Faux Fur Leg Warmers",
            "category": "Leg warmers",
            "colour": "Rich brown",
            "measurements": {"Longueur": "40 cm"},
        },
        "leg_warmer",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert "leg warmer" in listing.english.title.casefold()
    assert "jambière" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "40 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }


def test_leg_warmer_prompts_require_size_s_and_prices_negotiable() -> None:
    product = {
        "sku": "LEGWARMER-1",
        "title": "Fluffy Faux Fur Leg Warmers",
        "category": "Leg warmers",
        "colour": "Rich brown",
        "measurements": {"Longueur": "40 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "leg_warmer", None)
    full_prompt = _full_prompt(product, "leg_warmer", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size S" in prompt
        assert "Do not generate an image" in prompt
        assert "prices are negotiable :)" in prompt
    assert "Listing type: LEG_WARMER" in full_prompt
    assert '"measurements": {}' in fast_prompt


def test_jewelry_box_listing_uses_size_l_style_token_and_price_are_negotiable() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Large jewelry box with multiple leather storage tiers, pink, "
            "style chic, size 40"
        ),
        french_title=(
            "Grand coffret a bijoux en cuir a plusieurs niveaux, rose, "
            "style chic, taille 40"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#new", "#jewelrybox", "#forher"],
        french_hashtags=["#ceinture", "#veste", "#nouveau", "#coffretabijoux", "#pourelle"],
    )
    listing = listing_from_fast_draft(draft, "jewelry_box")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Large Pink Jewelry Box",
            "colour": "Pink",
            "measurements": {"Longueur": "25 cm"},
        },
        "jewelry_box",
    )

    assert formatted.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }
    assert formatted.english.title.endswith("size L")
    assert formatted.french.title.endswith("taille L")
    assert ", style " in formatted.english.title.casefold()
    assert "jewelry box" in formatted.english.title.casefold()
    assert "leather" not in formatted.english.title.casefold()
    assert "size 40" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "25 cm" in formatted.english.description
    assert "price are negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("forher" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("pourelle" not in tag.casefold() for tag in formatted.french.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_jewelry_box_listing_uses_size_l_and_style_token() -> None:
    listing = local_listing_from_product(
        {
            "sku": "JEWELRYBOX-1",
            "title": "Large Pink Jewelry Box",
            "category": "Jewelry boxes",
            "colour": "Pink",
            "measurements": {"Longueur": "25 cm"},
        },
        "jewelry_box",
    )

    assert listing.english.title.endswith("size L")
    assert listing.french.title.endswith("taille L")
    assert "jewelry box" in listing.english.title.casefold()
    assert "coffret" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "25 cm" in listing.english.description
    assert "price are negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }


def test_jewelry_box_prompts_require_size_l_and_price_are_negotiable() -> None:
    product = {
        "sku": "JEWELRYBOX-1",
        "title": "Large Pink Jewelry Box",
        "category": "Jewelry boxes",
        "colour": "Pink",
        "measurements": {"Longueur": "25 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "jewelry_box", None)
    full_prompt = _full_prompt(product, "jewelry_box", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size L" in prompt
        assert "Do not generate an image" in prompt
        assert "price are negotiable :)" in prompt
    assert "Listing type: JEWELRY_BOX" in full_prompt
    assert '"measurements": {}' in fast_prompt


def test_lace_umbrella_listing_uses_size_s_style_token_and_prices_negotiable() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Elegant bohemian-style lace parasol with a ruffled trim, "
            "white, style elegant, size 36"
        ),
        french_title=(
            "Ombrelle en dentelle elegante de style boheme a volants, "
            "blanc, style elegant, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#belt", "#jacket", "#vinted", "#new", "#lace", "#parasol"],
        french_hashtags=["#ceinture", "#veste", "#nouveau", "#dentelle", "#ombrelle"],
    )
    listing = listing_from_fast_draft(draft, "lace_umbrella")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Elegant Bohemian Lace Parasol",
            "colour": "White",
            "measurements": {"Longueur": "80 cm"},
        },
        "lace_umbrella",
    )

    assert formatted.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }
    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.english.title.casefold()
    assert any(
        word in formatted.english.title.casefold() for word in ("parasol", "umbrella")
    )
    assert "size 36" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "80 cm" in formatted.english.description
    assert "prices are negotiable :)" in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("belt" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_lace_umbrella_listing_uses_size_s_and_style_token() -> None:
    listing = local_listing_from_product(
        {
            "sku": "LACEUMBRELLA-1",
            "title": "Elegant Bohemian Lace Parasol",
            "category": "Umbrellas",
            "colour": "White",
            "measurements": {"Longueur": "80 cm"},
        },
        "lace_umbrella",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert any(word in listing.english.title.casefold() for word in ("parasol", "umbrella"))
    assert "ombrelle" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "80 cm" in listing.english.description
    assert "prices are negotiable :)" in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }


def test_lace_umbrella_prompts_require_size_s_and_prices_negotiable() -> None:
    product = {
        "sku": "LACEUMBRELLA-1",
        "title": "Elegant Bohemian Lace Parasol",
        "category": "Umbrellas",
        "colour": "White",
        "measurements": {"Longueur": "80 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "lace_umbrella", None)
    full_prompt = _full_prompt(product, "lace_umbrella", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size S" in prompt
        assert "Do not generate an image" in prompt
        assert "prices are negotiable :)" in prompt
    assert "Listing type: LACE_UMBRELLA" in full_prompt
    assert '"measurements": {}' in fast_prompt


def test_belt_listing_uses_size_s_style_token_and_price_are_negotiable() -> None:
    draft = FastListingDraft(
        fictional_brand="Velmora",
        english_title=(
            "Vintage western-style metal carved buckle leather belt, "
            "brown, style boho, size 36"
        ),
        french_title=(
            "Ceinture western vintage a boucle sculptee en metal et cuir, "
            "marron, style boho, taille 36"
        ),
        english_description="Do not keep this paragraph.",
        french_description="Ne pas garder ce paragraphe.",
        english_hashtags=["#bag", "#jacket", "#vinted", "#new", "#cowboybelt", "#brownbelt"],
        french_hashtags=["#sac", "#veste", "#nouveau", "#ceinturecowboy", "#ceinturemarron"],
    )
    listing = listing_from_fast_draft(draft, "belt")
    formatted = apply_required_listing_format(
        listing,
        {
            "title": "Vintage Western Style Metal Carved Buckle Leather Belt",
            "colour": "Brown",
            "measurements": {"Longueur": "100 cm"},
        },
        "belt",
    )

    assert formatted.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }
    assert formatted.english.title.endswith("size S")
    assert formatted.french.title.endswith("taille S")
    assert ", style " in formatted.english.title.casefold()
    assert "belt" in formatted.english.title.casefold()
    assert "leather" not in formatted.english.title.casefold()
    assert "metal" not in formatted.english.title.casefold()
    assert "size 36" not in formatted.english.title.casefold()
    assert formatted.english.description.split("\n\n")[0] == formatted.english.title
    assert "100 cm" in formatted.english.description
    assert "price are negotiable :)" in formatted.english.description
    assert "prices are negotiable :)" not in formatted.english.description
    assert "prix négociable :)" in formatted.french.description
    assert formatted.english.description.endswith("Perfect condition.")
    assert "Do not keep this paragraph." not in formatted.english.description
    assert len(formatted.english.hashtags) >= 20
    assert all("bag" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("jacket" not in tag.casefold() for tag in formatted.english.hashtags)
    assert all("vinted" not in tag.casefold() for tag in formatted.english.hashtags)
    assert 65 <= len(formatted.english.title) <= 100
    assert 65 <= len(formatted.french.title) <= 100


def test_local_belt_listing_uses_size_s_and_style_token() -> None:
    listing = local_listing_from_product(
        {
            "sku": "BELT-1",
            "title": "Vintage Western Style Metal Carved Buckle Leather Belt",
            "category": "Belts",
            "colour": "Brown",
            "measurements": {"Longueur": "100 cm"},
        },
        "belt",
    )

    assert listing.english.title.endswith("size S")
    assert listing.french.title.endswith("taille S")
    assert "belt" in listing.english.title.casefold()
    assert "ceinture" in listing.french.title.casefold()
    assert ", style " in listing.english.title.casefold()
    assert "100 cm" in listing.english.description
    assert "price are negotiable :)" in listing.english.description
    assert "prices are negotiable :)" not in listing.english.description
    assert listing.english.description.endswith("Perfect condition.")
    assert listing.fictional_brand in {
        "boho", "bohemian", "chic", "elegant", "oldmoney", "y2k", "goth",
        "street", "grunge", "dark",
    }


def test_belt_prompts_require_size_s_and_price_are_negotiable() -> None:
    product = {
        "sku": "BELT-1",
        "title": "Vintage Western Style Metal Carved Buckle Leather Belt",
        "category": "Belts",
        "colour": "Brown",
        "measurements": {"Longueur": "100 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "belt", None)
    full_prompt = _full_prompt(product, "belt", None)

    for prompt in (fast_prompt, full_prompt):
        assert "size S" in prompt
        assert "Do not generate an image" in prompt
        assert "price are negotiable :)" in prompt
    assert "Listing type: BELT" in full_prompt
    assert '"measurements": {}' in fast_prompt


def test_rich_extracted_facts_can_skip_image_upload() -> None:
    assert facts_are_descriptive_enough(
        {
            "title": (
                "Floral square-neck midi dress with puff sleeves "
                "and fitted waist"
            ),
            "colour": "Pink",
            "category": "Dress",
            "additional_details": {},
        }
    )
    assert not facts_are_descriptive_enough(
        {
            "title": "Pink dress",
            "colour": "Pink",
            "category": "Dress",
            "additional_details": {},
        }
    )


def test_generation_prompts_include_strict_visual_accuracy_rules() -> None:
    product = {
        "sku": "SKU1",
        "title": "Pink floral dress from page text",
        "category": "Dress",
        "colour": "Pink",
        "measurements": {"Longueur": "120 cm"},
        "additional_details": {},
    }

    fast_prompt = _fast_prompt(product, "dress", None)
    full_prompt = _full_prompt(product, "dress", None)

    for prompt in (fast_prompt, full_prompt):
        assert "attached product image as the highest authority" in prompt
        assert "ignore the conflicting text and follow the attached image" in prompt
        assert "Do not reuse details from a previous product" in prompt


def test_selected_image_prompt_overrides_stale_page_colour() -> None:
    product = {
        "sku": "SKU1",
        "title": "Nude dress from stale page text",
        "category": "Dress",
        "colour": None,
        "measurements": {"Longueur": "120 cm"},
        "additional_details": {
            "selected_description_image_url": "/downloads/red-dress.png",
            "ignored_page_colour_for_description": "Nude",
        },
    }

    fast_prompt = _fast_prompt(product, "dress", None)
    full_prompt = _full_prompt(product, "dress", None)

    for prompt in (fast_prompt, full_prompt):
        assert "User-selected image override" in prompt
        assert "only visual source of truth for colour and design" in prompt
        assert "/downloads/red-dress.png" in prompt


def test_full_generation_retries_when_dress_titles_are_too_short(monkeypatch) -> None:
    async def fake_load_reference_image(product):
        return None

    monkeypatch.setattr(gemini_service, "load_reference_image", fake_load_reference_image)

    calls: list[str] = []

    async def fake_generate_json_text(prompt, reference_image):
        calls.append(prompt)
        if len(calls) == 1:
            return "{}"
        if len(calls) == 2:
            return json.dumps(
                {
                    "fictional_brand": "Velmora",
                    "english": {
                        "title": "Green long dress style elegant size S",
                        "description": "",
                        "hashtags": [f"#style{i}" for i in range(20)],
                    },
                    "french": {
                        "title": "Robe longue green, style elegant taille S",
                        "description": "",
                        "hashtags": [f"#mode{i}" for i in range(20)],
                    },
                }
            )
        return json.dumps(
            {
                "fictional_brand": "Velmora",
                "english": {
                    "title": (
                        "Long red off-shoulder maxi dress in an elegant style with draped neckline size S"
                    ),
                    "description": "",
                    "hashtags": [f"#style{i}" for i in range(20)],
                },
                "french": {
                    "title": (
                        "Robe longue rouge épaules dénudées, style élégant avec encolure drapée taille S"
                    ),
                    "description": "",
                    "hashtags": [f"#mode{i}" for i in range(20)],
                },
            }
        )

    monkeypatch.setattr(gemini_service, "_generate_json_text", fake_generate_json_text)

    listing = asyncio.run(
        generate_listing(
            {
                "sku": "SKU1",
                "title": "Long dress",
                "category": "Dress",
                "colour": None,
                "measurements": {},
                "additional_details": {},
            },
            "dress",
        )
    )

    assert len(calls) == 3
    assert "too short" in calls[2]
    assert "red" in listing.english.title.casefold()
    assert "rouge" in listing.french.title.casefold()
    assert "green" not in listing.french.title.casefold()
