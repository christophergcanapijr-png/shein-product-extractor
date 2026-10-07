from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from backend.services.temu_extractor import (
    TemuExtractor,
    build_temu_search_url,
    is_temu_france_market_text,
    is_temu_philippines_market_text,
    is_temu_product_url,
    is_temu_transient_search_error_text,
    is_temu_unavailable_product_text,
    is_temu_verification_url,
    normalize_temu_image_urls,
    temu_product_page_matches_sku,
)


def test_philippines_market_detection_uses_visible_storefront_markers() -> None:
    assert is_temu_philippines_market_text("Secure payments with GCash")
    assert is_temu_philippines_market_text("Price: ₱284")
    assert is_temu_philippines_market_text("Country/Region: Philippines")
    assert not is_temu_philippines_market_text("Price: €15.00")


def test_france_market_detection_requires_france_and_eur() -> None:
    assert is_temu_france_market_text(
        "Country/Region\nFrance\nLanguage\nEnglish\nCurrency\nEUR : €"
    )
    assert not is_temu_france_market_text(
        "Country/Region\nFrance\nCurrency\nUSD : $"
    )


def test_temu_network_failure_is_not_treated_as_empty_results() -> None:
    assert is_temu_transient_search_error_text(
        "Please check your network connection and try again."
    )
    assert is_temu_transient_search_error_text(
        "Veuillez vérifier votre connexion réseau et réessayer."
    )
    assert not is_temu_transient_search_error_text(
        'No results for "DX10476663"'
    )


def test_temu_sold_out_page_is_recognized() -> None:
    assert is_temu_unavailable_product_text("Cet article est épuisé.")
    assert is_temu_unavailable_product_text("This item is sold out")
    assert not is_temu_unavailable_product_text("In stock")


def test_live_temu_page_is_not_sold_out_because_of_other_badges() -> None:
    page = """
    Cute bear backpack
    €12.99
    Add to cart
    Black Sold out
    You may also like
    This item is sold out
    This item is unavailable for pickup
    """
    assert not is_temu_unavailable_product_text(page)
    assert not is_temu_unavailable_product_text(
        "Ajouter au panier\nCet article est épuisé dans cette couleur"
    )


def test_temu_search_uses_search_key() -> None:
    url = build_temu_search_url(" HE24 886/87 ")
    parsed = urlparse(url)

    assert parsed.netloc == "www.temu.com"
    assert parsed.path == "/search_result.html"
    assert parse_qs(parsed.query)["search_key"] == ["HE24 886/87"]
    assert parse_qs(parsed.query)["search_method"] == ["user"]
    assert "is_back" not in parse_qs(parsed.query)


def test_temu_uses_managed_chromium_like_shein() -> None:
    assert TemuExtractor()._browser_executable_path() is None


def test_temu_product_url_detection() -> None:
    assert is_temu_product_url(
        "https://www.temu.com/example-product-g-601099999.html"
    )
    assert is_temu_product_url(
        "https://www.temu.com/goods.html?goods_id=601099999"
    )
    assert not is_temu_product_url(
        "https://www.temu.com/search_result.html?search_key=HE2488687"
    )
    assert not is_temu_product_url(
        "https://temu.com.evil.example/example-g-601099999.html"
    )


def test_temu_verification_url_detection() -> None:
    assert is_temu_verification_url(
        "https://www.temu.com/bgn_verification.html?verifyCode=abc"
    )
    assert not is_temu_verification_url(
        "https://www.temu.com/search_result.html?search_key=PW514089"
    )
    assert not is_temu_verification_url(
        "https://temu.com.evil.example/bgn_verification.html"
    )


def test_search_key_tracking_does_not_verify_wrong_product() -> None:
    assert not temu_product_page_matches_sku(
        "HE2488687",
        page_url=(
            "https://www.temu.com/different-product-g-601099999.html"
            "?search_key=HE2488687"
        ),
        body_text="Different product",
        html='<a href="?search_key=HE2488687">tracking</a>',
    )


def test_visible_product_id_verifies_exact_sku() -> None:
    assert temu_product_page_matches_sku(
        "HE2488687",
        page_url="https://www.temu.com/example-g-601099999.html",
        body_text="Product ID: HE2488687",
    )
    assert not temu_product_page_matches_sku(
        "HE2488687",
        page_url="https://www.temu.com/example-g-601099999.html",
        body_text="Product ID: HE24886870",
    )


def test_product_owned_json_identifier_verifies_sku() -> None:
    assert temu_product_page_matches_sku(
        "HE2488687",
        html='{"product_sku":"HE2488687","title":"Bag"}',
    )


def test_temu_images_keep_cdn_product_assets_only() -> None:
    images = normalize_temu_image_urls(
        [
            (
                "//img.kwcdn.com/product/fancy-bag.jpg"
                "?imageView2/2/w/180/q/70/format/avif"
            ),
            (
                "//img.kwcdn.com/product/fancy-bag.jpg"
                "?imageView2/2/w/800/q/70/format/avif"
            ),
            "https://img.kwcdn.com/review/user-photo.jpg",
            "data:image/png;base64,abc",
        ],
        "https://www.temu.com/example-g-601099999.html",
    )

    assert images == ["https://img.kwcdn.com/product/fancy-bag.jpg"]


class EmptyLocator:
    def count(self) -> int:
        return 0

    def inner_text(self, **_: object) -> str:
        return "Normal Temu search results"


class NormalSearchPage:
    url = "https://www.temu.com/search_result.html?search_key=PW514089"
    frames: list[object] = []

    def locator(self, _: str) -> EmptyLocator:
        return EmptyLocator()

    def content(self) -> str:
        return "<html>" + ("captcha-module-name " * 8_000) + "</html>"


def test_normal_large_temu_page_is_not_false_captcha() -> None:
    assert TemuExtractor()._has_captcha(NormalSearchPage()) is False


class JsonLdCandidatePage:
    def evaluate(self, _: str, sku: str | None = None) -> object:
        if sku is not None:
            return True
        return [
            {
                "href": (
                    "https://www.temu.com/ph/cute-bear-backpack"
                    "-g-601099767156171.html"
                ),
                "title": "Cute Bear Backpack",
                "identifier": None,
                "image": "https://img.kwcdn.com/product/bear.jpg",
            }
        ]


def test_single_json_ld_result_is_bound_to_exact_visible_search() -> None:
    candidates = TemuExtractor()._collect_temu_candidates(
        JsonLdCandidatePage(),
        "PW514089",
    )

    assert len(candidates) == 1
    assert candidates[0].visible_identifier == "PW514089"
    assert candidates[0].title == "Cute Bear Backpack"
