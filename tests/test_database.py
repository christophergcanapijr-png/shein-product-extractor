from __future__ import annotations

from pathlib import Path

from backend import database


def extracted_product(title: str = "Pink dress") -> dict:
    return {
        "sku": "SKU-123",
        "title": title,
        "product_url": "https://fr.shein.com/pink-dress-p-123.html",
        "main_image_url": "https://img.ltwebstatic.com/example.jpg",
        "additional_image_urls": ["https://img.ltwebstatic.com/example-2.jpg"],
        "colour": "Rose",
        "material": "Polyester",
        "category": "Robes",
        "measurements": {"Poitrine": "88 cm"},
        "measurement_originals": {},
        "measurement_table_type": "Mesures du produit",
        "additional_details": {"style": "Élégant"},
        "extraction_status": "success",
    }


def test_database_create_update_and_deduplicate(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)

    created = database.upsert_product(extracted_product(), path)
    updated = database.upsert_product(extracted_product("Updated pink dress"), path)
    items, total = database.list_products(path=path)

    assert created["id"] == updated["id"]
    assert updated["title"] == "Updated pink dress"
    assert total == 1
    assert items[0]["measurements"] == {"Poitrine": "88 cm"}


def test_product_notes_can_be_saved_and_cleared(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    created = database.upsert_product(extracted_product(), path)

    saved = database.update_product_notes(
        created["id"],
        "  grey version  4 pictures  size 38 ",
        path,
    )
    cleared = database.update_product_notes(created["id"], "   ", path)

    assert saved is not None
    assert saved["additional_details"]["notes"] == "grey version 4 pictures size 38"
    assert cleared is not None
    assert "notes" not in (cleared["additional_details"] or {})
    assert database.latest_notes_for_sku("SKU-123", path) == ""


def test_update_measurements_merges_and_stores_weight(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    created = database.upsert_product(extracted_product(), path)

    first = database.update_measurements(
        created["id"],
        {"Longueur": "16 cm", "Largeur": "26 cm"},
        "0.20 kg",
        path,
    )

    assert first is not None
    assert first["measurements"] == {"Poitrine": "88 cm", "Longueur": "16 cm", "Largeur": "26 cm"}
    assert first["additional_details"]["weight"] == "0.20 kg"

    second = database.update_measurements(
        created["id"],
        {"Longueur des brides": "120 cm"},
        None,
        path,
    )

    assert second is not None
    assert second["measurements"] == {
        "Poitrine": "88 cm",
        "Longueur": "16 cm",
        "Largeur": "26 cm",
        "Longueur des brides": "120 cm",
    }
    assert second["additional_details"]["weight"] == "0.20 kg"


def test_update_measurements_returns_none_for_missing_product(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)

    assert database.update_measurements(999, {"Longueur": "10 cm"}, None, path) is None


def test_database_listing_update(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    created = database.upsert_product(extracted_product(), path)
    listing = {
        "title": "Pink dress",
        "description": "Perfect condition.",
        "hashtags": ["#dress"] * 20,
    }

    updated = database.update_listings(
        created["id"],
        listing,
        listing,
        path,
        listing_type="earrings",
        fictional_brand="Velmora",
    )

    assert updated is not None
    assert updated["english_listing"]["title"] == "Pink dress"
    assert updated["listing_type"] == "earrings"
    assert updated["fictional_brand"] == "Velmora"
    assert updated["has_previous_listing"] is False


def test_regenerating_a_listing_lets_you_restore_the_previous_one(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    created = database.upsert_product(extracted_product(), path)
    first_listing = {
        "title": "First title",
        "description": "First description.",
        "hashtags": ["#first"] * 20,
    }
    second_listing = {
        "title": "Second title",
        "description": "Second description.",
        "hashtags": ["#second"] * 20,
    }

    database.update_listings(
        created["id"], first_listing, first_listing, path,
        listing_type="earrings", fictional_brand="Velmora",
    )
    after_second = database.update_listings(
        created["id"], second_listing, second_listing, path,
        listing_type="bag", fictional_brand="Aetheria",
    )

    assert after_second is not None
    assert after_second["has_previous_listing"] is True
    assert after_second["english_listing"]["title"] == "Second title"

    restored = database.restore_previous_listing(created["id"], path)

    assert restored is not None
    assert restored["english_listing"]["title"] == "First title"
    assert restored["listing_type"] == "earrings"
    assert restored["fictional_brand"] == "Velmora"
    assert restored["has_previous_listing"] is True

    restored_again = database.restore_previous_listing(created["id"], path)

    assert restored_again is not None
    assert restored_again["english_listing"]["title"] == "Second title"
    assert restored_again["listing_type"] == "bag"
    assert restored_again["fictional_brand"] == "Aetheria"


def test_restore_previous_listing_returns_none_when_nothing_to_restore(
    tmp_path: Path,
) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    created = database.upsert_product(extracted_product(), path)

    assert database.restore_previous_listing(created["id"], path) is None

    database.update_listings(
        created["id"],
        {"title": "Only title", "description": "Only.", "hashtags": ["#a"] * 20},
        {"title": "Only title", "description": "Only.", "hashtags": ["#a"] * 20},
        path,
        listing_type="earrings",
        fictional_brand="Velmora",
    )

    assert database.restore_previous_listing(created["id"], path) is None


def test_list_used_fictional_brands(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    first = database.upsert_product(extracted_product("Navy skirt"), path)
    second = database.upsert_product(
        {
            **extracted_product("Black skirt"),
            "sku": "SKU-456",
            "product_url": "https://fr.shein.com/black-skirt-p-456.html",
        },
        path,
    )
    listing = {
        "title": "Navy midi skirt size S",
        "description": "Perfect condition.",
        "hashtags": ["#skirt"] * 20,
    }
    database.update_listings(
        first["id"], listing, listing, path, listing_type="skirt", fictional_brand="elegant"
    )
    database.update_listings(
        second["id"], listing, listing, path, listing_type="skirt", fictional_brand="oldmoney"
    )

    assert database.list_used_fictional_brands(path) == {"elegant", "oldmoney"}


def test_success_replaces_failed_placeholder_for_same_sku(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    database.upsert_product(
        {
            "sku": "SKU-123",
            "product_url": "",
            "extraction_status": "failed",
            "error_message": "Temporary verification failure",
        },
        path,
    )

    database.upsert_product(extracted_product(), path)
    items, total = database.list_products(path=path)

    assert total == 1
    assert items[0]["extraction_status"] == "success"


def test_delete_all_products_clears_history(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    database.upsert_product(extracted_product(), path)

    deleted = database.delete_all_products(path)
    items, total = database.list_products(path=path)

    assert deleted == 1
    assert items == []
    assert total == 0
    assert database.get_current_batch_number(path) == 1


def test_empty_database_is_normalized_back_to_batch_one(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    assert database.start_next_batch(path) == 2

    database.init_database(path)

    assert database.get_current_batch_number(path) == 1


def test_batches_increment_group_and_delete_independently(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)

    first = extracted_product()
    first["batch_number"] = 1
    database.upsert_product(first, path)

    assert database.get_current_batch_number(path) == 1
    assert database.start_next_batch(path) == 2

    second = extracted_product("Blue dress")
    second.update(
        sku="SKU-456",
        product_url="https://fr.shein.com/blue-dress-p-456.html",
        batch_number=2,
    )
    database.upsert_product(second, path)

    assert database.list_batches(path) == [
        {
            "batch_number": 2,
            "name": "Batch 2",
            "product_count": 1,
            "last_updated_date": database.list_products_by_batch(2, path)[0][
                "last_updated_date"
            ],
        },
        {
            "batch_number": 1,
            "name": "Batch 1",
            "product_count": 1,
            "last_updated_date": database.list_products_by_batch(1, path)[0][
                "last_updated_date"
            ],
        },
    ]

    assert database.delete_products_by_batch(1, path) == 1
    items, total = database.list_products(path=path)
    assert total == 1
    assert items[0]["sku"] == "SKU-456"
    assert items[0]["batch_number"] == 2
    assert database.get_current_batch_number(path) == 2


def test_move_products_to_batch_reassigns_and_creates_target_batch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)

    first = extracted_product()
    first["batch_number"] = 1
    created_first = database.upsert_product(first, path)

    second = extracted_product("Blue dress")
    second.update(
        sku="SKU-456",
        product_url="https://fr.shein.com/blue-dress-p-456.html",
        batch_number=1,
    )
    created_second = database.upsert_product(second, path)

    moved = database.move_products_to_batch(
        [created_first["id"], created_second["id"]], 3, path
    )

    assert moved == 2
    items, _ = database.list_products(path=path)
    assert {item["batch_number"] for item in items} == {3}
    batch_numbers = {batch["batch_number"] for batch in database.list_batches(path)}
    assert 3 in batch_numbers


def test_move_products_to_batch_ignores_unknown_ids(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)
    product = database.upsert_product(extracted_product(), path)

    moved = database.move_products_to_batch([product["id"], 999999], 2, path)

    assert moved == 1
    items, _ = database.list_products(path=path)
    assert items[0]["batch_number"] == 2


def test_move_products_to_batch_with_no_ids_is_a_no_op(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)

    assert database.move_products_to_batch([], 5, path) == 0


def test_deleting_current_empty_batch_returns_to_previous_batch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)

    first = extracted_product()
    first["batch_number"] = 1
    database.upsert_product(first, path)
    assert database.start_next_batch(path) == 2

    assert database.delete_products_by_batch(2, path) == 0
    assert database.restore_current_batch(path) == 1
    assert database.get_current_batch_number(path) == 1
    assert [batch["batch_number"] for batch in database.list_batches(path)] == [1]


def test_batch_name_can_be_changed_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "products.db"
    database.init_database(path)

    assert database.rename_batch(1, "Summer dresses", path) == {
        "batch_number": 1,
        "name": "Summer dresses",
    }

    database.init_database(path)

    assert database.list_batches(path)[0]["name"] == "Summer dresses"
