from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    title TEXT,
    product_url TEXT NOT NULL DEFAULT '',
    main_image_url TEXT,
    additional_image_urls TEXT NOT NULL DEFAULT '[]',
    colour TEXT,
    material TEXT,
    category TEXT,
    measurements TEXT NOT NULL DEFAULT '{}',
    measurement_originals TEXT NOT NULL DEFAULT '{}',
    measurement_table_type TEXT,
    additional_details TEXT NOT NULL DEFAULT '{}',
    image_is_screenshot INTEGER NOT NULL DEFAULT 0,
    english_listing TEXT,
    french_listing TEXT,
    listing_type TEXT,
    fictional_brand TEXT,
    previous_english_listing TEXT,
    previous_french_listing TEXT,
    previous_listing_type TEXT,
    previous_fictional_brand TEXT,
    batch_number INTEGER NOT NULL DEFAULT 1,
    extraction_date TEXT NOT NULL,
    last_updated_date TEXT NOT NULL,
    extraction_status TEXT NOT NULL,
    error_message TEXT,
    UNIQUE(sku, product_url)
);
CREATE INDEX IF NOT EXISTS idx_products_updated
ON products(last_updated_date DESC);
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO app_state (key, value)
VALUES ('current_batch_number', '1');
CREATE TABLE IF NOT EXISTS batches (
    batch_number INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

JSON_FIELDS = {
    "additional_image_urls",
    "measurements",
    "measurement_originals",
    "additional_details",
    "english_listing",
    "french_listing",
    "previous_english_listing",
    "previous_french_listing",
}

_NULLABLE_LISTING_FIELDS = {
    "english_listing",
    "french_listing",
    "previous_english_listing",
    "previous_french_listing",
}


def _connect(path: Path | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(path or settings.database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_database(path: Path | None = None) -> None:
    with _connect(path) as connection:
        connection.executescript(SCHEMA)
        now = datetime.now(UTC).isoformat()
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(products)")
        }
        if "listing_type" not in columns:
            connection.execute("ALTER TABLE products ADD COLUMN listing_type TEXT")
        if "fictional_brand" not in columns:
            connection.execute("ALTER TABLE products ADD COLUMN fictional_brand TEXT")
        if "batch_number" not in columns:
            connection.execute(
                "ALTER TABLE products ADD COLUMN batch_number INTEGER NOT NULL DEFAULT 1"
            )
        if "previous_english_listing" not in columns:
            connection.execute(
                "ALTER TABLE products ADD COLUMN previous_english_listing TEXT"
            )
        if "previous_french_listing" not in columns:
            connection.execute(
                "ALTER TABLE products ADD COLUMN previous_french_listing TEXT"
            )
        if "previous_listing_type" not in columns:
            connection.execute(
                "ALTER TABLE products ADD COLUMN previous_listing_type TEXT"
            )
        if "previous_fictional_brand" not in columns:
            connection.execute(
                "ALTER TABLE products ADD COLUMN previous_fictional_brand TEXT"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_products_batch
            ON products(batch_number, last_updated_date DESC)
            """
        )
        product_count = connection.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]
        if product_count == 0:
            connection.execute(
                """
                UPDATE app_state SET value = '1'
                WHERE key = 'current_batch_number'
                """
            )
            connection.execute(
                "DELETE FROM batches WHERE batch_number != 1"
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO batches (
                    batch_number, name, created_at, updated_at
                ) VALUES (1, 'Batch 1', ?, ?)
                """,
                (now, now),
            )
        else:
            batch_numbers = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT batch_number FROM products"
                ).fetchall()
            }
            current_row = connection.execute(
                "SELECT value FROM app_state WHERE key = 'current_batch_number'"
            ).fetchone()
            try:
                batch_numbers.add(max(1, int(current_row[0])))
            except (TypeError, ValueError):
                batch_numbers.add(1)
            for batch_number in batch_numbers:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO batches (
                        batch_number, name, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (batch_number, f"Batch {batch_number}", now, now),
                )


def _decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for field in JSON_FIELDS:
        if data.get(field):
            data[field] = json.loads(data[field])
        elif field in _NULLABLE_LISTING_FIELDS:
            data[field] = None
        else:
            data[field] = [] if field == "additional_image_urls" else {}
    data["image_is_screenshot"] = bool(data["image_is_screenshot"])
    data["has_previous_listing"] = bool(data.get("previous_english_listing"))
    return data


def upsert_product(product: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    payload = {
        "sku": product["sku"],
        "title": product.get("title"),
        "product_url": product.get("product_url", ""),
        "main_image_url": product.get("main_image_url"),
        "additional_image_urls": json.dumps(
            product.get("additional_image_urls", []), ensure_ascii=False
        ),
        "colour": product.get("colour"),
        "material": product.get("material"),
        "category": product.get("category"),
        "measurements": json.dumps(product.get("measurements", {}), ensure_ascii=False),
        "measurement_originals": json.dumps(
            product.get("measurement_originals", {}), ensure_ascii=False
        ),
        "measurement_table_type": product.get("measurement_table_type"),
        "additional_details": json.dumps(
            product.get("additional_details", {}), ensure_ascii=False
        ),
        "image_is_screenshot": int(product.get("image_is_screenshot", False)),
        "batch_number": max(1, int(product.get("batch_number", 1))),
        "extraction_status": product.get("extraction_status", "success"),
        "error_message": product.get("error_message"),
        "now": now,
    }
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO batches (
                batch_number, name, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                payload["batch_number"],
                f"Batch {payload['batch_number']}",
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO products (
                sku, title, product_url, main_image_url, additional_image_urls,
                colour, material, category, measurements, measurement_originals,
                measurement_table_type, additional_details, image_is_screenshot,
                batch_number, extraction_date, last_updated_date,
                extraction_status, error_message
            ) VALUES (
                :sku, :title, :product_url, :main_image_url, :additional_image_urls,
                :colour, :material, :category, :measurements, :measurement_originals,
                :measurement_table_type, :additional_details, :image_is_screenshot,
                :batch_number, :now, :now, :extraction_status, :error_message
            )
            ON CONFLICT(sku, product_url) DO UPDATE SET
                title=excluded.title,
                main_image_url=excluded.main_image_url,
                additional_image_urls=excluded.additional_image_urls,
                colour=excluded.colour,
                material=excluded.material,
                category=excluded.category,
                measurements=excluded.measurements,
                measurement_originals=excluded.measurement_originals,
                measurement_table_type=excluded.measurement_table_type,
                additional_details=excluded.additional_details,
                image_is_screenshot=excluded.image_is_screenshot,
                batch_number=excluded.batch_number,
                last_updated_date=excluded.last_updated_date,
                extraction_status=excluded.extraction_status,
                error_message=excluded.error_message
            """,
            payload,
        )
        row = connection.execute(
            "SELECT * FROM products WHERE sku = ? AND product_url = ?",
            (payload["sku"], payload["product_url"]),
        ).fetchone()
        if (
            row is not None
            and payload["product_url"]
            and payload["extraction_status"] == "success"
        ):
            connection.execute(
                "DELETE FROM products WHERE sku = ? AND product_url = '' AND id != ?",
                (payload["sku"], row["id"]),
            )
    decoded = _decode_row(row)
    assert decoded is not None
    return decoded


def update_listings(
    product_id: int,
    english: dict[str, Any],
    french: dict[str, Any],
    path: Path | None = None,
    *,
    listing_type: str | None = None,
    fictional_brand: str | None = None,
) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    with _connect(path) as connection:
        current = connection.execute(
            """
            SELECT english_listing, french_listing, listing_type, fictional_brand
            FROM products WHERE id = ?
            """,
            (product_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE products
            SET english_listing = ?, french_listing = ?, listing_type = ?,
                fictional_brand = ?, last_updated_date = ?,
                previous_english_listing = ?, previous_french_listing = ?,
                previous_listing_type = ?, previous_fictional_brand = ?
            WHERE id = ?
            """,
            (
                json.dumps(english, ensure_ascii=False),
                json.dumps(french, ensure_ascii=False),
                listing_type,
                fictional_brand,
                now,
                current["english_listing"] if current else None,
                current["french_listing"] if current else None,
                current["listing_type"] if current else None,
                current["fictional_brand"] if current else None,
                product_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return _decode_row(row)


def restore_previous_listing(
    product_id: int, path: Path | None = None
) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    with _connect(path) as connection:
        current = connection.execute(
            """
            SELECT english_listing, french_listing, listing_type, fictional_brand,
                   previous_english_listing, previous_french_listing,
                   previous_listing_type, previous_fictional_brand
            FROM products WHERE id = ?
            """,
            (product_id,),
        ).fetchone()
        if current is None or not current["previous_english_listing"]:
            return None
        connection.execute(
            """
            UPDATE products
            SET english_listing = ?, french_listing = ?, listing_type = ?,
                fictional_brand = ?, last_updated_date = ?,
                previous_english_listing = ?, previous_french_listing = ?,
                previous_listing_type = ?, previous_fictional_brand = ?
            WHERE id = ?
            """,
            (
                current["previous_english_listing"],
                current["previous_french_listing"],
                current["previous_listing_type"],
                current["previous_fictional_brand"],
                now,
                current["english_listing"],
                current["french_listing"],
                current["listing_type"],
                current["fictional_brand"],
                product_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return _decode_row(row)


def update_price_calculation(
    product_id: int,
    calculation: dict[str, Any],
    path: Path | None = None,
) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT additional_details FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if row is None:
            return None
        details = json.loads(row["additional_details"] or "{}")
        details["price_calculator"] = calculation
        connection.execute(
            """
            UPDATE products
            SET additional_details = ?, last_updated_date = ?
            WHERE id = ?
            """,
            (json.dumps(details, ensure_ascii=False), now, product_id),
        )
        updated = connection.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return _decode_row(updated)


def latest_notes_for_sku(sku: str, path: Path | None = None) -> str:
    with _connect(path) as connection:
        rows = connection.execute(
            """
            SELECT additional_details FROM products
            WHERE sku = ?
            ORDER BY last_updated_date DESC
            """,
            (sku,),
        ).fetchall()
    for row in rows:
        details = json.loads(row["additional_details"] or "{}")
        note = " ".join(str(details.get("notes") or "").split())
        if note:
            return note
    return ""


def update_product_notes(
    product_id: int,
    notes: str,
    path: Path | None = None,
) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT additional_details FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if row is None:
            return None
        details = json.loads(row["additional_details"] or "{}")
        cleaned = " ".join(str(notes or "").split())
        if cleaned:
            details["notes"] = cleaned
        else:
            details.pop("notes", None)
        connection.execute(
            """
            UPDATE products
            SET additional_details = ?, last_updated_date = ?
            WHERE id = ?
            """,
            (json.dumps(details, ensure_ascii=False), now, product_id),
        )
        updated = connection.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return _decode_row(updated)


def update_measurements(
    product_id: int,
    measurements: dict[str, str],
    weight: str | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT measurements, additional_details FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        if row is None:
            return None
        merged = json.loads(row["measurements"] or "{}")
        merged.update(measurements)
        details = json.loads(row["additional_details"] or "{}")
        if weight:
            details["weight"] = weight
        connection.execute(
            """
            UPDATE products
            SET measurements = ?, additional_details = ?, last_updated_date = ?
            WHERE id = ?
            """,
            (
                json.dumps(merged, ensure_ascii=False),
                json.dumps(details, ensure_ascii=False),
                now,
                product_id,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return _decode_row(updated)


def list_used_fictional_brands(
    path: Path | None = None,
) -> set[str]:
    with _connect(path) as connection:
        rows = connection.execute(
            """
            SELECT fictional_brand
            FROM products
            WHERE fictional_brand IS NOT NULL
              AND TRIM(fictional_brand) != ''
            """
        ).fetchall()
    return {
        str(row["fictional_brand"]).strip()
        for row in rows
        if row["fictional_brand"] and str(row["fictional_brand"]).strip()
    }


def get_product(product_id: int, path: Path | None = None) -> dict[str, Any] | None:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return _decode_row(row)


def list_products(
    *, limit: int = 50, offset: int = 0, path: Path | None = None
) -> tuple[list[dict[str, Any]], int]:
    with _connect(path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM products
            ORDER BY last_updated_date DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        total = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    return [item for row in rows if (item := _decode_row(row)) is not None], total


def get_current_batch_number(path: Path | None = None) -> int:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT value FROM app_state WHERE key = 'current_batch_number'"
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO app_state (key, value)
                VALUES ('current_batch_number', '1')
                """
            )
            return 1
    try:
        return max(1, int(row["value"]))
    except (TypeError, ValueError):
        return 1


def _set_current_batch_number(batch_number: int, path: Path | None = None) -> None:
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES ('current_batch_number', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(batch_number),),
        )


def _ensure_batch_row(batch_number: int, path: Path | None = None) -> None:
    now = datetime.now(UTC).isoformat()
    with _connect(path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO batches (
                batch_number, name, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (batch_number, f"Batch {batch_number}", now, now),
        )


def start_next_batch(path: Path | None = None) -> int:
    current = get_current_batch_number(path)
    next_batch = current + 1
    _ensure_batch_row(next_batch, path)
    _set_current_batch_number(next_batch, path)
    return next_batch


def restore_current_batch(path: Path | None = None) -> int:
    remaining = list_batches(path)
    if remaining:
        latest = max(int(row["batch_number"]) for row in remaining)
        _set_current_batch_number(latest, path)
        return latest
    _ensure_batch_row(1, path)
    _set_current_batch_number(1, path)
    return 1


def list_batches(path: Path | None = None) -> list[dict[str, Any]]:
    with _connect(path) as connection:
        rows = connection.execute(
            """
            SELECT batches.batch_number, batches.name,
                   COUNT(products.id) AS product_count,
                   MAX(products.last_updated_date) AS last_updated_date
            FROM batches
            LEFT JOIN products
              ON products.batch_number = batches.batch_number
            GROUP BY batches.batch_number, batches.name
            ORDER BY batches.batch_number DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def rename_batch(
    batch_number: int,
    name: str,
    path: Path | None = None,
) -> dict[str, Any] | None:
    now = datetime.now(UTC).isoformat()
    with _connect(path) as connection:
        cursor = connection.execute(
            """
            UPDATE batches
            SET name = ?, updated_at = ?
            WHERE batch_number = ?
            """,
            (name, now, batch_number),
        )
        if cursor.rowcount == 0:
            return None
        row = connection.execute(
            """
            SELECT batch_number, name
            FROM batches WHERE batch_number = ?
            """,
            (batch_number,),
        ).fetchone()
    return dict(row) if row is not None else None


def move_products_to_batch(
    product_ids: list[int],
    batch_number: int,
    path: Path | None = None,
) -> int:
    """Reassign the given products to a different batch. Fixes mis-sorted picks."""
    if not product_ids:
        return 0
    _ensure_batch_row(batch_number, path)
    now = datetime.now(UTC).isoformat()
    placeholders = ",".join("?" for _ in product_ids)
    with _connect(path) as connection:
        cursor = connection.execute(
            f"""
            UPDATE products
            SET batch_number = ?, last_updated_date = ?
            WHERE id IN ({placeholders})
            """,
            (batch_number, now, *product_ids),
        )
    return cursor.rowcount


def list_products_by_batch(
    batch_number: int,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    with _connect(path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM products
            WHERE batch_number = ?
            ORDER BY last_updated_date DESC
            """,
            (batch_number,),
        ).fetchall()
    return [item for row in rows if (item := _decode_row(row)) is not None]


def delete_products_by_batch(
    batch_number: int,
    path: Path | None = None,
) -> int:
    with _connect(path) as connection:
        cursor = connection.execute(
            "DELETE FROM products WHERE batch_number = ?",
            (batch_number,),
        )
        connection.execute(
            "DELETE FROM batches WHERE batch_number = ?",
            (batch_number,),
        )
    return cursor.rowcount


def delete_product(product_id: int, path: Path | None = None) -> bool:
    with _connect(path) as connection:
        cursor = connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
    return cursor.rowcount > 0


def count_products_by_sku(sku: str, path: Path | None = None) -> int:
    with _connect(path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM products WHERE sku = ?",
            (sku,),
        ).fetchone()[0]


def count_products_by_batch(
    batch_number: int,
    path: Path | None = None,
) -> int:
    with _connect(path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM products WHERE batch_number = ?",
            (batch_number,),
        ).fetchone()[0]


def delete_all_products(path: Path | None = None) -> int:
    with _connect(path) as connection:
        cursor = connection.execute("DELETE FROM products")
        connection.execute("DELETE FROM batches")
        connection.execute(
            """
            UPDATE app_state SET value = '1'
            WHERE key = 'current_batch_number'
                """
        )
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            INSERT INTO batches (batch_number, name, created_at, updated_at)
            VALUES (1, 'Batch 1', ?, ?)
            """,
            (now, now),
        )
    return cursor.rowcount
