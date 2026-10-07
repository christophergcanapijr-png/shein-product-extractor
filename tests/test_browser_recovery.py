from __future__ import annotations

from backend.services.shein_extractor import (
    SheinExtractor,
    candidate_metadata_matches_sku,
    is_access_denied_response,
    is_recommendation_url,
    is_shein_oops_text,
)
from backend.schemas import ProductCandidate


class TargetClosedError(Exception):
    pass


class StoppedPage:
    def is_closed(self) -> bool:
        raise TargetClosedError("Playwright has already stopped")


def test_debug_capture_ignores_page_released_for_normal_browser() -> None:
    SheinExtractor()._debug_capture(
        StoppedPage(),
        "SKU123",
        "failure",
        force=True,
    )


class ClosedContext:
    def new_page(self) -> None:
        raise TargetClosedError("BrowserContext.new_page: context has been closed")


class WorkingContext:
    def __init__(self, page: object) -> None:
        self.page = page

    def new_page(self) -> object:
        return self.page


def test_new_page_rebuilds_a_closed_browser(monkeypatch) -> None:
    extractor = SheinExtractor()
    expected_page = object()
    contexts = iter([ClosedContext(), WorkingContext(expected_page)])
    discarded: list[bool] = []

    monkeypatch.setattr(extractor, "_ensure_context", lambda: next(contexts))
    monkeypatch.setattr(extractor, "_discard_browser", lambda: discarded.append(True))

    assert extractor._new_page() is expected_page
    assert discarded == [True]


def test_closed_context_is_not_reported_as_alive() -> None:
    extractor = SheinExtractor()

    class DisconnectedBrowser:
        def is_connected(self) -> bool:
            return False

    class Context:
        browser = DisconnectedBrowser()

    extractor._context = Context()

    assert extractor._context_is_alive() is False


def test_empty_result_recommendation_links_are_rejected() -> None:
    assert is_recommendation_url(
        "https://fr.shein.com/example-p-123.html"
        "?src_module=OtherListEmptyRecommend"
        "&src_identifier=on=PRODUCT_RECOMMEND_COMPONENT"
    )
    assert not is_recommendation_url(
        "https://fr.shein.com/example-p-123.html?src_module=search"
    )


def test_search_url_uses_the_submitted_sku_as_the_path() -> None:
    extractor = SheinExtractor()
    url = extractor._search_url("  SKU 12/34  ")

    assert "/pdsearch/SKU%2012%2F34/" in url


def test_tracking_query_does_not_make_a_different_product_match() -> None:
    candidate = ProductCandidate(
        title="Different green dress",
        url=(
            "https://fr.shein.com/different-dress-p-123.html"
            "?src_identifier=st%3D2%60sc%3Dsz25031114491515848"
        ),
        visible_identifier="123",
    )

    assert not candidate_metadata_matches_sku(
        "sz25031114491515848",
        candidate,
    )


def test_candidate_owned_sku_is_an_exact_candidate_match() -> None:
    candidate = ProductCandidate(
        title="Green dress",
        url="https://fr.shein.com/green-dress-p-123.html",
        visible_identifier="sz25031114491515848",
    )

    assert candidate_metadata_matches_sku(
        "sz25031114491515848",
        candidate,
    )


def test_shein_403_page_is_not_mislabeled_as_sku_not_found() -> None:
    assert is_access_denied_response(403, "")
    assert is_access_denied_response(None, "STATUS: 403")
    assert not is_access_denied_response(200, "No products matched this search")


def test_shein_oops_page_is_detected_without_treating_recommendations_as_results() -> None:
    assert is_shein_oops_text("OOPS... RETOUR À LA PAGE D’ACCUEIL")
    assert not is_shein_oops_text("SHEIN product results")


class VisibleLocator:
    def count(self) -> int:
        return 1

    def nth(self, index: int) -> "VisibleLocator":
        return self

    def is_visible(self) -> bool:
        return True


class CaptchaPage:
    frames: list[object] = []

    def locator(self, selector: str) -> VisibleLocator:
        if selector == ".one-pass-dialog":
            return VisibleLocator()

        class EmptyLocator(VisibleLocator):
            def count(self) -> int:
                return 0

        return EmptyLocator()


def test_visible_one_pass_verification_dialog_is_detected() -> None:
    extractor = SheinExtractor()

    assert extractor._has_captcha(CaptchaPage()) is True
