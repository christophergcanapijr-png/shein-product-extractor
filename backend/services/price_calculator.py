from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True, slots=True)
class PriceBand:
    minimum: Decimal
    maximum: Decimal
    vinted_minimum: Decimal
    vinted_maximum: Decimal


def _band(
    minimum: str, maximum: str, vinted_minimum: str, vinted_maximum: str
) -> PriceBand:
    return PriceBand(
        Decimal(minimum),
        Decimal(maximum),
        Decimal(vinted_minimum),
        Decimal(vinted_maximum),
    )


PRICE_RULES: dict[str, dict[str, Any]] = {
    "baggy_jeans": {
        "label": "Baggy jeans",
        "bands": (
            _band("0", "5", "35.9", "44.9"),
            _band("5", "15", "44.9", "57.9"),
            _band("15", "25", "57.9", "65.9"),
            _band("25", "30", "65.9", "82.9"),
            _band("30", "35", "82.9", "89.9"),
            _band("35", "40", "89.9", "89.9"),
        ),
    },
    "sets": {
        "label": "Sets",
        "bands": (
            _band("0", "5", "39.9", "44.9"),
            _band("5", "15", "44.9", "54.9"),
            _band("15", "25", "54.9", "67.9"),
            _band("25", "33", "68.9", "78.9"),
        ),
    },
    "pants": {
        "label": "Pants",
        "bands": (
            _band("0", "5", "34.9", "36.9"),
            _band("5", "15", "36.9", "39.9"),
            _band("15", "25", "39.9", "49.9"),
            _band("25", "30", "49.9", "59.9"),
        ),
    },
    "heeled_sandal": {
        "label": "Heeled sandal",
        "bands": (
            _band("0", "15", "27.9", "34.9"),
            _band("15", "25", "34.9", "43.9"),
            _band("25", "35", "43.9", "69.9"),
            _band("35", "40", "69.9", "79.9"),
        ),
    },
    "long_boots": {
        "label": "Long boots",
        "bands": (
            _band("0", "10", "27.9", "36.9"),
            _band("10", "20", "36.9", "46.9"),
            _band("20", "30", "46.9", "68.9"),
            _band("30", "35", "68.9", "69.9"),
        ),
    },
    "coats": {
        "label": "Coats",
        "bands": (
            _band("0", "5", "10.9", "16.9"),
            _band("5", "15", "29.9", "44.9"),
            _band("15", "25", "49.9", "69.9"),
            _band("25", "30", "65.9", "89.9"),
            _band("30", "46", "89.9", "119.9"),
        ),
    },
    "dress": {
        "label": "Dress",
        "bands": (
            _band("0", "5", "39.9", "44.9"),
            _band("5", "15", "44.9", "54.9"),
            _band("15", "25", "54.9", "69.9"),
            _band("25", "35", "74.9", "89.9"),
            _band("35", "40", "89.9", "119.9"),
        ),
    },
    "expensive_dress": {
        "label": "Expensive dress",
        "bands": (
            _band("30", "40", "109", "160"),
            _band("40", "50", "160", "190"),
            _band("50", "70", "190", "249"),
            _band("70", "110", "249", "390"),
        ),
    },
    "shorts": {
        "label": "Shorts",
        "bands": (
            _band("0", "5", "34.9", "39.9"),
            _band("5", "15", "39.9", "44.9"),
            _band("15", "25", "44.9", "49.9"),
            _band("25", "30", "49.9", "59.9"),
        ),
    },
    "tops": {
        "label": "Tops / tank tops",
        "bands": (
            _band("0", "5", "24.9", "29.9"),
            _band("5", "10", "29.9", "32.9"),
            _band("10", "15", "32.9", "39.9"),
            _band("15", "25", "39.9", "44.9"),
        ),
    },
    "earrings": {
        "label": "Earrings",
        "bands": (
            _band("0", "3", "28.9", "29.9"),
            _band("3", "5", "29.9", "34.9"),
            _band("5", "10", "34.9", "44.9"),
        ),
    },
    "jackets": {
        "label": "Jackets",
        "bands": (
            _band("0", "5", "44.9", "49.9"),
            _band("5", "15", "49.9", "59.9"),
            _band("15", "25", "59.9", "65.9"),
            _band("25", "33", "65.9", "73.9"),
        ),
    },
    "long_shorts": {
        "label": "Long shorts",
        "bands": (
            _band("0", "5", "34.9", "39.9"),
            _band("5", "15", "39.9", "45.9"),
            _band("15", "25", "45.9", "51.9"),
            _band("25", "33", "51.9", "55.9"),
        ),
    },
    "bracelets": {
        "label": "Bracelets",
        "bands": (
            _band("0", "3", "15.9", "21.9"),
            _band("3", "5", "21.9", "27.9"),
            _band("5", "10", "27.9", "34.9"),
        ),
    },
    "bed_linens": {
        "label": "Bed linens",
        "bands": (
            _band("0", "10", "34.9", "44.9"),
            _band("10", "20", "44.9", "64.9"),
            _band("20", "25", "64.9", "68.9"),
            _band("25", "30", "68.9", "79.9"),
        ),
    },
    "carpet": {
        "label": "Carpet",
        "bands": (
            _band("0", "5", "39.9", "44.9"),
            _band("5", "15", "44.9", "54.9"),
            _band("15", "25", "54.9", "67.9"),
            _band("25", "33", "68.9", "78.9"),
        ),
    },
    "shoulder_bag": {
        "label": "Shoulder bag",
        "bands": (
            _band("0", "5", "24.9", "28.9"),
            _band("5", "15", "26.9", "43.9"),
            _band("15", "19", "43.9", "49.9"),
            _band("19", "25", "49.9", "59.9"),
        ),
    },
    "handbag": {
        "label": "Handbag",
        "bands": (
            _band("0", "5", "24.9", "28.9"),
            _band("5", "15", "26.9", "43.9"),
            _band("15", "19", "43.9", "49.9"),
            _band("19", "25", "49.9", "59.9"),
        ),
    },
    "hats": {
        "label": "Hats",
        "bands": (
            _band("0", "5", "24.8", "31.9"),
            _band("5", "15", "31.9", "49.9"),
            _band("15", "19", "49.9", "54.9"),
            _band("19", "25", "54.9", "64.9"),
        ),
    },
    "watch": {
        "label": "Watch",
        "bands": (
            _band("0", "5", "39.9", "49.9"),
            _band("5", "15", "49.9", "66.9"),
            _band("15", "25", "66.9", "124.9"),
        ),
    },
    "shelf": {
        "label": "Shelf",
        "bands": (
            _band("0", "10", "34.9", "44.9"),
            _band("10", "20", "44.9", "59.9"),
            _band("20", "25", "59.9", "69.9"),
            _band("25", "30", "69.9", "74.9"),
            _band("30", "40", "74.9", "94.9"),
        ),
    },
    "cape_coats": {
        "label": "Cape coats",
        "bands": (
            _band("0", "5", "24.8", "31.9"),
            _band("5", "15", "31.9", "49.9"),
            _band("15", "19", "49.9", "54.9"),
            _band("19", "25", "54.9", "64.9"),
        ),
    },
    "lace_gloves": {
        "label": "Lace gloves",
        "bands": (
            _band("0", "3", "16.9", "18.9"),
            _band("3", "5", "18.9", "21.9"),
        ),
    },
    "skirt": {
        "label": "Skirt",
        "bands": (
            _band("0", "5", "24.9", "29.9"),
            _band("5", "10", "29.9", "34.9"),
            _band("10", "20", "34.9", "44.9"),
            _band("20", "30", "44.9", "59.9"),
        ),
    },
    "plant": {
        "label": "Plant",
        "bands": (
            _band("0", "5", "29.9", "37.9"),
            _band("10", "15", "37.9", "49.9"),
            _band("15", "20", "49.9", "64.9"),
            _band("20", "30", "64.9", "79.9"),
        ),
    },
    "nightstand": {
        "label": "Nightstand",
        "bands": (
            _band("0", "10", "39.9", "44.9"),
            _band("10", "20", "44.9", "64.9"),
            _band("20", "30", "64.9", "89.9"),
            _band("30", "40", "89.9", "129.9"),
        ),
    },
    "mask": {
        "label": "Mask",
        "bands": (
            _band("0", "10", "24.9", "39.9"),
            _band("10", "20", "39.9", "49.9"),
            _band("20", "30", "49.9", "79.9"),
            _band("30", "40", "89.9", "129.9"),
        ),
    },
    "necklace": {
        "label": "Necklace",
        "bands": (
            _band("0", "5", "14.9", "27.9"),
            _band("5", "10", "27.9", "33.9"),
        ),
    },
    "cat_toy": {
        "label": "Cat toy",
        "bands": (
            _band("0", "10", "27.9", "37.9"),
            _band("10", "20", "37.9", "49.9"),
            _band("20", "30", "49.9", "73.9"),
            _band("30", "40", "73.9", "94.9"),
            _band("40", "50", "94.9", "109.9"),
            _band("50", "60", "109.9", "139.9"),
        ),
    },
    "belt": {
        "label": "Belt",
        "bands": (
            _band("0", "5", "19.9", "27.9"),
            _band("5", "10", "27.9", "37.9"),
            _band("10", "20", "37.9", "44.9"),
            _band("20", "30", "44.9", "79.9"),
        ),
    },
    "mirror": {
        "label": "Mirror",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "20", "39.9", "62.9"),
            _band("20", "30", "62.9", "79.9"),
        ),
    },
    "suspended_decorations": {
        "label": "Suspended decorations",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "20", "39.9", "59.9"),
            _band("20", "30", "59.9", "69.9"),
        ),
    },
    "lamp": {
        "label": "Lamp",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "20", "39.9", "59.9"),
            _band("20", "30", "59.9", "69.9"),
        ),
    },
    "organizer": {
        "label": "Organizer",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "30", "39.9", "79.9"),
        ),
    },
    "curtains": {
        "label": "Curtains",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "20", "39.9", "59.9"),
            _band("20", "30", "59.9", "69.9"),
        ),
    },
    "sculpture": {
        "label": "Sculpture",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "30", "39.9", "79.9"),
        ),
    },
    "cushion": {
        "label": "Cushion",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "20", "39.9", "59.9"),
            _band("20", "30", "59.9", "69.9"),
        ),
    },
    "lace_umbrella": {
        "label": "Lace umbrella",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "20", "39.9", "59.9"),
            _band("20", "30", "59.9", "69.9"),
        ),
    },
    "jewelry_box": {
        "label": "Jewelry box",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "20", "39.9", "59.9"),
            _band("20", "30", "59.9", "69.9"),
        ),
    },
    "faux_fur_leg_warmer": {
        "label": "Faux fur leg warmer",
        "bands": (
            _band("0", "5", "24.9", "32.9"),
            _band("5", "10", "32.9", "39.9"),
            _band("10", "20", "39.9", "59.9"),
            _band("20", "30", "59.9", "69.9"),
        ),
    },
}


def _plain(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold()


def infer_price_category(title: str | None, category: str | None) -> str | None:
    text = _plain(" ".join(filter(None, (title, category))))
    patterns = (
        ("earrings", r"\bearrings?\b|boucles?\s+d[' ]oreilles?"),
        ("bracelets", r"\bbracelets?\b"),
        ("necklace", r"\bnecklaces?\b|\bcolliers?\b"),
        ("watch", r"\bwatches?\b|\bmontres?\b"),
        ("belt", r"\bbelts?\b|\bceintures?\b"),
        ("lace_gloves", r"lace\s+gloves?|gants?\s+(?:en\s+)?dentelle"),
        ("lace_umbrella", r"lace\s+umbrellas?|parapluies?\s+(?:en\s+)?dentelle"),
        ("jewelry_box", r"jewel(?:le)?ry\s*box(?:es)?|bo[iî]tes?\s+[àa]\s+bijoux"),
        ("faux_fur_leg_warmer", r"(?:faux\s*fur\s+)?leg\s*warmers?|jambi[eè]res?\s+(?:en\s+)?fausse\s+fourrure"),
        ("cape_coats", r"\bcapes?\b|\bponchos?\b"),
        ("bed_linens", r"bed\s*linen|bedding|linge\s+de\s+lit|draps?|housses?\s+de\s+couette"),
        ("carpet", r"\bcarpets?\b|\brugs?\b|\btapis\b"),
        ("nightstand", r"\bnight\s*stands?\b|\bbedside\s+tables?\b|tables?\s+de\s+nuit|\bchevets?\b"),
        ("mask", r"\bmasks?\b|\bmasques?\b|\bmasquerade\b|\bmascarade\b"),
        ("shelf", r"\bshelves?\b|\bshelf\b|\betageres?\b"),
        ("cat_toy", r"\bcat\s*toys?\b|jouets?\s+(?:pour\s+)?chats?\b"),
        ("mirror", r"\bmirrors?\b|\bmiroirs?\b"),
        ("lamp", r"\blamps?\b|\blampes?\b"),
        ("curtains", r"\bcurtains?\b|\brideaux?\b"),
        ("cushion", r"\bcushions?\b|\bpillows?\b|\bcoussins?\b"),
        ("organizer", r"\borganizers?\b|\borganiseurs?\b|\brangements?\b"),
        ("sculpture", r"\bsculptures?\b|\bstatues?\b|\bstatuettes?\b"),
        ("suspended_decorations", r"suspended\s+decorations?|hanging\s+decorations?|d[ée]corations?\s+suspendues?"),
        ("plant", r"\bplants?\b|\bplantes?\b"),
        ("long_boots", r"knee[- ]high\s+boots?|thigh[- ]high\s+boots?|long\s+boots?|bottes?\s+hautes?|bottes?\s+longues?|\bcuissardes?\b|\bboots?\b|\bbottes?\b"),
        ("heeled_sandal", r"heeled\s+sandals?|\bsandales?\s+a\s+talons?\b|\btalons?\b"),
        ("shoulder_bag", r"shoulder\s*bag|crossbody|sac\s+(?:a\s+)?bandouliere|sac\s+d[' ]epaule"),
        ("handbag", r"\bhandbags?\b|sacs?\s+a\s+main|\btote\b"),
        ("baggy_jeans", r"baggy.*jeans?|jeans?.*baggy"),
        ("pants", r"\bpants?\b|\btrousers?\b|\bpantalons?\b|\bjeans?\b"),
        ("long_shorts", r"long\s+shorts?|\bbermudas?\b"),
        ("shorts", r"\bshorts?\b"),
        ("tops", r"\btops?\b|tank\s*tops?|\bt-?shirts?\b|\bblouses?\b|\bchemisiers?\b|\bdebardeurs?\b"),
        ("sets", r"\bsets?\b|\bensembles?\b"),
        ("jackets", r"\bjackets?\b|\bvestes?\b|\bblousons?\b"),
        ("coats", r"\bcoats?\b|\bmanteaux?\b|\btrench\b"),
        ("skirt", r"\bskirts?\b|\bjupes?\b"),
        ("hats", r"\bhats?\b|\bcaps?\b|\bchapeaux?\b|\bcasquettes?\b|\bbonnets?\b"),
        ("dress", r"\bdresses?\b|\brobes?\b"),
    )
    for key, pattern in patterns:
        if re.search(pattern, text):
            return key
    return None


def calculate_vinted_price(
    source_price_eur: float | Decimal,
    category: str | None,
) -> dict[str, Any]:
    source = Decimal(str(source_price_eur)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    adjusted = source
    result: dict[str, Any] = {
        "source_price_eur": float(source),
        "adjustment_eur": 0.0,
        "adjusted_price_eur": float(adjusted),
        "category": category,
        "category_label": PRICE_RULES.get(category or "", {}).get("label"),
        "vinted_price_min_eur": None,
        "vinted_price_max_eur": None,
        "recommended_price_eur": None,
        "eligible": False,
        "message": "",
    }
    rule = PRICE_RULES.get(category or "")
    if rule is None:
        result["message"] = "Choose a product category to calculate the Vinted price."
        return result

    bands: tuple[PriceBand, ...] = rule["bands"]
    for index, band in enumerate(bands):
        is_last = index == len(bands) - 1
        if adjusted >= band.minimum and (
            adjusted < band.maximum or (is_last and adjusted <= band.maximum)
        ):
            bracket_width = band.maximum - band.minimum
            if bracket_width == 0:
                position = Decimal("0")
            else:
                position = (adjusted - band.minimum) / bracket_width
            interpolated = (
                band.vinted_minimum
                + position * (band.vinted_maximum - band.vinted_minimum)
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            result.update(
                {
                    "vinted_price_min_eur": float(band.vinted_minimum),
                    "vinted_price_max_eur": float(band.vinted_maximum),
                    "recommended_price_eur": float(interpolated),
                    "purchase_bracket_min_eur": float(band.minimum),
                    "purchase_bracket_max_eur": float(band.maximum),
                    "interpolation_position": float(position),
                    "eligible": True,
                    "message": (
                        "Price calculated by linear interpolation within the "
                        "matching workbook bracket."
                    ),
                }
            )
            return result

    lowest = bands[0].minimum
    highest = bands[-1].maximum
    if adjusted < lowest:
        result["message"] = (
            f"The adjusted purchase price is below the {rule['label']} table."
        )
    elif adjusted > highest:
        result["message"] = (
            f"Do not test this product: the adjusted purchase price exceeds "
            f"the {rule['label']} limit of €{highest}."
        )
    else:
        result["message"] = (
            "The spreadsheet does not define a Vinted range for this exact price."
        )
    return result


def price_category_options() -> list[dict[str, str]]:
    return [
        {"value": key, "label": str(rule["label"])}
        for key, rule in PRICE_RULES.items()
    ]
