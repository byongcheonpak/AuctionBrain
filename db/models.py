import sqlite3


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS auction_properties (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    case_number        TEXT NOT NULL,
    court              TEXT NOT NULL,
    property_number    TEXT NOT NULL,
    address            TEXT,
    property_type      TEXT DEFAULT '아파트',
    appraised_value    INTEGER,
    min_bid_price      INTEGER,
    discount_rate      REAL,
    failed_count       INTEGER DEFAULT 0,
    bid_date           DATE,
    exclusive_area     REAL,
    current_floor      INTEGER,
    total_floor        INTEGER,
    image_url          TEXT,
    detail_url         TEXT,
    region             TEXT,
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (case_number, property_number)
);
"""


def init_db(db_path: str) -> None:
    """Create auction_properties table if it does not exist."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(CREATE_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()
