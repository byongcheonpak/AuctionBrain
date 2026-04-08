import sqlite3
from datetime import date


def get_connection(db_path: str) -> sqlite3.Connection:
    """Return a SQLite connection with row_factory set to sqlite3.Row."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_property(conn: sqlite3.Connection, property_data: dict) -> None:
    """
    Insert or replace a property record keyed on (case_number, property_number).

    discount_rate is computed automatically as min_bid_price / appraised_value * 100.
    updated_at is always set to the current timestamp.
    """
    data = dict(property_data)

    appraised_value = data.get("appraised_value")
    min_bid_price = data.get("min_bid_price")
    if appraised_value and min_bid_price:
        data["discount_rate"] = round(min_bid_price / appraised_value * 100, 2)
    else:
        data.setdefault("discount_rate", None)

    columns = [
        "case_number",
        "court",
        "property_number",
        "address",
        "property_type",
        "appraised_value",
        "min_bid_price",
        "discount_rate",
        "failed_count",
        "bid_date",
        "exclusive_area",
        "current_floor",
        "total_floor",
        "image_url",
        "detail_url",
        "region",
        "updated_at",
    ]

    # Build the values dict; updated_at is always refreshed to now.
    row = {col: data.get(col) for col in columns}
    row["updated_at"] = date.today().isoformat()  # CURRENT_TIMESTAMP equivalent

    placeholders = ", ".join(f":{col}" for col in columns)
    col_names = ", ".join(columns)

    sql = f"""
        INSERT INTO auction_properties ({col_names})
        VALUES ({placeholders})
        ON CONFLICT(case_number, property_number) DO UPDATE SET
            {", ".join(f"{col} = excluded.{col}" for col in columns if col != "case_number" and col != "property_number")}
    """

    conn.execute(sql, row)
    conn.commit()


def get_future_properties(conn: sqlite3.Connection, today: str = None) -> list[dict]:
    """
    Return properties whose bid_date is on or after today, ordered by bid_date ascending.

    If today is not provided, the current date is used (YYYY-MM-DD).
    """
    if today is None:
        today = date.today().isoformat()

    cursor = conn.execute(
        """
        SELECT *
        FROM auction_properties
        WHERE bid_date >= :today
        ORDER BY bid_date ASC
        """,
        {"today": today},
    )
    return [dict(row) for row in cursor.fetchall()]
