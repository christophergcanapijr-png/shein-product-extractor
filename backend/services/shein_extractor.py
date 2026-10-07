from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from backend.config import Settings, settings
from backend.errors import ExtractionError
from backend.schemas import ExtractedProduct, ProductCandidate
from backend.services.price_calculator import (
    calculate_vinted_price,
    infer_price_category,
)


logger = logging.getLogger(__name__)

PRODUCT_LINK_SELECTORS = (
    'a[href*="-p-"]',
    'a[href*="/product/"]',
    '[data-goods-id] a[href]',
    '[data-product-id] a[href]',
)
SEARCH_SELECTORS = (
    'input[type="search"]',
    'input[placeholder*="Rechercher" i]',
    'input[placeholder*="recherche" i]',
    'input[aria-label*="recherch" i]',
    'header input[type="text"]',
)
COOKIE_BUTTON_TEXTS = (
    "Tout accepter",
    "Accepter",
    "J'accepte",
    "Accept All",
    "Autoriser tous les cookies",
)
SIZE_GUIDE_TEXTS = (
    "Guide des tailles",
    "Guide de taille",
    "Taille & coupe",
    "Size Guide",
)
SIZE_S_RADIO_SELECTORS = (
    '[data-attr_value_name="S"][role="radio"]',
    '[data-attr_value_name="S"] [role="radio"]',
    '[data-attr_value_name$=" S"][role="radio"]',
    '[data-attr_value_name$=" S"] [role="radio"]',
    '[role="radio"][aria-label$="(S)"]',
    '[role="radio"][aria-label$=" S)"]',
    '[role="radio"][aria-label="S"]',
)


def size_radio_selectors(size: str) -> tuple[str, ...]:
    letter = size.strip().upper()
    if letter == "S":
        return SIZE_S_RADIO_SELECTORS
    return (
        f'[data-attr_value_name="{letter}"][role="radio"]',
        f'[data-attr_value_name="{letter}"] [role="radio"]',
        f'[data-attr_value_name$=" {letter}"][role="radio"]',
        f'[data-attr_value_name$=" {letter}"] [role="radio"]',
        f'[data-attr_value_name^="{letter} "][role="radio"]',
        f'[data-attr_value_name^="{letter} ("][role="radio"]',
        f'[role="radio"][aria-label$="({letter})"]',
        f'[role="radio"][aria-label$=" {letter})"]',
        f'[role="radio"][aria-label="{letter}"]',
    )


def extraction_size_letter(item_type: str) -> str | None:
    if item_type == "dress_m":
        return "M"
    if item_type == "jeans":
        return "L"
    if item_type in {"dress", "skirt", "coat", "jacket"}:
        return "S"
    return None
CAPTCHA_MARKERS = (
    "captcha",
    "verifiez que vous etes humain",
    "verifier que vous etes humain",
    "verify you are human",
    "verification de securite",
    "security verification",
    "je suis humain",
    "veuillez cliquer pour effectuer les actions suivantes",
)
CAPTCHA_SELECTORS = (
    ".one-pass-dialog",
    ".js-challenge-one-pass-overlay",
    '[class*="captcha" i]',
    '[class*="challenge-one-pass" i]',
)
LOGIN_MARKERS = (
    "connectez-vous pour continuer",
    "connexion requise",
    "sign in to continue",
    "login required",
)
EMPTY_RESULT_MARKERS = (
    "aucun résultat",
    "aucun produit trouvé",
    "nous n'avons trouvé aucun résultat",
    "no results found",
    "we couldn't find any results",
)
ACCESS_DENIED_MARKERS = (
    "status: 403",
    "access denied",
    "request blocked",
    "forbidden",
)
RECOMMENDATION_URL_MARKERS = (
    "otherlistemptyrecommend",
    "product_recommend_component",
    "recommend_component",
)


def _euro_number_to_float(number: str) -> float | None:
    compact = number.replace(" ", "")
    if "," in compact and "." in compact:
        compact = compact.replace(".", "").replace(",", ".")
    else:
        compact = compact.replace(",", ".")
    try:
        return float(compact)
    except ValueError:
        return None


def parse_euro_price(value: str | None) -> float | None:
    if not value:
        return None
    cleaned = (
        unicodedata.normalize("NFKC", value)
        .replace("\xa0", " ")
        .replace("\u202f", " ")
    )
    # Foreign storefronts (Temu PH/US) must not be treated as euros.
    if re.search(r"[₱$£¥₩₹]|PHP|USD|GBP|JPY", cleaned, flags=re.I) and not re.search(
        r"€|EUR", cleaned, flags=re.I
    ):
        return None

    # Temu France writes the current price as 12€99, often split across spans.
    # Use the last match so a strikethrough "was 29€99 now 12€99" keeps the live price.
    split_matches = list(
        re.finditer(r"(?<!\d)(\d{1,4})\s*€\s*(\d{1,2})(?!\d)", cleaned)
    )
    if split_matches:
        split_match = split_matches[-1]
        return float(f"{int(split_match.group(1))}.{split_match.group(2).zfill(2)}")

    marked_matches = list(
        re.finditer(
            r"(?:€\s*)(\d{1,4}(?:[ .]\d{3})*(?:[,.]\d{1,2})?)"
            r"|"
            r"(?<!\d)(\d{1,4}(?:[ .]\d{3})*(?:[,.]\d{1,2})?)\s*(?:€|EUR\b)",
            cleaned,
            flags=re.I,
        )
    )
    if marked_matches:
        marked = marked_matches[-1]
        return _euro_number_to_float(marked.group(1) or marked.group(2))

    match = re.search(r"(?<!\d)(\d{1,4}(?:[ .]\d{3})*(?:[,.]\d{1,2})?)(?!\d)", cleaned)
    if not match:
        return None
    return _euro_number_to_float(match.group(1))

CANONICAL_LABELS = {
    "tour de poitrine": "Poitrine",
    "poitrine": "Poitrine",
    "buste": "Poitrine",
    "tour de taille": "Tour de taille",
    "taille": "Tour de taille",
    "tour de hanches": "Hanches",
    "hanches": "Hanches",
    "longueur": "Longueur",
    "longueur des manches": "Longueur des manches",
    "longueur de manche": "Longueur des manches",
    "carrure": "Carrure",
    "epaule": "Carrure",
    "epaules": "Carrure",
    "tour de bras": "Tour de bras",
    "tour de poignet": "Tour de poignet",
    "cuisse": "Cuisse",
    "tour de cuisse": "Cuisse",
    "thigh": "Cuisse",
    "entrejambe": "Entrejambe",
    "l'entrejambe": "Entrejambe",
    "longueur de l'entrejambe": "Entrejambe",
    "inseam": "Entrejambe",
    "waist": "Tour de taille",
    "hip": "Hanches",
    "hips": "Hanches",
}
SIZE_LABELS = {"taille", "size", "eu", "fr"}


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return re.sub(r"\s+", " ", "".join(c for c in normalized if not unicodedata.combining(c))).strip()


def normalize_sku(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def candidate_metadata_matches_sku(
    sku: str, candidate: ProductCandidate
) -> bool:
    """Match candidate-owned fields without trusting tracking query parameters."""
    needle = normalize_sku(sku)
    path = unquote(urlparse(candidate.url).path)
    return any(
        needle and needle in normalize_sku(value or "")
        for value in (path, candidate.title, candidate.visible_identifier)
    )


def is_recommendation_url(url: str) -> bool:
    lowered = url.casefold()
    return any(marker in lowered for marker in RECOMMENDATION_URL_MARKERS)


def high_resolution_shein_image_url(url: str) -> str:
    """Replace a SHEIN CDN thumbnail path with its original image path."""
    parsed = urlparse(url)
    upgraded_path = re.sub(
        r"_thumbnail_\d+x\d*(?=\.(?:avif|jpe?g|png|webp)$)",
        "",
        parsed.path,
        flags=re.I,
    )
    return parsed._replace(path=upgraded_path).geturl()


def normalize_product_image_urls(
    values: list[str],
    page_url: str,
    *,
    limit: int | None = None,
) -> list[str]:
    images: list[str] = []
    for value in values:
        url = high_resolution_shein_image_url(urljoin(page_url, value))
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        if (
            parsed.scheme in {"http", "https"}
            and any(token in host for token in ("shein", "ltwebstatic"))
            and not re.search(r"(logo|icon|avatar|sprite|review|user)", parsed.path, re.I)
            and url not in images
        ):
            images.append(url)
        if limit is not None and len(images) >= limit:
            break
    return images


def is_access_denied_response(status: int | None, page_text: str) -> bool:
    return status in {401, 403, 429} or any(
        marker in page_text.casefold() for marker in ACCESS_DENIED_MARKERS
    )


def is_shein_oops_text(page_text: str) -> bool:
    normalized = _plain(page_text).casefold()
    return "oops" in normalized and (
        ("retour a la page" in normalized and "accueil" in normalized)
        or "return to homepage" in normalized
        or "back to homepage" in normalized
    )


def canonical_measurement_label(label: str) -> str:
    cleaned = _plain(label).casefold().strip(" :")
    cleaned = re.sub(r"\s*\((?:cm|in|inch|inches|po)\)\s*$", "", cleaned)
    return CANONICAL_LABELS.get(cleaned, label.strip().strip(":"))


def inches_to_centimetres(value: str) -> str:
    numbers = re.findall(r"\d+(?:[.,]\d+)?", value)
    if not numbers:
        raise ValueError(f"No numeric measurement in {value!r}")
    converted = [round(float(number.replace(",", ".")) * 2.54, 1) for number in numbers]
    output = value
    for source, centimetres in zip(numbers, converted, strict=True):
        replacement = f"{centimetres:g}"
        output = output.replace(source, replacement, 1)
    output = re.sub(r'(?i)(?:\s*(?:inches|inch|in|po)\b|\s*")', "", output).strip()
    return f"{output} cm"


def normalize_measurement(value: str, *, unit_hint: str = "") -> tuple[str, str | None]:
    cleaned = re.sub(r"\s+", " ", value).strip()
    inches = bool(re.search(r'(?i)(?:\bin(?:ches|ch)?\b|\bpo\b|")', f"{cleaned} {unit_hint}"))
    centimetres = bool(re.search(r"(?i)\bcm\b", f"{cleaned} {unit_hint}"))
    if inches and not centimetres:
        original = cleaned if re.search(r'(?i)(?:\bin\b|\bpo\b|")', cleaned) else f"{cleaned} in"
        return inches_to_centimetres(original), original
    if re.search(r"\d", cleaned) and not re.search(r"(?i)\bcm\b", cleaned):
        cleaned = f"{cleaned} cm"
    return cleaned, None


@dataclass(slots=True)
class ParsedMeasurements:
    measurements: dict[str, str]
    originals: dict[str, str]
    table_type: str


def _table_context(table: Tag) -> str:
    fragments: list[str] = []
    for sibling in table.find_all_previous(["h1", "h2", "h3", "h4", "p", "button"], limit=5):
        text = sibling.get_text(" ", strip=True)
        if text:
            fragments.append(text)
    return _plain(" ".join(reversed(fragments))).casefold()


def _table_type(context: str) -> tuple[str, int]:
    if "mesures du produit" in context or "product measurements" in context:
        return "Mesures du produit", 20
    if "mesures du corps" in context or "body measurements" in context:
        return "Mesures du corps", -20
    return "Tableau de mesures", 0


def _clean_cells(row: Tag) -> list[str]:
    return [re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip() for cell in row.find_all(["th", "td"])]


def _looks_like_size_name(value: str) -> bool:
    cleaned = _plain(value).casefold().strip(" :：-–—()")
    if re.fullmatch(r"(?:xxs|xs|s|m|l|xl|xxl|xxxl|[0-5]xl)", cleaned):
        return True
    if re.fullmatch(r"(?:us|eu|fr|uk)\s*(?:xxs|xs|s|m|l|xl|xxl|[0-5]xl|\d{2})", cleaned):
        return True
    if re.fullmatch(r"(?:2[4-9]|3[0-9]|4[0-4])", cleaned):
        return True
    return False


def _fix_implausible_centimetres(
    measurements: dict[str, str],
    originals: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    inch_labels = {
        "Poitrine",
        "Tour de taille",
        "Hanches",
        "Cuisse",
        "Entrejambe",
    }
    fixed = dict(measurements)
    fixed_originals = dict(originals)
    for label, value in measurements.items():
        if label not in inch_labels:
            continue
        numbers = [
            float(number.replace(",", "."))
            for number in re.findall(r"\d+(?:[.,]\d+)?", value)
        ]
        if not numbers or any(number >= 50 for number in numbers):
            continue
        inch_source = re.sub(r"(?i)\s*cm\b", " in", value)
        try:
            fixed[label] = inches_to_centimetres(inch_source)
        except ValueError:
            continue
        fixed_originals.setdefault(label, value)
    return fixed, fixed_originals


def _known_measurement_label(value: str) -> str | None:
    cleaned = _plain(value).casefold().strip(" :：-–—")
    cleaned = re.sub(r"\s*\((?:cm|in|inch|inches|po)\)\s*$", "", cleaned)
    if cleaned in CANONICAL_LABELS:
        return CANONICAL_LABELS[cleaned]
    for canonical in set(CANONICAL_LABELS.values()):
        if cleaned == _plain(canonical).casefold():
            return canonical
    return None


def _extract_measurement_pairs(container: Tag) -> tuple[dict[str, str], dict[str, str]]:
    tokens = [
        re.sub(r"\s+", " ", token).strip()
        for token in container.stripped_strings
        if token.strip()
    ]
    context = " ".join(tokens[:30])
    measurements: dict[str, str] = {}
    originals: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        label: str | None = None
        raw_value: str | None = None
        header_source = token

        combined = re.match(
            r"^(.+?)\s*[:：]\s*((?:env\.?\s*)?\d.+)$",
            token,
            re.I,
        )
        if combined:
            header_source = combined.group(1)
            label = _known_measurement_label(combined.group(1))
            raw_value = combined.group(2)
        else:
            inline = re.match(
                r"^(.+?)\s+((?:env\.?\s*)?\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?\s*(?:cm|in|inch|inches|po|\")?)$",
                token,
                re.I,
            )
            if inline:
                header_source = inline.group(1)
                label = _known_measurement_label(inline.group(1))
                raw_value = inline.group(2)

        if not label:
            label = _known_measurement_label(token)
            if label and index + 1 < len(tokens) and re.search(r"\d", tokens[index + 1]):
                raw_value = tokens[index + 1]
                if (
                    index + 2 < len(tokens)
                    and re.fullmatch(r"(?i)cm|in|inch|inches|po", tokens[index + 2])
                    and not re.search(r"(?i)cm|in|inch|inches|po|\"", raw_value)
                ):
                    raw_value = f"{raw_value} {tokens[index + 2]}"
                    index += 1
                index += 1

        if label and raw_value:
            header = _plain(header_source).casefold().strip(" :：")
            if header in SIZE_LABELS and _looks_like_size_name(raw_value):
                index += 1
                continue
            value, original = normalize_measurement(raw_value, unit_hint=context)
            measurements[label] = value
            if original:
                originals[label] = original
        index += 1
    return _fix_implausible_centimetres(measurements, originals)


def parse_hovered_size_measurements(html: str, size: str = "S") -> ParsedMeasurements:
    """Parse the temporary product-measurement panel shown while a size is hovered."""
    soup = BeautifulSoup(html, "html.parser")
    best: tuple[int, int, dict[str, str], dict[str, str]] | None = None
    for container in soup.find_all(True):
        if not isinstance(container, Tag):
            continue
        measurements, originals = _extract_measurement_pairs(container)
        if not measurements:
            continue
        text_length = len(container.get_text(" ", strip=True))
        score = (len(measurements), -text_length)
        if best is None or score > (best[0], best[1]):
            best = (score[0], score[1], measurements, originals)
    if best:
        return ParsedMeasurements(
            measurements=best[2],
            originals=best[3],
            table_type=f"Mesures du produit — Taille {size.upper()} (survol)",
        )
    raise ExtractionError(
        "measurements_unavailable",
        f"The Size {size.upper()} hover panel appeared, but its product measurements could not be read.",
        status_code=422,
    )


def parse_embedded_size_measurements(html: str, size: str = "S") -> ParsedMeasurements:
    """Read the same product data used to render SHEIN's hover card for one size."""
    letter = size.strip().upper()
    candidates: list[tuple[int, ParsedMeasurements]] = []
    object_pattern = re.compile(
        rf'\{{\s*[^{{}}]{{0,8000}}"attr_value_name"\s*:\s*"[^"]*{re.escape(letter)}[^"]*"[^{{}}]{{0,8000}}\}}',
        re.I,
    )
    metadata_keys = {
        "attr_id",
        "attr_name",
        "attr_value_id",
        "attr_value_name",
        "attr_value_name_en",
    }
    size_token = re.compile(
        rf"(?:^|\s){re.escape(letter.casefold())}(?:$|\s|\b|\s*\()"
    )
    for match in object_pattern.finditer(html):
        try:
            item = json.loads(match.group(0))
        except (TypeError, ValueError):
            continue
        size_name = _plain(
            str(item.get("attr_value_name") or item.get("attr_value_name_en") or "")
        ).casefold()
        if letter == "L" and re.search(r"\b(?:x{1,2}l|[2-5]xl)\b", size_name):
            continue
        if not size_token.search(size_name):
            continue
        measurements: dict[str, str] = {}
        originals: dict[str, str] = {}
        centimetre_values = 0
        for raw_label, raw_value in item.items():
            if raw_label in metadata_keys or not isinstance(raw_value, str):
                continue
            if not re.search(r"\d", raw_value):
                continue
            if not re.search(r'(?i)\bcm\b|\bin(?:ch|ches)?\b|\bpo\b|"', raw_value):
                continue
            label = canonical_measurement_label(raw_label)
            value, original = normalize_measurement(raw_value, unit_hint=raw_label)
            measurements[label] = value
            if original:
                originals[label] = original
            if re.search(r"(?i)\bcm\b", raw_value):
                centimetre_values += 1
        measurements, originals = _fix_implausible_centimetres(measurements, originals)
        if measurements:
            candidates.append(
                (
                    centimetre_values * 10 + len(measurements),
                    ParsedMeasurements(
                        measurements=measurements,
                        originals=originals,
                        table_type=f"Mesures du produit — Taille {letter}",
                    ),
                )
            )
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    raise ExtractionError(
        "measurements_unavailable",
        f"Size {letter} product measurements were not present in the loaded product data.",
        status_code=422,
    )


def parse_embedded_size_s_measurements(html: str) -> ParsedMeasurements:
    return parse_embedded_size_measurements(html, "S")


def _extract_horizontal(rows: list[list[str]], context: str) -> ParsedMeasurements | None:
    if len(rows) < 2:
        return None
    headers = rows[0]
    possible_size_indices = [
        index
        for index, header in enumerate(headers)
        if _plain(header).casefold().strip(" :") in SIZE_LABELS
    ]
    size_index = next(
        (
            index
            for index in possible_size_indices
            if any(
                len(row) > index and _plain(row[index]).casefold() == "s"
                for row in rows[1:]
            )
        ),
        possible_size_indices[0] if possible_size_indices else 0,
    )
    size_row = next(
        (
            row
            for row in rows[1:]
            if len(row) > size_index and _plain(row[size_index]).casefold() == "s"
        ),
        None,
    )
    if not size_row:
        return None
    measurements: dict[str, str] = {}
    originals: dict[str, str] = {}
    for index, raw_value in enumerate(size_row):
        if index == size_index or index >= len(headers) or not raw_value:
            continue
        header = headers[index]
        if _plain(header).casefold() in SIZE_LABELS:
            continue
        label = canonical_measurement_label(header)
        value, original = normalize_measurement(raw_value, unit_hint=header)
        measurements[label] = value
        if original:
            originals[label] = original
    table_type, _ = _table_type(context)
    return ParsedMeasurements(measurements, originals, table_type) if measurements else None


def _extract_vertical(rows: list[list[str]], context: str) -> ParsedMeasurements | None:
    if len(rows) < 2:
        return None
    size_column = next(
        (
            index
            for index, cell in enumerate(rows[0])
            if _plain(cell).casefold() == "s"
        ),
        None,
    )
    if size_column is None or size_column == 0:
        return None
    measurements: dict[str, str] = {}
    originals: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) <= size_column or not row[0] or not row[size_column]:
            continue
        label = canonical_measurement_label(row[0])
        value, original = normalize_measurement(row[size_column], unit_hint=row[0])
        measurements[label] = value
        if original:
            originals[label] = original
    table_type, _ = _table_type(context)
    return ParsedMeasurements(measurements, originals, table_type) if measurements else None


def parse_size_s_measurements(html: str) -> ParsedMeasurements:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    candidates: list[tuple[int, ParsedMeasurements]] = []
    saw_other_size = False
    for table in tables:
        rows = [_clean_cells(row) for row in table.find_all("tr")]
        rows = [row for row in rows if row]
        flattened = {_plain(cell).casefold() for row in rows for cell in row}
        saw_other_size = saw_other_size or bool(flattened & {"xs", "m", "l", "xl"})
        context = _table_context(table)
        _, score = _table_type(context)
        for parser in (_extract_horizontal, _extract_vertical):
            parsed = parser(rows, context)
            if parsed:
                candidates.append((score + len(parsed.measurements), parsed))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]
    if tables and saw_other_size:
        raise ExtractionError(
            "size_s_missing",
            "The size guide was found, but it does not contain Size S.",
            status_code=422,
        )
    raise ExtractionError(
        "measurements_unavailable",
        "No Size S product measurements could be read from the size guide.",
        status_code=422,
    )


class SheinExtractor:
    """Owns a persistent Playwright context on one dedicated worker thread."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._owner_thread: int | None = None
        self._playwright: Any = None
        self._context: Any = None
        self._verification_page: Any = None

    def _assert_thread(self) -> None:
        current = threading.get_ident()
        if self._owner_thread is None:
            self._owner_thread = current
        elif self._owner_thread != current:
            raise RuntimeError("SheinExtractor must run on its dedicated browser thread")

    def _context_is_alive(self) -> bool:
        if not self._context:
            return False
        try:
            browser = self._context.browser
            return browser is not None and browser.is_connected()
        except Exception:
            return False

    def _discard_browser(self) -> None:
        """Forget a closed browser and stop its Playwright driver safely."""
        if self._context:
            try:
                self._context.close()
            except Exception:
                logger.debug("Discarding an already closed browser context")
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                logger.debug("Discarding an already stopped Playwright driver")
        self._context = None
        self._playwright = None
        self._verification_page = None

    def _launch_context(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            browser_args = ["--disable-blink-features=AutomationControlled"]
            if self.settings.playwright_background and not self.settings.playwright_headless:
                browser_args.extend(
                    [
                        "--start-minimized",
                        "--window-position=-32000,-32000",
                        "--disable-background-timer-throttling",
                        "--disable-backgrounding-occluded-windows",
                        "--disable-renderer-backgrounding",
                    ]
                )
            launch_options: dict[str, Any] = {
                "user_data_dir": str(self.settings.browser_profile_path),
                "headless": self.settings.playwright_headless,
                "locale": "fr-FR",
                "viewport": {"width": 1600, "height": 1100},
                "device_scale_factor": 2,
                "accept_downloads": True,
                "args": browser_args,
            }
            executable_path = self._browser_executable_path()
            if executable_path:
                launch_options["executable_path"] = executable_path
            self._context = self._playwright.chromium.launch_persistent_context(
                **launch_options,
            )
            self._context.set_default_timeout(self.settings.browser_timeout_ms)
            return self._context
        except Exception as exc:
            self._discard_browser()
            raise ExtractionError(
                "browser_launch_failure",
                "Chromium could not be started. Run “playwright install chromium” and try again.",
                status_code=503,
                details={"reason": type(exc).__name__},
                retryable=True,
            ) from exc

    def _browser_executable_path(self) -> str | None:
        """Allow a store-specific extractor to choose a Chromium browser."""
        return None

    def _ensure_context(self) -> Any:
        self._assert_thread()
        if self._context_is_alive():
            return self._context
        if self._context or self._playwright:
            logger.info("Browser was closed; starting a fresh persistent context")
            self._discard_browser()
        return self._launch_context()

    def _new_page(self) -> Any:
        """Open a page, rebuilding Chromium once if the user closed its window."""
        for attempt in range(2):
            context = self._ensure_context()
            if self._verification_page:
                try:
                    if not self._verification_page.is_closed():
                        page = self._verification_page
                        self._verification_page = None
                        return page
                except Exception:
                    self._verification_page = None
            try:
                return context.new_page()
            except Exception as exc:
                if "TargetClosed" not in type(exc).__name__ and "closed" not in str(exc).casefold():
                    raise
                logger.warning("Chromium closed before a page could open; relaunching")
                self._discard_browser()
                if attempt == 1:
                    raise ExtractionError(
                        "browser_closed",
                        "The Chromium window closed before extraction began. Click Retry to reopen it.",
                        status_code=409,
                        retryable=True,
                    ) from exc
        raise AssertionError("Unreachable browser recovery state")

    def close(self) -> None:
        self._assert_thread()
        self._discard_browser()

    def _accept_cookies(self, page: Any) -> None:
        for text in COOKIE_BUTTON_TEXTS:
            try:
                button = page.get_by_role("button", name=re.compile(re.escape(text), re.I))
                if button.count() and button.first.is_visible():
                    button.first.click(timeout=2_000)
                    return
            except Exception:
                continue

    def _page_text(self, page: Any) -> str:
        try:
            return page.locator("body").inner_text(timeout=5_000).casefold()
        except Exception:
            return ""

    def _raise_if_access_denied(self, page: Any, status: int | None = None) -> None:
        if not is_access_denied_response(status, self._page_text(page)):
            return
        raise ExtractionError(
            "shein_access_denied",
            "SHEIN blocked the automated request (HTTP 403). Set "
            "PLAYWRIGHT_BACKGROUND=false, restart the app, complete any "
            "verification in Chromium, then retry.",
            status_code=403,
            retryable=True,
        )

    def _goto(self, page: Any, url: str, *, wait_until: str = "domcontentloaded") -> Any:
        response = page.goto(url, wait_until=wait_until)
        status = response.status if response is not None else None
        self._raise_if_access_denied(page, status)
        return response

    def _has_captcha(self, page: Any) -> bool:
        for selector in CAPTCHA_SELECTORS:
            try:
                locator = page.locator(selector)
                for index in range(min(locator.count(), 5)):
                    if locator.nth(index).is_visible():
                        return True
            except Exception:
                continue

        texts = [self._page_text(page)]
        for frame in getattr(page, "frames", []):
            try:
                texts.append(frame.locator("body").inner_text(timeout=1_000))
            except Exception:
                continue
        text = _plain(" ".join(texts)).casefold()
        return any(marker in text for marker in CAPTCHA_MARKERS)

    def _raise_if_login_required(self, page: Any) -> None:
        text = self._page_text(page)
        if any(marker in text for marker in LOGIN_MARKERS):
            raise ExtractionError(
                "login_required",
                "SHEIN requires a signed-in session. Sign in in the opened browser, then retry.",
                status_code=409,
                retryable=True,
            )

    def _set_verification_window_visible(self, page: Any, visible: bool) -> bool:
        try:
            session = self._context.new_cdp_session(page)
            window = session.send("Browser.getWindowForTarget")
            window_id = window["windowId"]
            if visible:
                session.send(
                    "Browser.setWindowBounds",
                    {"windowId": window_id, "bounds": {"windowState": "normal"}},
                )
                session.send(
                    "Browser.setWindowBounds",
                    {
                        "windowId": window_id,
                        "bounds": {
                            "left": 80,
                            "top": 80,
                            "width": 1440,
                            "height": 960,
                        },
                    },
                )
                try:
                    page.bring_to_front()
                except Exception:
                    logger.debug("Could not focus the verification tab")
            else:
                session.send(
                    "Browser.setWindowBounds",
                    {"windowId": window_id, "bounds": {"windowState": "minimized"}},
                )
            session.detach()
            return True
        except Exception:
            logger.exception("Could not change the Chromium verification window state")
            return False

    def _wait_for_manual_verification(self, page: Any) -> None:
        if not self._has_captcha(page):
            return
        logger.warning("CAPTCHA detected; waiting for manual completion")
        if self.settings.playwright_headless:
            raise ExtractionError(
                "captcha_detected",
                "SHEIN requested verification. Set PLAYWRIGHT_HEADLESS=false, restart, and retry.",
                status_code=409,
                retryable=True,
            )
        was_background = self.settings.playwright_background
        if was_background and not self._set_verification_window_visible(page, True):
            raise ExtractionError(
                "captcha_detected",
                "SHEIN requested verification. Set PLAYWRIGHT_BACKGROUND=false, "
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
            "Complete the verification in the opened Chromium window, then click Retry.",
            status_code=409,
            retryable=True,
        )

    def _debug_capture(self, page: Any, sku: str, suffix: str, force: bool = False) -> None:
        if not (self.settings.debug or force):
            return
        try:
            if page.is_closed():
                return
        except Exception:
            # The browser may have been intentionally released before opening
            # a normal verification/search window.
            return
        safe_sku = re.sub(r"[^a-zA-Z0-9._-]", "_", sku)[:60]
        stamp = int(time.time())
        stem = self.settings.debug_path / f"{safe_sku}-{stamp}-{suffix}"
        try:
            stem.with_suffix(".html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
        except Exception:
            logger.exception("Could not save extraction debug artifacts")

    def _find_search_input(self, page: Any) -> Any:
        for selector in SEARCH_SELECTORS:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 5)):
                candidate = locator.nth(index)
                try:
                    if candidate.is_visible() and candidate.is_enabled():
                        return candidate
                except Exception:
                    continue
        raise ExtractionError(
            "search_field_not_found",
            "The SHEIN search field could not be found. The site layout may have changed.",
            status_code=502,
            retryable=True,
        )

    def _collect_candidates(self, page: Any) -> list[ProductCandidate]:
        found: dict[str, ProductCandidate] = {}
        for selector in PRODUCT_LINK_SELECTORS:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 40)):
                link = locator.nth(index)
                try:
                    href = link.get_attribute("href")
                    if not href:
                        continue
                    url = urljoin(self.settings.shein_base_url, href)
                    parsed = urlparse(url)
                    if not parsed.hostname or not parsed.hostname.endswith("shein.com"):
                        continue
                    if is_recommendation_url(url):
                        continue
                    title = (
                        link.get_attribute("aria-label")
                        or link.get_attribute("title")
                        or link.inner_text(timeout=1_000)
                        or "SHEIN product"
                    ).strip()
                    container = link.locator(
                        "xpath=ancestor::*[@data-sku or @data-goods-id "
                        "or @data-product-id][1]"
                    )
                    identifier = None
                    image_url = None
                    if container.count():
                        identifier = (
                            container.first.get_attribute("data-sku")
                            or container.first.get_attribute("data-goods-id")
                            or container.first.get_attribute("data-product-id")
                        )
                    image = link.locator("img")
                    if image.count():
                        image_url = (
                            image.first.get_attribute("data-src")
                            or image.first.get_attribute("src")
                        )
                        if image_url:
                            image_url = urljoin(self.settings.shein_base_url, image_url)
                    found[url] = ProductCandidate(
                        title=re.sub(r"\s+", " ", title)[:240],
                        url=url,
                        image_url=image_url,
                        visible_identifier=identifier,
                    )
                except Exception:
                    continue
        return list(found.values())

    def _is_empty_result_page(self, page: Any) -> bool:
        text = self._page_text(page)
        normalized = _plain(text).casefold()
        if any(_plain(marker).casefold() in normalized for marker in EMPTY_RESULT_MARKERS):
            return True
        for selector in (
            '[class*="otherListEmptyRecommend" i]',
            '[class*="empty-result" i]',
            '[data-testid*="empty-result" i]',
        ):
            try:
                locator = page.locator(selector)
                if any(
                    locator.nth(index).is_visible()
                    for index in range(min(locator.count(), 5))
                ):
                    return True
            except Exception:
                continue
        return False

    def _is_shein_oops_page(self, page: Any) -> bool:
        return is_shein_oops_text(self._page_text(page))

    def _wait_for_search_candidates(
        self, page: Any, *, timeout_ms: int = 15_000
    ) -> list[ProductCandidate]:
        deadline = time.monotonic() + timeout_ms / 1_000
        while time.monotonic() < deadline:
            self._raise_if_access_denied(page)
            self._wait_for_manual_verification(page)
            self._raise_if_login_required(page)
            if self._is_shein_oops_page(page):
                raise ExtractionError(
                    "shein_search_oops",
                    "SHEIN returned its OOPS page instead of SKU search results.",
                    status_code=502,
                    retryable=True,
                )
            candidates = self._collect_candidates(page)
            if candidates:
                return candidates
            if self._is_empty_result_page(page):
                return []
            page.wait_for_timeout(200)
        raise ExtractionError(
            "search_results_timeout",
            "SHEIN's search page did not finish loading its results. Retrying the "
            "same SKU usually resolves this temporary page-loading problem.",
            status_code=504,
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
                if self._has_captcha(page):
                    self._wait_for_manual_verification(page)
            page.wait_for_timeout(200)
        raise ExtractionError(
            "product_page_timeout",
            "The SHEIN product page did not finish loading. The extractor will "
            "retry the product page once before giving up.",
            status_code=504,
            retryable=True,
        )

    def _search_url(self, sku: str) -> str:
        encoded = quote(sku.strip(), safe="")
        return (
            f"{self.settings.shein_base_url}/pdsearch/{encoded}/"
            f"?ici=s1%60EditSearch%60{encoded}%60_fb%60d0%60PageHome"
            f"&src_identifier=st%3D2%60sc%3D{encoded}%60sr%3D0%60ps%3D1"
        )

    def _url_or_text_matches(self, sku: str, *values: str | None) -> bool:
        needle = normalize_sku(sku)
        return any(needle and needle in normalize_sku(value or "") for value in values)

    def _primary_product_sku_matches(
        self, page: Any, sku: str, *, timeout_ms: int = 5_000
    ) -> bool:
        selectors = (
            (
                '.product-intro__head-sku-copy[data-clipboard-text]',
                "data-clipboard-text",
            ),
            ('.product-intro__head-sku-text', None),
            (
                '[data-clipboard-text][aria-label*="SKU" i]',
                "data-clipboard-text",
            ),
            ('meta[itemprop="sku"]', "content"),
        )
        deadline = time.monotonic() + timeout_ms / 1_000
        while time.monotonic() < deadline:
            values: list[str] = []
            for selector, attribute in selectors:
                locator = page.locator(selector)
                for index in range(min(locator.count(), 5)):
                    try:
                        value = (
                            locator.nth(index).get_attribute(attribute)
                            if attribute
                            else locator.nth(index).inner_text(timeout=1_000)
                        )
                        if value:
                            values.append(value)
                    except Exception:
                        continue
            if values:
                return self._url_or_text_matches(sku, *values)
            page.wait_for_timeout(150)
        return False

    def _select_exact_product(
        self, page: Any, sku: str, candidates: list[ProductCandidate]
    ) -> tuple[Any, ProductCandidate]:
        likely = [
            candidate
            for candidate in candidates
            if candidate_metadata_matches_sku(sku, candidate)
        ]
        candidates_to_verify = likely or candidates[:1]
        for candidate in candidates_to_verify:
            probe = self._context.new_page()
            matched = False
            keep_probe = False
            try:
                self._goto(probe, candidate.url)
                self._wait_for_manual_verification(probe)
                self._raise_if_login_required(probe)
                self._wait_for_product_ready(probe)
                if self._primary_product_sku_matches(probe, sku):
                    matched = True
                    return probe, candidate
            except ExtractionError as exc:
                if exc.code == "captcha_detected":
                    keep_probe = True
                    self._verification_page = probe
                raise
            except Exception:
                logger.debug("Candidate probe failed: %s", candidate.url, exc_info=True)
            finally:
                if not matched and not keep_probe and not probe.is_closed():
                    probe.close()

        raise ExtractionError(
            "sku_not_found",
            "No product found.",
            status_code=404,
        )

    def _extract_title(self, page: Any) -> str:
        selectors = (
            "h1",
            '[data-testid*="product-title" i]',
            '[class*="product-intro__head-name"]',
            'meta[property="og:title"]',
        )
        for selector in selectors:
            locator = page.locator(selector)
            if not locator.count():
                continue
            value = (
                locator.first.get_attribute("content")
                if selector.startswith("meta")
                else locator.first.inner_text()
            )
            if value and value.strip():
                return re.sub(r"\s+", " ", value).strip()
        raise ExtractionError(
            "product_title_unavailable",
            "The product page opened, but its title could not be read.",
            status_code=502,
        )

    def _extract_images(self, page: Any) -> list[str]:
        raw: list[str] = page.evaluate(
            """
            () => {
              const values = [];
              const add = (value) => {
                if (!value || typeof value !== 'string') return;
                value.split(',').forEach(part => {
                  const url = part.trim().split(/\\s+/)[0];
                  if (url) values.push(url);
                });
              };
              document.querySelectorAll(
                'meta[property="og:image"], meta[name="twitter:image"]'
              ).forEach(el => add(el.content));
              document.querySelectorAll(
                '[class*="product-intro__main"] img,'
                + '[class*="product-intro__thumb"] img,'
                + '[class*="product-intro__gallery"] img,'
                + '[class*="product-intro"] img,'
                + 'main [class*="goods"] img'
              ).forEach(img => {
                ['data-origin-src','data-original','data-src','src','srcset']
                  .forEach(attr => add(img.getAttribute(attr)));
              });
              document.querySelectorAll('script[type="application/ld+json"]')
                .forEach(script => {
                  try {
                    const data = JSON.parse(script.textContent);
                    const items = Array.isArray(data) ? data : [data];
                    items.forEach(item => {
                      const images = Array.isArray(item.image) ? item.image : [item.image];
                      images.forEach(add);
                    });
                  } catch (_) {}
                });
              return values;
            }
            """
        )
        return normalize_product_image_urls(raw, page.url)

    def _screenshot_main_image(self, page: Any, sku: str) -> str:
        selectors = (
            'img[aria-label="Goods Image"]',
            'main img[class*="main" i]',
            '[class*="product-intro"] img',
            '[class*="goods"] img',
        )
        safe_sku = re.sub(r"[^a-zA-Z0-9._-]", "_", sku)[:60]
        target = self.settings.downloads_path / f"{safe_sku}-product-image.png"
        best_image: Any = None
        best_score = 0.0
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 10)):
                image = locator.nth(index)
                try:
                    image.scroll_into_view_if_needed(timeout=3_000)
                    box = image.bounding_box()
                    dimensions = image.evaluate(
                        """
                        image => ({
                          naturalWidth: image.naturalWidth || 0,
                          naturalHeight: image.naturalHeight || 0
                        })
                        """
                    )
                    if (
                        image.is_visible()
                        and box
                        and box["width"] >= 250
                        and box["height"] >= 250
                    ):
                        natural_area = (
                            dimensions["naturalWidth"] * dimensions["naturalHeight"]
                        )
                        rendered_area = box["width"] * box["height"]
                        score = max(natural_area, rendered_area)
                        if score > best_score:
                            best_image = image
                            best_score = score
                except Exception:
                    continue
        if best_image is not None:
            original_style = best_image.get_attribute("style")
            try:
                natural_width = best_image.evaluate(
                    "image => image.naturalWidth || image.width || 900"
                )
                capture_width = min(max(int(natural_width), 900), 1_200)
                best_image.evaluate(
                    """
                    (image, width) => {
                      image.style.width = `${width}px`;
                      image.style.height = "auto";
                      image.style.maxWidth = "none";
                    }
                    """,
                    capture_width,
                )
                best_image.screenshot(
                    path=str(target),
                    animations="disabled",
                    caret="hide",
                    scale="device",
                )
                return f"/downloads/{target.name}"
            finally:
                try:
                    best_image.evaluate(
                        """
                        (image, style) => {
                          if (style === null) image.removeAttribute("style");
                          else image.setAttribute("style", style);
                        }
                        """,
                        original_style,
                    )
                except Exception:
                    logger.debug("Could not restore captured image styling")
        raise ExtractionError(
            "product_image_unavailable",
            "No original product image or suitable image element could be found.",
            status_code=422,
        )

    def _screenshot_product_images(
        self, sku: str, image_urls: list[str]
    ) -> list[str]:
        """Download original gallery files concurrently for speed and clarity."""
        if not image_urls:
            return []
        safe_sku = re.sub(r"[^a-zA-Z0-9._-]", "_", sku)[:60]
        try:
            browser_cookies = {
                cookie["name"]: cookie["value"]
                for cookie in self._context.cookies(image_urls)
            }
        except Exception:
            browser_cookies = {}

        extension_by_type = {
            "image/avif": "avif",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }

        def download(index: int, image_url: str) -> tuple[int, bytes, str]:
            with httpx.Client(
                cookies=browser_cookies,
                follow_redirects=True,
                timeout=httpx.Timeout(8.0),
                headers={
                    "Referer": f"{self.settings.shein_base_url}/",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
            ) as client:
                response = client.get(image_url)
                response.raise_for_status()
                mime_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .casefold()
                )
                if mime_type not in extension_by_type:
                    raise RuntimeError("Gallery entry was not a supported image")
                image_bytes = response.content
                if not image_bytes or len(image_bytes) > self.settings.max_image_bytes:
                    raise RuntimeError("Gallery image size was invalid")
                return index, image_bytes, extension_by_type[mime_type]

        downloaded: dict[int, tuple[bytes, str]] = {}
        worker_count = min(6, len(image_urls))
        try:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="product-image",
            ) as executor:
                futures = {
                    executor.submit(download, index, image_url): index
                    for index, image_url in enumerate(image_urls, 1)
                }
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        result_index, image_bytes, extension = future.result()
                        downloaded[result_index] = (image_bytes, extension)
                    except Exception as exc:
                        logger.warning(
                            "Skipping unreadable gallery image %s (%s)",
                            index,
                            type(exc).__name__,
                        )
        except Exception:
            logger.exception("Parallel gallery download failed")

        saved_images: list[str] = []
        if downloaded:
            # A refreshed gallery can contain fewer images than the previous
            # capture. Clear the old SKU slots so rejected/recommendation
            # images are not left behind as unused local files.
            for old_target in self.settings.downloads_path.glob(
                f"{safe_sku}-product-image-*.*"
            ):
                try:
                    if old_target.is_file():
                        old_target.unlink()
                except OSError:
                    logger.warning(
                        "Could not remove old gallery capture %s",
                        old_target.name,
                    )
        for index in sorted(downloaded):
            image_bytes, extension = downloaded[index]
            target = self.settings.downloads_path / (
                f"{safe_sku}-product-image-{index}.{extension}"
            )
            try:
                # Remove an older capture of this slot if its format changed.
                for old_extension in ("avif", "jpg", "jpeg", "png", "webp"):
                    old_target = target.with_suffix(f".{old_extension}")
                    if old_target != target and old_target.is_file():
                        old_target.unlink()
                target.write_bytes(image_bytes)
                saved_images.append(f"/downloads/{target.name}")
            except OSError as exc:
                logger.warning(
                    "Could not save gallery image %s (%s)",
                    index,
                    type(exc).__name__,
                )

        # A small number of CDNs require Playwright's authenticated request
        # context. Fall back only when every parallel download was rejected.
        if not saved_images:
            for index, image_url in enumerate(image_urls, 1):
                try:
                    response = self._context.request.get(
                        image_url,
                        headers={
                            "Referer": f"{self.settings.shein_base_url}/",
                            "User-Agent": "Mozilla/5.0",
                        },
                        timeout=5_000,
                    )
                    if not response.ok:
                        raise RuntimeError(f"Image returned HTTP {response.status}")
                    image_bytes = response.body()
                    if len(image_bytes) > self.settings.max_image_bytes:
                        raise RuntimeError("Image exceeded the download limit")
                    mime_type = (
                        response.headers.get("content-type", "image/jpeg")
                        .split(";", 1)[0]
                        .strip()
                        .casefold()
                    )
                    extension = extension_by_type.get(mime_type)
                    if not extension:
                        raise RuntimeError("Gallery entry was not a supported image")
                    target = self.settings.downloads_path / (
                        f"{safe_sku}-product-image-{index}.{extension}"
                    )
                    target.write_bytes(image_bytes)
                    saved_images.append(f"/downloads/{target.name}")
                except Exception as exc:
                    logger.warning(
                        "Skipping unreadable fallback gallery image %s (%s)",
                        index,
                        type(exc).__name__,
                    )
        return saved_images

    def _extract_detail(self, text: str, labels: tuple[str, ...]) -> str | None:
        for label in labels:
            match = re.search(
                rf"(?:^|\n)\s*{re.escape(label)}\s*[:：]?\s*([^\n|]{{1,120}})",
                text,
                re.I,
            )
            if match:
                return match.group(1).strip()
        return None

    def _extract_price_eur(self, page: Any) -> float | None:
        selectors = (
            "#productMainPriceId",
            ".productPrice__main",
            ".product-intro__head-price .discount",
            'meta[property="product:price:amount"]',
            '[itemprop="price"]',
        )
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 4)):
                item = locator.nth(index)
                for attribute in (
                    "aria-label",
                    "content",
                    "data-price",
                    "data-amount",
                ):
                    try:
                        parsed = parse_euro_price(item.get_attribute(attribute))
                    except Exception:
                        parsed = None
                    if parsed is not None:
                        return parsed
                try:
                    parsed = parse_euro_price(item.inner_text(timeout=1_000))
                except Exception:
                    parsed = None
                if parsed is not None:
                    return parsed
        return None

    def _extract_category(self, page: Any) -> str | None:
        selectors = (
            'nav[aria-label*="breadcrumb" i] a',
            '[class*="breadcrumb"] a',
            '[data-testid*="breadcrumb"] a',
        )
        for selector in selectors:
            locator = page.locator(selector)
            values = []
            for index in range(min(locator.count(), 12)):
                try:
                    text = locator.nth(index).inner_text().strip()
                    if text and text.casefold() not in {"accueil", "home"}:
                        values.append(text)
                except Exception:
                    continue
            if values:
                return " › ".join(values)
        return None

    def _visible_measurement_panel_html(
        self,
        page: Any,
        size: str = "S",
        trigger: Any | None = None,
    ) -> str | None:
        script = """
            (trigger) => {
              if (!trigger) return null;
              const triggerRect = trigger.getBoundingClientRect();
              const measurementPattern =
                /poitrine|buste|taille|hanches|longueur|manches?|carrure|bras|poignet|cuisse|entrejambe|waist|hip|inseam|thigh/i;
              const candidates = Array.from(document.querySelectorAll('body *'))
                .filter((element) => {
                  const text = (element.innerText || '').trim();
                  if (
                    !measurementPattern.test(text)
                    || !/(?:\\d+(?:[.,]\\d+)?)\\s*(?:cm|in|pouces?|")/i.test(text)
                    || text.length > 1800
                    || element.closest('[class*="review" i]')
                  ) return false;
                  const style = getComputedStyle(element);
                  const rect = element.getBoundingClientRect();
                  const distance = Math.min(
                    Math.abs(rect.top - triggerRect.bottom),
                    Math.abs(rect.bottom - triggerRect.top),
                    Math.abs(rect.left - triggerRect.right),
                    Math.abs(rect.right - triggerRect.left)
                  );
                  return element !== trigger
                    && !trigger.contains(element)
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) !== 0
                    && rect.width > 0
                    && rect.height > 0
                    && rect.bottom >= 0
                    && rect.top <= window.innerHeight
                    && distance < 650;
                })
                .sort((left, right) => {
                  const preferred = (element) =>
                    element.matches('[role="tooltip"], [class*="tooltip" i], [class*="popover" i], [class*="measure" i]')
                      ? 1 : 0;
                  return preferred(right) - preferred(left)
                    || (left.innerText || '').length - (right.innerText || '').length;
                });
              return candidates.length ? candidates[0].outerHTML : null;
            }
        """
        if trigger is None:
            letter = (size or "S").strip().upper() or "S"
            for selector in size_radio_selectors(letter):
                locator = page.locator(selector)
                try:
                    if locator.count():
                        trigger = locator.first
                        break
                except Exception:
                    continue
        if trigger is None:
            return None
        try:
            return trigger.evaluate(script)
        except Exception:
            logger.debug("Could not read the hovered size measurement panel", exc_info=True)
            return None

    def _measurements_from_size_hover(
        self, page: Any, sku: str, size: str = "S"
    ) -> ParsedMeasurements:
        letter = size.strip().upper() or "S"
        logger.info("Hovering Size %s", letter)
        if self._has_captcha(page):
            try:
                logger.info(
                    "Using loaded Size %s data while verification covers the page",
                    letter,
                )
                return parse_embedded_size_measurements(page.content(), letter)
            except ExtractionError:
                self._wait_for_manual_verification(page)

        selectors = size_radio_selectors(letter)
        try:
            page.locator(",".join(selectors)).first.wait_for(
                state="visible", timeout=3_000
            )
        except Exception:
            logger.debug("Size %s did not become visible within five seconds", letter)

        found_size = False
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 8)):
                size_option = locator.nth(index)
                try:
                    if not size_option.is_visible():
                        continue
                    if size_option.get_attribute("aria-disabled") == "true":
                        continue
                    found_size = True
                    size_option.hover(timeout=3_000)
                    page.wait_for_timeout(350)
                    panel_html = self._visible_measurement_panel_html(
                        page,
                        letter,
                        size_option,
                    )
                    if not panel_html:
                        continue
                    parsed = parse_hovered_size_measurements(panel_html, letter)
                    self._debug_capture(page, sku, f"size-{letter.lower()}-hover")
                    return parsed
                except ExtractionError:
                    logger.debug(
                        "A Size %s hover panel appeared but was not parseable",
                        letter,
                        exc_info=True,
                    )
                except Exception:
                    logger.debug(
                        "Could not read a Size %s hover panel",
                        letter,
                        exc_info=True,
                    )
        try:
            logger.info("Reading Size %s measurements from loaded product data", letter)
            return parse_embedded_size_measurements(page.content(), letter)
        except ExtractionError:
            pass
        if not found_size:
            missing_code = {
                "S": "size_s_missing",
                "M": "size_m_missing",
                "L": "size_l_missing",
            }.get(letter, "size_s_missing")
            raise ExtractionError(
                missing_code,
                f"This product does not offer an enabled Size {letter} option.",
                status_code=422,
            )
        raise ExtractionError(
            "measurements_unavailable",
            f"Size {letter} was found and hovered, but its visible measurement card could not be read.",
            status_code=422,
            retryable=True,
        )

    def _measurements_from_size_s_hover(self, page: Any, sku: str) -> ParsedMeasurements:
        return self._measurements_from_size_hover(page, sku, "S")

    def _open_size_guide(self, page: Any) -> None:
        logger.info("Opening size guide")
        for text in SIZE_GUIDE_TEXTS:
            pattern = re.compile(re.escape(text), re.I)
            for role in ("button", "link"):
                locator = page.get_by_role(role, name=pattern)
                if locator.count():
                    try:
                        locator.first.click()
                        page.wait_for_timeout(800)
                        return
                    except Exception:
                        continue
            locator = page.get_by_text(pattern, exact=False)
            if locator.count():
                try:
                    locator.first.click()
                    page.wait_for_timeout(800)
                    return
                except Exception:
                    continue
        raise ExtractionError(
            "size_guide_not_found",
            "The size guide could not be found for this product.",
            status_code=422,
        )

    def extract(
        self,
        sku: str,
        candidate_url: str | None = None,
        item_type: str = "dress",
    ) -> ExtractedProduct:
        page: Any = None
        keep_page = False
        try:
            page = self._new_page()
            if candidate_url:
                logger.info("Opening user-selected product")
                self._goto(page, candidate_url)
                self._accept_cookies(page)
                self._wait_for_manual_verification(page)
                self._raise_if_login_required(page)
                try:
                    self._wait_for_product_ready(page)
                except ExtractionError as exc:
                    if exc.code != "product_page_timeout":
                        raise
                    logger.info("Product page stayed incomplete; reloading it once")
                    self._goto(page, candidate_url)
                    self._wait_for_product_ready(page)
            else:
                logger.info("Searching SKU: %s", sku)
                candidates: list[ProductCandidate] = []
                search_failure: ExtractionError | None = None
                for attempt in range(2):
                    if attempt:
                        logger.info("Retrying the exact SKU search URL")
                    try:
                        self._goto(page, self._search_url(sku))
                    except Exception as exc:
                        if "Timeout" not in type(exc).__name__:
                            raise
                        logger.warning("SHEIN search navigation timed out")
                        search_failure = ExtractionError(
                            "search_results_timeout",
                            "SHEIN did not finish loading the exact SKU search.",
                            status_code=504,
                            retryable=True,
                        )
                    self._accept_cookies(page)
                    self._wait_for_manual_verification(page)
                    self._raise_if_login_required(page)
                    try:
                        candidates = self._wait_for_search_candidates(page)
                        search_failure = None
                        break
                    except ExtractionError as exc:
                        if exc.code not in {
                            "search_results_timeout",
                            "shein_search_oops",
                        }:
                            raise
                        search_failure = exc
                if search_failure is not None:
                    if search_failure.code == "shein_search_oops":
                        raise ExtractionError(
                            "sku_not_found",
                            "No product found.",
                            status_code=404,
                            retryable=True,
                        )
                    raise search_failure
                if not candidates:
                    raise ExtractionError(
                        "sku_not_found",
                        "No product found.",
                        status_code=404,
                    )

                logger.info("Opening product")
                product_page, _ = self._select_exact_product(page, sku, candidates)
                if product_page != page and not page.is_closed():
                    page.close()
                page = product_page
                page.wait_for_load_state("domcontentloaded")
                product_url = page.url
                try:
                    self._wait_for_product_ready(page)
                except ExtractionError as exc:
                    if exc.code != "product_page_timeout":
                        raise
                    logger.info("Product page stayed incomplete; reloading it once")
                    self._goto(page, product_url)
                    self._wait_for_product_ready(page)

            if not self._primary_product_sku_matches(page, sku):
                raise ExtractionError(
                    "sku_not_found",
                    "No product found.",
                    status_code=404,
                )

            title = self._extract_title(page)
            source_price_eur = self._extract_price_eur(page)
            logger.info("Extracting images")
            images = self._extract_images(page)
            image_is_screenshot = False
            if images:
                captured_images = self._screenshot_product_images(
                    sku, images
                )
                if captured_images:
                    main_image = captured_images[0]
                    additional_images = captured_images[1:]
                    image_is_screenshot = True
                else:
                    main_image = images[0]
                    additional_images = images[1:]
            else:
                main_image = self._screenshot_main_image(page, sku)
                additional_images = []
                image_is_screenshot = True

            body_text = page.locator("body").inner_text()
            colour = self._extract_detail(body_text, ("Couleur", "Coloris", "Color"))
            material = self._extract_detail(
                body_text, ("Composition", "Matière", "Matériau", "Material")
            )
            category = self._extract_category(page)

            size_letter = extraction_size_letter(item_type)
            if size_letter:
                logger.info("Finding Size %s", size_letter)
                parsed = self._measurements_from_size_hover(page, sku, size_letter)
            else:
                logger.info("Skipping size measurements for %s extraction", item_type)
                parsed = ParsedMeasurements(
                    measurements={},
                    originals={},
                    table_type=f"Measurements not required for {item_type}",
                )

            details: dict[str, Any] = {
                "item_type": item_type,
                "store": "shein",
            }
            for label, aliases in {
                "style": ("Style",),
                "pattern": ("Type de motif", "Motif"),
                "season": ("Saison",),
            }.items():
                value = self._extract_detail(body_text, aliases)
                if value:
                    details[label] = value
            price_category = infer_price_category(title, category)
            if source_price_eur is not None:
                details["price_calculator"] = calculate_vinted_price(
                    source_price_eur,
                    price_category,
                )
            else:
                details["price_calculator"] = {
                    "source_price_eur": None,
                    "adjustment_eur": 0.0,
                    "adjusted_price_eur": None,
                    "category": price_category,
                    "category_label": None,
                    "vinted_price_min_eur": None,
                    "vinted_price_max_eur": None,
                    "recommended_price_eur": None,
                    "eligible": False,
                    "message": "The current SHEIN price could not be read.",
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
                additional_details=details,
                image_is_screenshot=image_is_screenshot,
            )
        except ExtractionError as exc:
            keep_page = (
                exc.code == "captcha_detected"
                and not self.settings.playwright_headless
            )
            if page is not None:
                self._debug_capture(page, sku, "failure", force=True)
            if keep_page:
                self._verification_page = page
            raise
        except Exception as exc:
            if page is not None:
                self._debug_capture(page, sku, "failure", force=True)
            name = type(exc).__name__
            if "TargetClosed" in name or "closed" in str(exc).casefold():
                self._discard_browser()
                raise ExtractionError(
                    "browser_closed",
                    "The Chromium window was closed. Click Retry and keep it open while extraction runs.",
                    status_code=409,
                    retryable=True,
                ) from exc
            if "Timeout" in name:
                raise ExtractionError(
                    "shein_timeout",
                    "SHEIN took too long to respond. Please retry.",
                    status_code=504,
                    retryable=True,
                ) from exc
            raise ExtractionError(
                "network_failure",
                "The product could not be extracted because SHEIN or the network returned an unexpected error.",
                status_code=502,
                details={"reason": name},
                retryable=True,
            ) from exc
        finally:
            if not keep_page and page:
                try:
                    if not page.is_closed():
                        page.close()
                except Exception:
                    logger.debug("Extraction page was already closed")
