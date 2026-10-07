from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse

from backend.config import PROJECT_ROOT, Settings, settings
from backend.errors import ExtractionError
from backend.schemas import ExtractedProduct, ProductCandidate
from backend.services.price_calculator import (
    calculate_vinted_price,
    infer_price_category,
)
from backend.services.shein_extractor import (
    ParsedMeasurements,
    SheinExtractor,
    extraction_size_letter,
    normalize_sku,
    parse_euro_price,
)


logger = logging.getLogger(__name__)

TEMU_BASE_URL = "https://www.temu.com"
TEMU_PRODUCT_PATH_MARKERS = (
    "goods.html",
    "/goods/",
    "-g-",
)
TEMU_EMPTY_RESULT_MARKERS = (
    "no results",
    "no products found",
    "we couldn't find",
    "try another search",
    "aucun résultat",
    "aucun produit",
)
TEMU_CHALLENGE_MARKERS = (
    "/chl/js/",
    "challenge-one-pass",
)
TEMU_TRANSIENT_SEARCH_MARKERS = (
    "please check your network connection and try again",
    "something went wrong. please try again",
    "search failed. please try again",
    "veuillez vérifier votre connexion réseau et réessayer",
    "veuillez verifier votre connexion reseau et reessayer",
)
TEMU_UNAVAILABLE_PRODUCT_MARKERS = (
    "this item is sold out",
    "this item is unavailable",
    "cet article est épuisé",
    "cet article est indisponible",
)


def is_temu_philippines_market_text(value: str) -> bool:
    """Recognize the Philippines storefront from visible, market-owned text."""
    text = (value or "").casefold()
    return "gcash" in text or "philippines" in text or "₱" in value


def is_temu_france_market_text(value: str) -> bool:
    """Recognize Temu's France/EUR region settings page."""
    text = re.sub(r"\s+", " ", (value or "").casefold())
    return (
        "country/region france" in text
        and ("currency eur" in text or "eur : €" in text)
    )


def is_temu_transient_search_error_text(value: str) -> bool:
    text = (value or "").casefold()
    return any(marker in text for marker in TEMU_TRANSIENT_SEARCH_MARKERS)


TEMU_LIVE_LISTING_MARKERS = (
    "add to cart",
    "add to bag",
    "buy now",
    "ajouter au panier",
    "acheter maintenant",
)


def is_temu_unavailable_product_text(value: str) -> bool:
    """True only for a dedicated dead listing, not a live product page.

    Temu product pages almost always mention sold-out colours, similar items,
    or shipping limits. Those strings used to abort extraction of in-stock
    products.
    """
    text = re.sub(r"\s+", " ", value or "").strip().casefold()
    if not text:
        return False
    if any(marker in text for marker in TEMU_LIVE_LISTING_MARKERS):
        return False
    head = text[:500]
    return any(marker in head for marker in TEMU_UNAVAILABLE_PRODUCT_MARKERS)


def build_temu_search_url(sku: str) -> str:
    encoded = quote(sku.strip(), safe="")
    return (
        f"{TEMU_BASE_URL}/search_result.html?search_key={encoded}"
        "&search_method=user"
    )


def is_temu_product_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    path = unquote(parsed.path).casefold()
    return (
        parsed.scheme in {"http", "https"}
        and (host == "temu.com" or host.endswith(".temu.com"))
        and any(marker in path for marker in TEMU_PRODUCT_PATH_MARKERS)
    )


def canonical_temu_product_url(value: str) -> str:
    """Remove per-session Temu tracking while preserving an ID-only URL."""
    parsed = urlparse(value)
    if not is_temu_product_url(value):
        return value
    goods_id = dict(
        part.split("=", 1)
        for part in parsed.query.split("&")
        if "=" in part and part.split("=", 1)[0] == "goods_id"
    ).get("goods_id")
    query = f"goods_id={quote(goods_id, safe='')}" if goods_id else ""
    return parsed._replace(query=query, fragment="").geturl()


def is_temu_verification_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    host = (parsed.hostname or "").casefold()
    return (
        (host == "temu.com" or host.endswith(".temu.com"))
        and parsed.path.casefold().endswith("/bgn_verification.html")
    )


def temu_product_page_matches_sku(
    sku: str,
    *,
    page_url: str = "",
    body_text: str = "",
    metadata_values: list[str] | None = None,
    html: str = "",
) -> bool:
    """Require the requested code in product-owned data, never search tracking."""
    cleaned_sku = sku.strip()
    if not cleaned_sku:
        return False
    token = re.compile(
        rf"(?<![a-z0-9]){re.escape(cleaned_sku)}(?![a-z0-9])",
        re.I,
    )
    values = metadata_values or []
    if any(token.search(value or "") for value in values):
        return True
    if token.search(body_text or ""):
        return True

    # A product slug may contain the SKU. Deliberately ignore query parameters
    # because Temu echoes search_key there as referral/tracking data.
    if token.search(unquote(urlparse(page_url).path)):
        return True

    # Some Temu builds keep the code in product JSON before the details panel
    # is expanded. Only accept values owned by an identifier field.
    normalized = normalize_sku(cleaned_sku)
    identifier_pattern = re.compile(
        r"""["'](?:sku|product_sku|item_sku|goods_sn|product_sn)["']\s*:\s*["']([^"']+)["']""",
        re.I,
    )
    return any(
        normalize_sku(match.group(1)) == normalized
        for match in identifier_pattern.finditer(html or "")
    )


def normalize_temu_image_urls(values: list[str], page_url: str) -> list[str]:
    images: list[str] = []
    for raw_value in values:
        value = (raw_value or "").strip()
        if not value or value.startswith("data:"):
            continue
        value = value.split(",", 1)[0].strip().split(" ", 1)[0]
        url = urljoin(page_url, value)
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        path = parsed.path.casefold()
        # Temu appends imageView resizing/AVIF parameters to gallery files.
        # The CDN path itself serves the original asset and also lets thumbnail
        # and large-view duplicates collapse into one image.
        if "kwcdn" in host and "/product/" in path:
            url = parsed._replace(query="", fragment="").geturl()
            parsed = urlparse(url)
        if (
            parsed.scheme in {"http", "https"}
            and ("kwcdn" in host or "temu" in host)
            and not re.search(
                r"(logo|icon|avatar|sprite|review|user|flag|payment)",
                path,
                re.I,
            )
            and url not in images
        ):
            images.append(url)
    return images


class TemuExtractor(SheinExtractor):
    """Temu extraction using a dedicated persistent Chromium profile."""

    def __init__(self, app_settings: Settings = settings) -> None:
        temu_settings = replace(
            app_settings,
            shein_base_url=TEMU_BASE_URL,
            browser_profile_path=PROJECT_ROOT / "browser_profile_temu",
        )
        temu_settings.browser_profile_path.mkdir(parents=True, exist_ok=True)
        super().__init__(temu_settings)
        self._manual_verification_process: subprocess.Popen[Any] | None = None

    def _browser_executable_path(self) -> str | None:
        # Match SHEIN: use Playwright's managed Chromium instead of opening a
        # second Brave profile.
        return None

    def _has_captcha(self, page: Any) -> bool:
        try:
            if is_temu_verification_url(page.url):
                return True
        except Exception:
            pass
        if super()._has_captcha(page):
            return True
        try:
            html = page.content().casefold()
        except Exception:
            html = ""
        # Temu's normal application bundles contain dormant CAPTCHA strings.
        # Only treat a small script-only bootstrap response as a challenge.
        return len(html) < 100_000 and any(
            marker in html for marker in TEMU_CHALLENGE_MARKERS
        )

    def _normal_browser_executable(self) -> str:
        candidates = (
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            str(
                Path.home()
                / "AppData"
                / "Local"
                / "BraveSoftware"
                / "Brave-Browser"
                / "Application"
                / "brave.exe"
            ),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        )
        for candidate in candidates:
            if Path(candidate).is_file():
                return candidate
        try:
            return self._playwright.chromium.executable_path
        except Exception as exc:
            raise ExtractionError(
                "verification_browser_unavailable",
                "A normal browser window could not be opened for Temu verification.",
                status_code=503,
                retryable=True,
            ) from exc

    def _open_normal_temu_window(
        self,
        page: Any,
        *,
        error_code: str,
        message: str,
        destination_url: str | None = None,
        use_default_profile: bool = False,
    ) -> None:
        target_url = destination_url or page.url
        executable = self._normal_browser_executable()
        profile_path = self.settings.browser_profile_path

        # Release the profile lock held by Playwright. Temu may close or block
        # verification/login pages while automation is attached, so the user
        # completes that step in a normal browser using the same profile.
        self._discard_browser()
        try:
            if use_default_profile:
                # Reuse the user's already-running Default Brave profile. Do
                # not force a second window on top of the current one.
                browser_arguments = [
                    executable,
                    "--profile-directory=Default",
                    target_url,
                ]
            else:
                browser_arguments = [
                    executable,
                    f"--user-data-dir={profile_path}",
                    "--profile-directory=Default",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--new-window",
                    target_url,
                ]
            self._manual_verification_process = subprocess.Popen(
                browser_arguments,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ExtractionError(
                "verification_browser_unavailable",
                "A normal browser window could not be opened for Temu verification.",
                status_code=503,
                retryable=True,
            ) from exc
        raise ExtractionError(
            error_code,
            message,
            status_code=409,
            retryable=True,
        )

    def _open_default_brave_search(self, sku: str) -> None:
        """Open SKU search directly in the user's regular Brave profile."""
        executable = self._normal_browser_executable()
        try:
            self._manual_verification_process = subprocess.Popen(
                [
                    executable,
                    "--profile-directory=Default",
                    build_temu_search_url(sku),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ExtractionError(
                "verification_browser_unavailable",
                "Your regular Brave window could not be opened for Temu search.",
                status_code=503,
                retryable=True,
            ) from exc
        raise ExtractionError(
            "temu_manual_search_required",
            "Temu search was opened in your regular Brave profile. Open the "
            "exact product, copy its address, then click 'Paste product link' "
            "in the app.",
            status_code=409,
            retryable=True,
        )

    def _raise_if_access_denied(
        self, page: Any, status: int | None = None
    ) -> None:
        text = self._page_text(page)
        if status not in {401, 403, 429} and not any(
            marker in text
            for marker in ("access denied", "request blocked", "forbidden")
        ):
            return
        raise ExtractionError(
            "temu_access_denied",
            "Temu blocked the automated request. Set PLAYWRIGHT_BACKGROUND=false, "
            "restart the app, complete Temu verification in Chromium, then retry.",
            status_code=403,
            retryable=True,
        )

    def _raise_if_login_required(self, page: Any) -> None:
        try:
            login_url = "/login.html" in urlparse(page.url).path.casefold()
        except Exception:
            login_url = False
        text = self._page_text(page)
        login_text = any(
            marker in text
            for marker in (
                "sign in to continue",
                "log in to continue",
                "sign in / register",
            )
        )
        if not login_url and not login_text:
            return
        raise ExtractionError(
            "login_required",
            "Sign in to Temu in the opened Chromium window, then click Retry.",
            status_code=409,
            retryable=True,
        )

    def _ensure_france_market(self, page: Any) -> bool:
        """Switch the dedicated Temu profile from Philippines to France."""
        if not is_temu_philippines_market_text(self._page_text(page)):
            return False
        try:
            page.locator(
                '[role="button"][aria-label^="Philippines "]'
            ).first.click(timeout=5_000)
            page.get_by_text(
                "Change country/region", exact=True
            ).click(timeout=5_000)
            page.get_by_role(
                "button", name="Philippines", exact=True
            ).click(force=True, timeout=5_000)
            page.get_by_role(
                "button", name="France", exact=True
            ).click(force=True, timeout=5_000)
            page.get_by_role(
                "button", name="Switch to France", exact=True
            ).click(force=True, timeout=5_000)
            page.wait_for_timeout(2_000)
            if not is_temu_france_market_text(self._page_text(page)):
                raise RuntimeError("Temu did not confirm the France storefront")
            logger.info("Temu storefront switched automatically to France/EUR")
            return True
        except ExtractionError:
            raise
        except Exception:
            logger.exception("Automatic Temu France region switch failed")
            self._open_normal_temu_window(
                page,
                error_code="temu_region_mismatch",
                destination_url=f"{TEMU_BASE_URL}/",
                message=(
                    "Temu is using the Philippines catalog and its automatic "
                    "France switch did not complete. In Brave, click the "
                    "Philippine flag, switch to France/EUR, close Brave, then "
                    "click Retry."
                ),
            )
        return False

    def _dismiss_temu_cookie_consent(self, page: Any) -> None:
        """Save a privacy-preserving consent choice so the overlay cannot block search."""
        for label in ("Reject All", "Tout refuser"):
            try:
                button = page.get_by_role("button", name=label, exact=True)
                if button.count() and button.first.is_visible():
                    button.first.click(timeout=3_000)
                    page.wait_for_timeout(300)
                    return
            except Exception:
                continue

    def _wait_for_manual_verification(self, page: Any) -> None:
        if not self._has_captcha(page):
            return
        logger.warning("Temu verification detected; waiting for manual completion")
        if self.settings.playwright_headless:
            raise ExtractionError(
                "captcha_detected",
                "Temu requested verification. Set PLAYWRIGHT_HEADLESS=false, "
                "restart the app, and retry.",
                status_code=409,
                retryable=True,
            )
        was_background = self.settings.playwright_background
        if was_background and not self._set_verification_window_visible(page, True):
            raise ExtractionError(
                "captcha_detected",
                "Temu requested verification. Set PLAYWRIGHT_BACKGROUND=false, "
                "restart the app, complete verification in Chromium, then retry.",
                status_code=409,
                retryable=True,
            )
        deadline = time.monotonic() + self.settings.captcha_wait_seconds
        while time.monotonic() < deadline:
            if page.is_closed() or not self._has_captcha(page):
                if was_background and not page.is_closed():
                    self._set_verification_window_visible(page, False)
                return
            time.sleep(2)
        self._verification_page = page
        raise ExtractionError(
            "captcha_detected",
            "Complete Temu verification in the opened Chromium window, then click Retry.",
            status_code=409,
            retryable=True,
        )

    def _wait_for_product_ready(
        self, page: Any, *, timeout_ms: int = 15_000
    ) -> None:
        deadline = time.monotonic() + timeout_ms / 1_000
        while time.monotonic() < deadline:
            self._raise_if_access_denied(page)
            self._raise_if_login_required(page)
            try:
                self._extract_title(page)
                return
            except ExtractionError:
                if is_temu_unavailable_product_text(self._page_text(page)):
                    raise ExtractionError(
                        "temu_product_unavailable",
                        "This Temu product is sold out or unavailable in the France "
                        "catalog.",
                        status_code=410,
                    )
                if self._has_captcha(page):
                    self._wait_for_manual_verification(page)
            page.wait_for_timeout(200)
        raise ExtractionError(
            "product_page_timeout",
            "The Temu product page did not finish loading. Please retry.",
            status_code=504,
            retryable=True,
        )

    def _collect_temu_candidates(
        self,
        page: Any,
        searched_sku: str | None = None,
    ) -> list[ProductCandidate]:
        raw: list[dict[str, str | None]] = page.evaluate(
            """
            () => {
              const candidates = Array.from(document.querySelectorAll('a[href]'))
                .slice(0, 800)
                .map(link => {
                const card = link.closest(
                  '[data-sku], [data-goods-id], [data-product-id], article'
                );
                const image = link.querySelector('img');
                return {
                  href: link.href || link.getAttribute('href'),
                  title: link.getAttribute('aria-label')
                    || link.getAttribute('title')
                    || (image && image.getAttribute('alt'))
                    || (link.innerText || '').trim(),
                  identifier: card && (
                    card.getAttribute('data-sku')
                    || card.getAttribute('data-goods-id')
                    || card.getAttribute('data-product-id')
                  ),
                  image: image && (
                    image.getAttribute('data-src')
                    || image.getAttribute('src')
                  )
                };
                });
              document.querySelectorAll('script[type="application/ld+json"]')
                .forEach(script => {
                  try {
                    const parsed = JSON.parse(script.textContent);
                    const roots = Array.isArray(parsed) ? parsed : [parsed];
                    roots.forEach(root => {
                      const entries = root && root.itemListElement
                        ? root.itemListElement : [root];
                      entries.forEach(entry => {
                        const item = entry && entry.item ? entry.item : entry;
                        if (!item || !item.url) return;
                        candidates.push({
                          href: item.url,
                          title: item.name || 'Temu product',
                          identifier: item.sku || item.productID || null,
                          image: typeof item.image === 'string'
                            ? item.image
                            : item.image && item.image.url
                        });
                      });
                    });
                  } catch (_) {}
                });
              return candidates;
            }
            """
        )
        found: dict[str, ProductCandidate] = {}
        for item in raw:
            href = item.get("href")
            if not href:
                continue
            url = urljoin(TEMU_BASE_URL, href)
            if not is_temu_product_url(url):
                continue
            found[url] = ProductCandidate(
                title=re.sub(r"\s+", " ", item.get("title") or "Temu product")[
                    :240
                ],
                url=url,
                image_url=item.get("image"),
                visible_identifier=item.get("identifier"),
            )
            if len(found) >= 40:
                break
        candidates = list(found.values())
        if searched_sku and len(candidates) == 1:
            try:
                exact_query_visible = page.evaluate(
                    """
                    sku => Array.from(document.querySelectorAll('input'))
                      .some(input =>
                        (input.value || '').trim().toLowerCase()
                          === sku.trim().toLowerCase()
                      )
                    """,
                    searched_sku,
                )
            except Exception:
                exact_query_visible = False
            if exact_query_visible:
                candidates[0].visible_identifier = searched_sku
        return candidates

    def _wait_for_temu_candidates(
        self,
        page: Any,
        sku: str,
        *,
        timeout_ms: int = 20_000,
    ) -> list[ProductCandidate]:
        started = time.monotonic()
        deadline = started + timeout_ms / 1_000
        while time.monotonic() < deadline:
            self._raise_if_access_denied(page)
            self._wait_for_manual_verification(page)
            self._raise_if_login_required(page)
            candidates = self._collect_temu_candidates(page, sku)
            if candidates:
                return candidates
            text = self._page_text(page)
            if (
                time.monotonic() - started >= 1.2
                and is_temu_transient_search_error_text(text)
            ):
                raise ExtractionError(
                    "temu_search_network_error",
                    "Temu's search request temporarily failed. The extractor "
                    "will retry automatically.",
                    status_code=503,
                    retryable=True,
                )
            if (
                time.monotonic() - started >= 2.5
                and any(marker in text for marker in TEMU_EMPTY_RESULT_MARKERS)
            ):
                return []
            page.wait_for_timeout(200)
        raise ExtractionError(
            "search_results_timeout",
            "Temu did not finish loading the exact SKU search. Please retry.",
            status_code=504,
            retryable=True,
        )

    def _submit_temu_search(self, page: Any, sku: str) -> bool:
        selectors = (
            "#searchInput",
            'input[type="search"]',
            'input[aria-label*="Search" i]',
        )
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 3)):
                search_input = locator.nth(index)
                try:
                    if not search_input.is_visible() or not search_input.is_enabled():
                        continue
                    search_input.fill("")
                    search_input.fill(sku)
                    search_input.press("Enter")
                    page.wait_for_timeout(600)
                    return True
                except Exception:
                    continue
        return False

    def _temu_page_sku_matches(
        self, page: Any, sku: str, *, timeout_ms: int = 4_000
    ) -> bool:
        selectors = (
            ('meta[itemprop="sku"]', "content"),
            ('[data-sku]', "data-sku"),
            ('[data-product-sku]', "data-product-sku"),
            ('[data-item-sku]', "data-item-sku"),
            ('[data-goods-sn]', "data-goods-sn"),
        )
        deadline = time.monotonic() + timeout_ms / 1_000
        while time.monotonic() < deadline:
            metadata: list[str] = []
            for selector, attribute in selectors:
                locator = page.locator(selector)
                for index in range(min(locator.count(), 10)):
                    try:
                        value = locator.nth(index).get_attribute(attribute)
                        if value:
                            metadata.append(value)
                    except Exception:
                        continue
            try:
                body_text = page.locator("body").inner_text(timeout=800)
            except Exception:
                body_text = ""
            try:
                html = page.content()
            except Exception:
                html = ""
            if temu_product_page_matches_sku(
                sku,
                page_url=page.url,
                body_text=body_text,
                metadata_values=metadata,
                html=html,
            ):
                return True
            page.wait_for_timeout(200)
        return False

    def _select_exact_temu_product(
        self, search_page: Any, sku: str, candidates: list[ProductCandidate]
    ) -> tuple[Any, bool]:
        # Open only a small set from an exact search and verify each product page.
        # No image is downloaded until this method returns an exact match.
        for candidate in candidates[:3]:
            probe = self._context.new_page()
            matched = False
            keep_probe = False
            try:
                self._goto(probe, candidate.url)
                self._wait_for_manual_verification(probe)
                self._wait_for_product_ready(probe, timeout_ms=15_000)
                if self._temu_page_sku_matches(probe, sku):
                    matched = True
                    return probe, False
                if normalize_sku(candidate.visible_identifier or "") == normalize_sku(
                    sku
                ):
                    matched = True
                    return probe, True
            except ExtractionError as exc:
                if exc.code == "captcha_detected":
                    keep_probe = True
                    self._verification_page = probe
                raise
            except Exception:
                logger.debug(
                    "Temu candidate probe failed: %s",
                    candidate.url,
                    exc_info=True,
                )
            finally:
                if not matched and not keep_probe and not probe.is_closed():
                    probe.close()
        raise ExtractionError(
            "sku_not_found",
            "No product found.",
            status_code=404,
        )

    def _extract_temu_price_eur(self, page: Any) -> float | None:
        try:
            raw = page.evaluate(
                """
                () => {
                  const hooked = document.getElementById(
                    "product-extractor-temu-price-data"
                  );
                  if (hooked?.textContent) return hooked.textContent;
                  const heading = document.querySelector("h1");
                  const scope = heading?.parentElement?.parentElement
                    || document.body;
                  const texts = [];
                  scope.querySelectorAll(
                    "[aria-label], [itemprop='price'], [class*='price' i], span, strong"
                  ).forEach((element) => {
                    const style = getComputedStyle(element);
                    if (style.textDecorationLine.includes("line-through")) return;
                    if (/(original|market|was-|retail|strikethrough)/i.test(
                      element.className || ""
                    )) return;
                    const label = element.getAttribute("aria-label") || "";
                    const text = (element.innerText || "").replace(/\\s+/g, " ").trim();
                    const value = /€|EUR/i.test(label) ? label : text;
                    if (
                      /€|EUR/i.test(value)
                      && /\\d/.test(value)
                      && value.length < 40
                    ) {
                      texts.push(value);
                    }
                  });
                  return texts[0] || null;
                }
                """
            )
        except Exception:
            raw = None
        if isinstance(raw, str) and raw.lstrip().startswith("{"):
            try:
                data = json.loads(raw)
                hooked_price = data.get("price_eur")
                if hooked_price not in (None, ""):
                    parsed_hook = float(hooked_price)
                    if 0 < parsed_hook < 500:
                        return parsed_hook
                raw = data.get("price_text")
            except Exception:
                pass
        parsed = parse_euro_price(raw if isinstance(raw, str) else None)
        if parsed is not None:
            return parsed

        selectors = (
            'meta[property="product:price:amount"]',
            '[itemprop="price"]',
            '[data-testid*="price" i]',
        )
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 8)):
                item = locator.nth(index)
                for attribute in ("content", "data-price", "aria-label"):
                    try:
                        parsed = parse_euro_price(item.get_attribute(attribute))
                    except Exception:
                        parsed = None
                    if parsed is not None:
                        return parsed
                try:
                    parsed = parse_euro_price(item.inner_text(timeout=500))
                except Exception:
                    parsed = None
                if parsed is not None:
                    return parsed
        return None

    def _extract_temu_images(self, page: Any) -> list[str]:
        raw: list[str] = page.evaluate(
            """
            () => {
              const values = [];
              const add = value => {
                if (!value || typeof value !== 'string') return;
                value.split(',').forEach(part => {
                  const url = part.trim().split(/\\s+/)[0];
                  if (url) values.push(url);
                });
              };
              document.querySelectorAll(
                'meta[property="og:image"], meta[name="twitter:image"]'
              ).forEach(element => add(element.content));
              document.querySelectorAll(
                'img[aria-label="Goods Image"],'
                + 'img[src*="img.kwcdn.com/product/"],'
                + 'img[data-src*="img.kwcdn.com/product/"],'
                + 'main img, [class*="gallery" i] img, [class*="thumb" i] img'
              ).forEach(image => {
                const productLink = image.closest(
                  'a[href*="goods.html"], a[href*="-g-"]'
                );
                if (productLink) return;
                ['data-original','data-src','src','srcset']
                  .forEach(attribute => add(image.getAttribute(attribute)));
              });
              document.querySelectorAll('script[type="application/ld+json"]')
                .forEach(script => {
                  try {
                    const parsed = JSON.parse(script.textContent);
                    const items = Array.isArray(parsed) ? parsed : [parsed];
                    items.forEach(item => {
                      const images = Array.isArray(item.image)
                        ? item.image : [item.image];
                      images.forEach(image => {
                        if (typeof image === 'string') add(image);
                        else if (image && image.url) add(image.url);
                      });
                    });
                  } catch (_) {}
                });
              return values;
            }
            """
        )
        return normalize_temu_image_urls(raw, page.url)

    def ingest_extension_product(
        self,
        sku: str,
        item_type: str,
        payload: dict[str, Any],
    ) -> ExtractedProduct:
        """Turn data read by the trusted Brave profile into a saved product."""
        product_url = canonical_temu_product_url(
            str(payload.get("product_url") or "")
        )
        if not is_temu_product_url(product_url):
            raise ExtractionError(
                "invalid_candidate_url",
                "The Brave extension did not return a valid Temu product URL.",
                status_code=422,
            )

        images = normalize_temu_image_urls(
            list(payload.get("image_urls") or []),
            product_url,
        )[:6]
        if not images:
            raise ExtractionError(
                "product_image_unavailable",
                "No original Temu product images could be read.",
                status_code=422,
            )
        captured_images = self._screenshot_product_images(sku, images)
        if captured_images:
            main_image = captured_images[0]
            additional_images = captured_images[1:]
            image_is_screenshot = True
        else:
            # Keep the original CDN files visible even if local downloading is
            # temporarily rejected. The app's image proxy can still display them.
            main_image = images[0]
            additional_images = images[1:]
            image_is_screenshot = False

        title = re.sub(r"\s+", " ", str(payload.get("title") or "")).strip()
        if not title:
            raise ExtractionError(
                "product_title_unavailable",
                "The Temu product title could not be read.",
                status_code=422,
            )
        body_text = str(payload.get("body_text") or "")
        colour = payload.get("colour") or self._extract_detail(
            body_text, ("Color", "Colour", "Couleur")
        )
        material = payload.get("material") or self._extract_detail(
            body_text,
            ("Material", "Materials", "Composition", "MatiÃ¨re"),
        )
        category = payload.get("category") or self._extract_detail(
            body_text, ("Category", "CatÃ©gorie")
        )

        supplied_price = payload.get("price_eur")
        source_price_eur = None
        if supplied_price not in (None, ""):
            try:
                parsed_supplied = float(supplied_price)
            except (TypeError, ValueError):
                parsed_supplied = None
            if parsed_supplied is not None and 0 < parsed_supplied < 500:
                source_price_eur = parsed_supplied
        if source_price_eur is None:
            source_price_eur = parse_euro_price(payload.get("price_text"))
        price_category = infer_price_category(title, category)
        if source_price_eur is not None:
            price_calculator = calculate_vinted_price(
                source_price_eur, price_category
            )
        else:
            price_calculator = {
                "source_price_eur": None,
                "adjustment_eur": 0.0,
                "adjusted_price_eur": None,
                "category": price_category,
                "category_label": None,
                "vinted_price_min_eur": None,
                "vinted_price_max_eur": None,
                "recommended_price_eur": None,
                "eligible": False,
                "message": "The current Temu price could not be read.",
            }

        size_letter = extraction_size_letter(item_type)
        if size_letter:
            measurements = dict(payload.get("measurements") or {})
            measurement_originals = dict(
                payload.get("measurement_originals") or {}
            )
            size_label = f"Size {size_letter}"
            measurement_table_type = (
                payload.get("measurement_table_type")
                or (
                    f"{size_label} measurements read in Brave"
                    if measurements
                    else f"{size_label} measurement card was not available on Temu"
                )
            )
        else:
            measurements = {}
            measurement_originals = {}
            measurement_table_type = f"Measurements not required for {item_type}"

        return ExtractedProduct(
            sku=sku,
            title=title,
            product_url=product_url,
            main_image_url=main_image,
            additional_image_urls=additional_images,
            colour=colour,
            material=material,
            category=category,
            measurements=measurements,
            measurement_originals=measurement_originals,
            measurement_table_type=measurement_table_type,
            additional_details={
                "item_type": item_type,
                "store": "temu",
                "extraction_method": "brave_extension",
                "price_calculator": price_calculator,
            },
            image_is_screenshot=image_is_screenshot,
        )

    def extract(
        self,
        sku: str,
        candidate_url: str | None = None,
        item_type: str = "dress",
    ) -> ExtractedProduct:
        search_page: Any = None
        page: Any = None
        exact_search_result_verified = False
        keep_page = False
        try:
            search_page = self._new_page()
            if candidate_url:
                if not is_temu_product_url(candidate_url):
                    raise ExtractionError(
                        "invalid_candidate_url",
                        "The selected URL is not a Temu product.",
                        status_code=422,
                    )
                page = search_page
                self._goto(page, candidate_url)
                self._wait_for_manual_verification(page)
                self._wait_for_product_ready(page, timeout_ms=15_000)
                # A URL explicitly pasted/selected by the user is the exact
                # product. Temu product pages commonly omit the seller's
                # searchable code, so it cannot be re-verified from the page.
                exact_search_result_verified = True
            else:
                logger.info("Searching Temu SKU: %s", sku)
                search_url = build_temu_search_url(sku)
                candidates: list[ProductCandidate] = []
                for attempt in range(3):
                    if attempt == 1:
                        logger.info(
                            "Temu returned no result; submitting through search box"
                        )
                        if not self._submit_temu_search(search_page, sku):
                            self._goto(search_page, search_url)
                    else:
                        self._goto(search_page, search_url)
                    self._wait_for_manual_verification(search_page)
                    self._dismiss_temu_cookie_consent(search_page)
                    if self._ensure_france_market(search_page):
                        # Temu changes the catalog on a settings page, so return
                        # to the exact requested search after the switch.
                        self._goto(search_page, search_url)
                        self._wait_for_manual_verification(search_page)
                        self._dismiss_temu_cookie_consent(search_page)
                    try:
                        candidates = self._wait_for_temu_candidates(
                            search_page, sku
                        )
                    except ExtractionError as exc:
                        if exc.code != "temu_search_network_error":
                            raise
                        logger.warning(
                            "Temu search request failed (attempt %s/3)",
                            attempt + 1,
                        )
                        if attempt < 2:
                            search_page.wait_for_timeout(1_000)
                            continue
                        raise ExtractionError(
                            "temu_search_network_error",
                            "Temu blocked the Chromium search after three "
                            "attempts. Complete any verification visible in "
                            "Chromium, then click Retry.",
                            status_code=503,
                            retryable=True,
                        )
                    if candidates:
                        break
                if not candidates:
                    if is_temu_philippines_market_text(
                        self._page_text(search_page)
                    ):
                        self._open_normal_temu_window(
                            search_page,
                            error_code="temu_region_mismatch",
                            destination_url=f"{TEMU_BASE_URL}/",
                            message=(
                                "Temu is set to the Philippines catalog, where "
                                "this SKU may not exist. In the Brave window, "
                                "click the Philippine flag, change Country/Region "
                                "to France and currency to EUR, sign in again if "
                                "asked, close Brave, then click Retry."
                            ),
                        )
                    raise ExtractionError(
                        "sku_not_found",
                        "No product found.",
                        status_code=404,
                    )
                page, exact_search_result_verified = self._select_exact_temu_product(
                    search_page, sku, candidates
                )

            if (
                not exact_search_result_verified
                and not self._temu_page_sku_matches(page, sku)
            ):
                raise ExtractionError(
                    "sku_not_found",
                    "No product found.",
                    status_code=404,
                )

            title = self._extract_title(page)
            source_price_eur = self._extract_temu_price_eur(page)
            images = self._extract_temu_images(page)
            captured_images = self._screenshot_product_images(sku, images)
            if captured_images:
                main_image = captured_images[0]
                additional_images = captured_images[1:]
                image_is_screenshot = True
            elif images:
                main_image = images[0]
                additional_images = images[1:]
                image_is_screenshot = False
            else:
                main_image = self._screenshot_main_image(page, sku)
                additional_images = []
                image_is_screenshot = True

            body_text = page.locator("body").inner_text()
            colour = self._extract_detail(body_text, ("Color", "Colour", "Couleur"))
            material = self._extract_detail(
                body_text,
                ("Material", "Materials", "Composition", "Matière"),
            )
            category = self._extract_category(page)

            size_letter = extraction_size_letter(item_type)
            if size_letter:
                parsed = self._measurements_from_size_hover(page, sku, size_letter)
            else:
                parsed = ParsedMeasurements(
                    measurements={},
                    originals={},
                    table_type=f"Measurements not required for {item_type}",
                )

            price_category = infer_price_category(title, category)
            if source_price_eur is not None:
                price_calculator = calculate_vinted_price(
                    source_price_eur, price_category
                )
            else:
                price_calculator = {
                    "source_price_eur": None,
                    "adjustment_eur": 0.0,
                    "adjusted_price_eur": None,
                    "category": price_category,
                    "category_label": None,
                    "vinted_price_min_eur": None,
                    "vinted_price_max_eur": None,
                    "recommended_price_eur": None,
                    "eligible": False,
                    "message": "The current Temu price could not be read.",
                }

            return ExtractedProduct(
                sku=sku,
                title=title,
                product_url=page.url,
                main_image_url=main_image,
                additional_image_urls=additional_images,
                colour=colour,
                material=material,
                category=category,
                measurements=parsed.measurements,
                measurement_originals=parsed.originals,
                measurement_table_type=parsed.table_type,
                additional_details={
                    "item_type": item_type,
                    "store": "temu",
                    "price_calculator": price_calculator,
                },
                image_is_screenshot=image_is_screenshot,
            )
        except ExtractionError as exc:
            keep_page = (
                exc.code
                in {
                    "captcha_detected",
                    "login_required",
                    "temu_search_network_error",
                }
                and not self.settings.playwright_headless
            )
            debug_page = page or search_page
            if debug_page is not None:
                self._debug_capture(debug_page, sku, "temu-failure", force=True)
            if keep_page:
                self._verification_page = debug_page
            raise
        except Exception as exc:
            debug_page = page or search_page
            if debug_page is not None:
                self._debug_capture(debug_page, sku, "temu-failure", force=True)
            name = type(exc).__name__
            if "TargetClosed" in name or "closed" in str(exc).casefold():
                self._discard_browser()
                raise ExtractionError(
                    "browser_closed",
                    "The Temu Chromium window was closed. Click Retry and keep it "
                    "open while extraction runs.",
                    status_code=409,
                    retryable=True,
                ) from exc
            if "Timeout" in name:
                raise ExtractionError(
                    "temu_timeout",
                    "Temu took too long to respond. Please retry.",
                    status_code=504,
                    retryable=True,
                ) from exc
            raise ExtractionError(
                "network_failure",
                "The product could not be extracted because Temu or the network "
                "returned an unexpected error.",
                status_code=502,
                details={"reason": name},
                retryable=True,
            ) from exc
        finally:
            for open_page in (page, search_page):
                if (
                    not keep_page
                    and open_page is not None
                    and open_page != self._verification_page
                ):
                    try:
                        if not open_page.is_closed():
                            open_page.close()
                    except Exception:
                        logger.debug("Temu extraction page was already closed")
