from __future__ import annotations

from pathlib import Path

import pytest

from backend.errors import ExtractionError
from backend.services.shein_extractor import (
    extraction_size_letter,
    inches_to_centimetres,
    parse_embedded_size_measurements,
    parse_embedded_size_s_measurements,
    parse_hovered_size_measurements,
    parse_size_s_measurements,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_product_measurement_table_and_labels_are_normalised() -> None:
    parsed = parse_size_s_measurements(
        (FIXTURES / "size_guide_product.html").read_text(encoding="utf-8")
    )
    assert parsed.table_type == "Mesures du produit"
    assert parsed.measurements == {
        "Poitrine": "88 cm",
        "Tour de taille": "70 cm",
        "Hanches": "94 cm",
        "Longueur": "136 cm",
    }
    assert parsed.originals == {}


def test_vertical_inch_table_is_converted_and_preserved() -> None:
    parsed = parse_size_s_measurements(
        (FIXTURES / "size_guide_inches.html").read_text(encoding="utf-8")
    )
    assert parsed.measurements["Poitrine"] == "87.6 cm"
    assert parsed.originals["Poitrine"] == "34.5 in"
    assert parsed.measurements["Longueur"] == "129.5 cm"


def test_inches_to_centimetres_converts_ranges() -> None:
    assert inches_to_centimetres('10-12"') == "25.4-30.5 cm"


def test_missing_size_s_is_not_replaced_with_another_size() -> None:
    html = """
    <h3>Mesures du produit</h3>
    <table>
      <tr><th>Taille</th><th>Poitrine</th></tr>
      <tr><td>M</td><td>92 cm</td></tr>
    </table>
    """
    with pytest.raises(ExtractionError) as raised:
        parse_size_s_measurements(html)
    assert raised.value.code == "size_s_missing"


def test_hover_rendered_size_s_panel_is_parsed() -> None:
    parsed = parse_hovered_size_measurements(
        (FIXTURES / "size_s_hover_panel.html").read_text(encoding="utf-8")
    )

    assert parsed.table_type == "Mesures du produit — Taille S (survol)"
    assert parsed.measurements == {
        "Poitrine": "88 cm",
        "Tour de taille": "70 cm",
        "Hanches": "94 cm",
        "Longueur": "136 cm",
    }


def test_french_numeric_and_letter_size_columns_are_supported() -> None:
    html = """
    <h3>Mesures produits</h3>
    <table>
      <tr>
        <th>FR</th><th>Taille</th><th>Tour de poitrine</th>
        <th>Tour de taille</th><th>Tour de hanches</th><th>Longueur</th>
      </tr>
      <tr><td>34</td><td>XS</td><td>73.5</td><td>58.0-94.5</td><td>161.5</td><td>115.3</td></tr>
      <tr><td>36</td><td>S</td><td>77.5</td><td>62.0-98.5</td><td>166.0</td><td>116.0</td></tr>
      <tr><td>38</td><td>M</td><td>81.5</td><td>66.0-102.5</td><td>170.5</td><td>116.7</td></tr>
    </table>
    """

    parsed = parse_size_s_measurements(html)

    assert parsed.measurements["Poitrine"] == "77.5 cm"
    assert parsed.measurements["Tour de taille"] == "62.0-98.5 cm"
    assert parsed.measurements["Hanches"] == "166.0 cm"


def test_hover_panel_does_not_require_a_measurement_heading() -> None:
    html = """
    <div class="size-hover-card" role="tooltip">
      <div><strong>Tour de poitrine:</strong> 77.5 cm</div>
      <div><strong>Tour de taille:</strong> 62.0-98.5 cm</div>
      <div><strong>Tour de hanches:</strong> 166.0 cm</div>
      <div><strong>Longueur:</strong> 116.0 cm</div>
    </div>
    """

    parsed = parse_hovered_size_measurements(html)

    assert parsed.measurements == {
        "Poitrine": "77.5 cm",
        "Tour de taille": "62.0-98.5 cm",
        "Hanches": "166.0 cm",
        "Longueur": "116.0 cm",
    }


def test_embedded_size_s_product_data_is_parsed_without_the_size_guide() -> None:
    html = """
    <script>
    window.productData = {"sizeInfo":[
      {"attr_id":"87","attr_value_name":"XS","Tour de poitrine ":"71 cm"},
      {"attr_id":"87","attr_value_name":"S","Tour de poitrine ":"75 cm",
       "Tour de taille ":"70-96 cm","Tour de hanches ":"104 cm",
       "Longueur ":"129 cm","Longueur des brides ":"36 cm"}
    ]};
    </script>
    """

    parsed = parse_embedded_size_s_measurements(html)

    assert parsed.table_type == "Mesures du produit — Taille S"
    assert parsed.measurements == {
        "Poitrine": "75 cm",
        "Tour de taille": "70-96 cm",
        "Hanches": "104 cm",
        "Longueur": "129 cm",
        "Longueur des brides": "36 cm",
    }


def test_embedded_petite_size_s_is_treated_as_size_s() -> None:
    html = """
    <script>
    window.productData = {"sizeInfo":[
      {"attr_value_name":"Petite XS","Tour de poitrine ":"71 cm"},
      {"attr_value_name":"Petite S","Tour de poitrine ":"75 cm",
       "Tour de taille ":"65 cm","Longueur ":"135 cm"}
    ]};
    </script>
    """

    parsed = parse_embedded_size_s_measurements(html)

    assert parsed.measurements == {
        "Poitrine": "75 cm",
        "Tour de taille": "65 cm",
        "Longueur": "135 cm",
    }


def test_extraction_size_letter_maps_dress_m_to_m() -> None:
    assert extraction_size_letter("dress_m") == "M"
    assert extraction_size_letter("dress") == "S"
    assert extraction_size_letter("skirt") == "S"
    assert extraction_size_letter("jeans") == "L"
    assert extraction_size_letter("coat") == "S"
    assert extraction_size_letter("jacket") == "S"
    assert extraction_size_letter("earrings") is None
    assert extraction_size_letter("mask") is None
    assert extraction_size_letter("hat") is None
    assert extraction_size_letter("plant") is None
    assert extraction_size_letter("shelf") is None


def test_embedded_size_l_product_data_is_parsed_without_taking_size_s() -> None:
    html = """
    <script>
    window.productData = {"sizeInfo":[
      {"attr_id":"87","attr_value_name":"S","Tour de taille ":"70 cm",
       "Longueur ":"100 cm"},
      {"attr_id":"87","attr_value_name":"M","Tour de taille ":"78 cm",
       "Longueur ":"104 cm"},
      {"attr_id":"87","attr_value_name":"L","Tour de taille ":"86 cm",
       "Longueur ":"108 cm"},
      {"attr_id":"87","attr_value_name":"XL","Tour de taille ":"94 cm",
       "Longueur ":"110 cm"}
    ]};
    </script>
    """

    parsed = parse_embedded_size_measurements(html, "L")

    assert parsed.table_type == "Mesures du produit — Taille L"
    assert parsed.measurements == {
        "Tour de taille": "86 cm",
        "Longueur": "108 cm",
    }


def test_hover_panel_does_not_treat_size_name_as_waist() -> None:
    html = """
    <div class="size-hover-card" role="tooltip">
      <div>Taille 28</div>
      <div><strong>Tour de taille:</strong> 86 cm</div>
      <div><strong>Hanches:</strong> 119 cm</div>
      <div><strong>L'entrejambe:</strong> 76.8 cm</div>
    </div>
    """

    parsed = parse_hovered_size_measurements(html, "L")

    assert parsed.measurements["Tour de taille"] == "86 cm"
    assert parsed.measurements["Hanches"] == "119 cm"
    assert parsed.measurements["Entrejambe"] == "76.8 cm"
    assert parsed.measurements["Tour de taille"] != "28 cm"


def test_hover_panel_converts_inch_waist_mistaken_as_cm() -> None:
    html = """
    <div class="size-hover-card" role="tooltip">
      <div><strong>Tour de taille:</strong> 28</div>
      <div><strong>Hanches:</strong> 46.9</div>
    </div>
    """

    parsed = parse_hovered_size_measurements(html, "L")

    assert parsed.measurements["Tour de taille"] == "71.1 cm"
    assert parsed.measurements["Hanches"] == "119.1 cm"


def test_embedded_size_l_with_waist_number_in_name() -> None:
    html = """
    <script>
    window.productData = {"sizeInfo":[
      {"attr_value_name":"XL","Tour de taille ":"94 cm","Longueur ":"110 cm"},
      {"attr_value_name":"L (32)","Tour de taille ":"86 cm","Longueur ":"108 cm",
       "L'entrejambe ":"76.8 cm"}
    ]};
    </script>
    """

    parsed = parse_embedded_size_measurements(html, "L")

    assert parsed.measurements == {
        "Tour de taille": "86 cm",
        "Longueur": "108 cm",
        "Entrejambe": "76.8 cm",
    }


def test_embedded_size_m_product_data_is_parsed_without_taking_size_s() -> None:
    html = """
    <script>
    window.productData = {"sizeInfo":[
      {"attr_id":"87","attr_value_name":"S","Tour de poitrine ":"75 cm",
       "Tour de taille ":"70-96 cm","Longueur ":"129 cm"},
      {"attr_id":"87","attr_value_name":"M","Tour de poitrine ":"92 cm",
       "Tour de taille ":"78-104 cm","Longueur ":"132 cm"},
      {"attr_id":"87","attr_value_name":"SM","Tour de poitrine ":"80 cm"}
    ]};
    </script>
    """

    parsed = parse_embedded_size_measurements(html, "M")

    assert parsed.table_type == "Mesures du produit — Taille M"
    assert parsed.measurements == {
        "Poitrine": "92 cm",
        "Tour de taille": "78-104 cm",
        "Longueur": "132 cm",
    }
