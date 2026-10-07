from decimal import Decimal, ROUND_HALF_UP

from backend.services.price_calculator import (
    PRICE_RULES,
    calculate_vinted_price,
    infer_price_category,
)
from backend.services.shein_extractor import parse_euro_price


def test_parse_current_shein_price_with_decimal_comma() -> None:
    assert parse_euro_price("25,99€") == 25.99


def test_parse_current_shein_price_with_spaces() -> None:
    assert parse_euro_price("1 099,90 EUR") == 1099.9


def test_parse_temu_france_split_euro_price() -> None:
    assert parse_euro_price("12€99") == 12.99
    assert parse_euro_price("12 € 99") == 12.99
    assert parse_euro_price("€8.99") == 8.99


def test_parse_euro_price_prefers_marked_amount_over_other_numbers() -> None:
    assert parse_euro_price("Save 50% €8,99") == 8.99
    assert parse_euro_price("4,8 (1289 avis) 12,99 €") == 12.99
    assert parse_euro_price("was 29€99 now 12€99") == 12.99


def test_parse_euro_price_rejects_foreign_storefront_currency() -> None:
    assert parse_euro_price("₱284") is None
    assert parse_euro_price("$12.99") is None


def test_dress_price_uses_the_live_shein_price_for_lookup() -> None:
    calculation = calculate_vinted_price(25.99, "dress")

    assert calculation["adjusted_price_eur"] == 25.99
    assert calculation["vinted_price_min_eur"] == 74.9
    assert calculation["vinted_price_max_eur"] == 89.9
    assert calculation["recommended_price_eur"] == 76.39
    assert calculation["eligible"] is True


def test_fifteen_euro_dress_uses_the_updated_band_minimum() -> None:
    calculation = calculate_vinted_price(15, "dress")

    assert calculation["adjusted_price_eur"] == 15.0
    assert calculation["vinted_price_min_eur"] == 54.9
    assert calculation["recommended_price_eur"] == 54.9


def test_sixteen_euro_dress_is_linearly_interpolated() -> None:
    calculation = calculate_vinted_price(16, "dress")

    # Purchase 15-25, Vinted 54.9-69.9 per the latest pricing sheet.
    assert calculation["recommended_price_eur"] == 56.4
    assert calculation["purchase_bracket_min_eur"] == 15.0
    assert calculation["purchase_bracket_max_eur"] == 25.0
    assert calculation["interpolation_position"] == 0.1


def test_twenty_two_euro_dress_matches_updated_interpolation() -> None:
    calculation = calculate_vinted_price(22, "dress")

    assert calculation["vinted_price_min_eur"] == 54.9
    assert calculation["vinted_price_max_eur"] == 69.9
    assert calculation["interpolation_position"] == 0.7
    assert calculation["recommended_price_eur"] == 65.4


def test_shoulder_bag_price_is_interpolated_inside_its_bracket() -> None:
    calculation = calculate_vinted_price(9, "shoulder_bag")

    # 40% through purchase 5-15 and Vinted 26.9-43.9.
    assert calculation["recommended_price_eur"] == 33.7


def test_nineteen_euro_skirt_uses_updated_interpolation_bracket() -> None:
    calculation = calculate_vinted_price(19, "skirt")

    assert calculation["purchase_bracket_min_eur"] == 10.0
    assert calculation["purchase_bracket_max_eur"] == 20.0
    assert calculation["vinted_price_min_eur"] == 34.9
    assert calculation["vinted_price_max_eur"] == 44.9
    assert calculation["interpolation_position"] == 0.9
    assert calculation["recommended_price_eur"] == 43.9


def test_updated_workbook_categories_are_available() -> None:
    assert calculate_vinted_price(20, "heeled_sandal")["recommended_price_eur"] == 39.4
    assert calculate_vinted_price(20, "long_boots")["recommended_price_eur"] == 46.9
    assert calculate_vinted_price(7, "necklace")["recommended_price_eur"] == 30.3
    assert calculate_vinted_price(12, "plant")["recommended_price_eur"] == 42.7


def test_pants_use_their_own_vinted_bands() -> None:
    calculation = calculate_vinted_price(10, "pants")

    assert calculation["eligible"] is True
    assert calculation["vinted_price_min_eur"] == 36.9
    assert calculation["vinted_price_max_eur"] == 39.9
    assert calculation["recommended_price_eur"] == 38.4


def test_pants_at_thirty_euros_uses_top_of_last_band() -> None:
    at_thirty = calculate_vinted_price(30, "pants")

    assert at_thirty["eligible"] is True
    assert at_thirty["recommended_price_eur"] == 59.9


def test_pants_above_thirty_euros_are_rejected() -> None:
    calculation = calculate_vinted_price(30.01, "pants")

    assert calculation["eligible"] is False
    assert "Do not test" in calculation["message"]


def test_long_boots_above_forty_euros_are_rejected() -> None:
    calculation = calculate_vinted_price(45.5, "long_boots")

    assert calculation["eligible"] is False
    assert "Do not test" in calculation["message"]


def test_every_workbook_band_interpolates_its_midpoint() -> None:
    for category, rule in PRICE_RULES.items():
        for band in rule["bands"]:
            purchase_midpoint = (band.minimum + band.maximum) / 2
            expected = (
                (band.vinted_minimum + band.vinted_maximum) / 2
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            calculation = calculate_vinted_price(
                purchase_midpoint, category
            )
            assert calculation["recommended_price_eur"] == float(expected)


def test_price_above_category_limit_is_rejected() -> None:
    calculation = calculate_vinted_price(12, "earrings")

    assert calculation["eligible"] is False
    assert "Do not test" in calculation["message"]


def test_infers_french_and_english_product_categories() -> None:
    assert infer_price_category("Robe longue élégante", None) == "dress"
    assert infer_price_category("Gold floral drop earrings", None) == "earrings"
    assert infer_price_category("Sac à bandoulière", None) == "shoulder_bag"


def test_infers_new_workbook_categories() -> None:
    assert infer_price_category("Black heeled sandals", None) == "heeled_sandal"
    assert infer_price_category("Knee-high cream boots", None) == "long_boots"
    assert infer_price_category("Bottes hautes noires", None) == "long_boots"
    assert infer_price_category("Gold necklace", None) == "necklace"
    assert infer_price_category("Artificial plant decor", None) == "plant"
    assert infer_price_category("Boho area rug", None) == "carpet"
    assert infer_price_category("Wood nightstand bedside", None) == "nightstand"
    assert infer_price_category("Table de nuit beige", None) == "nightstand"
    assert infer_price_category("Tapis salon vintage", None) == "carpet"
    assert infer_price_category("Black sequin masquerade mask", None) == "mask"
    assert infer_price_category("Masque de soirée à paillettes", None) == "mask"


def test_carpet_price_uses_the_requested_vinted_bands() -> None:
    calculation = calculate_vinted_price(10, "carpet")

    assert calculation["eligible"] is True
    assert calculation["vinted_price_min_eur"] == 44.9
    assert calculation["vinted_price_max_eur"] == 54.9
    assert calculation["recommended_price_eur"] == 49.9


def test_plant_price_uses_the_requested_vinted_bands() -> None:
    calculation = calculate_vinted_price(12, "plant")

    assert calculation["eligible"] is True
    assert calculation["vinted_price_min_eur"] == 37.9
    assert calculation["vinted_price_max_eur"] == 49.9
    assert calculation["recommended_price_eur"] == 42.7


def test_plant_above_thirty_euros_is_rejected() -> None:
    at_limit = calculate_vinted_price(30, "plant")
    over_limit = calculate_vinted_price(30.01, "plant")

    assert at_limit["eligible"] is True
    assert at_limit["recommended_price_eur"] == 79.9
    assert over_limit["eligible"] is False
    assert "Do not test" in over_limit["message"]


def test_nightstand_price_uses_the_requested_vinted_bands() -> None:
    calculation = calculate_vinted_price(15, "nightstand")

    assert calculation["eligible"] is True
    assert calculation["vinted_price_min_eur"] == 44.9
    assert calculation["vinted_price_max_eur"] == 64.9
    assert calculation["recommended_price_eur"] == 54.9


def test_nightstand_above_forty_euros_is_rejected() -> None:
    at_limit = calculate_vinted_price(40, "nightstand")
    over_limit = calculate_vinted_price(40.01, "nightstand")

    assert at_limit["eligible"] is True
    assert at_limit["recommended_price_eur"] == 129.9
    assert over_limit["eligible"] is False
    assert "Do not test" in over_limit["message"]


def test_mask_price_uses_the_requested_vinted_bands() -> None:
    calculation = calculate_vinted_price(15, "mask")

    assert calculation["eligible"] is True
    assert calculation["vinted_price_min_eur"] == 39.9
    assert calculation["vinted_price_max_eur"] == 49.9
    assert calculation["recommended_price_eur"] == 44.9


def test_mask_above_forty_euros_is_rejected() -> None:
    at_limit = calculate_vinted_price(40, "mask")
    over_limit = calculate_vinted_price(40.01, "mask")

    assert at_limit["eligible"] is True
    assert at_limit["recommended_price_eur"] == 129.9
    assert over_limit["eligible"] is False
    assert "Do not test" in over_limit["message"]


def test_carpet_above_thirty_three_euros_is_rejected() -> None:
    at_limit = calculate_vinted_price(33, "carpet")
    over_limit = calculate_vinted_price(33.01, "carpet")

    assert at_limit["eligible"] is True
    assert at_limit["recommended_price_eur"] == 78.9
    assert over_limit["eligible"] is False
    assert "Do not test" in over_limit["message"]


def test_cat_toy_price_uses_the_requested_vinted_bands() -> None:
    calculation = calculate_vinted_price(25, "cat_toy")

    assert calculation["eligible"] is True
    assert calculation["vinted_price_min_eur"] == 49.9
    assert calculation["vinted_price_max_eur"] == 73.9
    assert calculation["recommended_price_eur"] == 61.9


def test_belt_above_thirty_euros_is_rejected() -> None:
    at_limit = calculate_vinted_price(30, "belt")
    over_limit = calculate_vinted_price(30.01, "belt")

    assert at_limit["eligible"] is True
    assert at_limit["recommended_price_eur"] == 79.9
    assert over_limit["eligible"] is False
    assert "Do not test" in over_limit["message"]


def test_new_home_decor_categories_are_available() -> None:
    assert calculate_vinted_price(2, "mirror")["recommended_price_eur"] == 28.1
    assert calculate_vinted_price(2, "lamp")["recommended_price_eur"] == 28.1
    assert calculate_vinted_price(2, "curtains")["recommended_price_eur"] == 28.1
    assert (
        calculate_vinted_price(2, "suspended_decorations")["recommended_price_eur"]
        == 28.1
    )
    assert calculate_vinted_price(20, "organizer")["recommended_price_eur"] == 59.9
    assert calculate_vinted_price(20, "sculpture")["recommended_price_eur"] == 59.9
    assert calculate_vinted_price(2, "cushion")["recommended_price_eur"] == 28.1


def test_cushion_price_matches_the_curtains_bands() -> None:
    for price in (2, 7, 15, 25):
        assert (
            calculate_vinted_price(price, "cushion")["recommended_price_eur"]
            == calculate_vinted_price(price, "curtains")["recommended_price_eur"]
        )


def test_cushion_above_thirty_euros_is_rejected() -> None:
    over_limit = calculate_vinted_price(30.01, "cushion")

    assert over_limit["eligible"] is False
    assert "do not test" in over_limit["message"].casefold()


def test_infers_home_decor_and_accessory_categories() -> None:
    assert infer_price_category("Interactive cat toy wand", None) == "cat_toy"
    assert infer_price_category("Ceinture cuir femme", None) == "belt"
    assert infer_price_category("Round wall mirror", None) == "mirror"
    assert infer_price_category("LED desk lamp", None) == "lamp"
    assert infer_price_category("Blackout curtains set", None) == "curtains"
    assert infer_price_category("Desk organizer box", None) == "organizer"
    assert infer_price_category("Resin cat sculpture", None) == "sculpture"
    assert infer_price_category("Decorative throw cushion", None) == "cushion"
    assert infer_price_category("Coussin décoratif brodé", None) == "cushion"


def test_plant_price_uses_the_updated_ten_to_fifteen_band() -> None:
    calculation = calculate_vinted_price(12, "plant")

    # Purchase 10-15, Vinted 37.9-49.9 per the latest pricing sheet.
    assert calculation["purchase_bracket_min_eur"] == 10.0
    assert calculation["purchase_bracket_max_eur"] == 15.0
    assert calculation["vinted_price_min_eur"] == 37.9
    assert calculation["vinted_price_max_eur"] == 49.9


def test_new_home_decor_categories_use_the_shared_bands() -> None:
    for category in ("lace_umbrella", "jewelry_box", "faux_fur_leg_warmer"):
        assert (
            calculate_vinted_price(2, category)["recommended_price_eur"]
            == calculate_vinted_price(2, "curtains")["recommended_price_eur"]
        )
        over_limit = calculate_vinted_price(30.01, category)
        assert over_limit["eligible"] is False


def test_infers_newly_added_categories() -> None:
    assert infer_price_category("Lace umbrella", None) == "lace_umbrella"
    assert infer_price_category("Parapluie en dentelle", None) == "lace_umbrella"
    assert infer_price_category("Velvet jewelry box", None) == "jewelry_box"
    assert infer_price_category("Boîte à bijoux dorée", None) == "jewelry_box"
    assert infer_price_category("Faux fur leg warmers", None) == "faux_fur_leg_warmer"
    assert infer_price_category("Jambières en fausse fourrure", None) == "faux_fur_leg_warmer"
