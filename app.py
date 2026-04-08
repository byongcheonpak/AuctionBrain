import os
import sqlite3
from datetime import date, timedelta

import streamlit as st

import config
from db.repository import get_connection, get_future_properties

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_bid_date(date_str: str, today: date) -> str:
    """'2026-04-10' -> '2026-04-10 (D-15)'"""
    try:
        bid = date.fromisoformat(date_str)
        delta = (bid - today).days
        return f"{date_str} (D-{delta})"
    except Exception:
        return date_str or ""


def format_area(sqm: float) -> str:
    """84.97 -> '84.97㎡ (25평)'"""
    if sqm is None:
        return ""
    pyeong = round(sqm / 3.3058)
    return f"{sqm:.2f}㎡ ({pyeong}평)"


def format_price(won: int) -> str:
    """520000000 -> '5.2억'"""
    if won is None:
        return ""
    uk = won / 1_0000_0000
    return f"{uk:.1f}억"


def format_floor(current: int, total: int) -> str:
    """(10, 25) -> '10 / 25층'"""
    if current is None or total is None:
        return ""
    return f"{current} / {total}층"


def format_failed_count(count: int) -> str:
    """0 -> '신건', 2 -> '2회'"""
    if count is None or count == 0:
        return "신건"
    return f"{count}회"


def format_discount_rate(rate: float) -> str:
    """69.23 -> '69%'"""
    if rate is None:
        return ""
    return f"{int(round(rate))}%"


def format_court_link(court: str, case_number: str, detail_url: str) -> str:
    label = f"{court} {case_number}"
    if detail_url:
        return f'<a href="{detail_url}" target="_blank">{label}</a>'
    return label


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def load_data() -> tuple[list[dict], str | None]:
    """Return (rows, last_updated_at) from DB. Caches for 60 s."""
    if not os.path.exists(config.DB_PATH):
        return [], None

    conn = get_connection(config.DB_PATH)
    try:
        rows = get_future_properties(conn)
        # Fetch last updated_at across all rows
        cur = conn.execute(
            "SELECT MAX(updated_at) as last_updated FROM auction_properties"
        )
        result = cur.fetchone()
        last_updated = result["last_updated"] if result else None
        return rows, last_updated
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# App layout
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="AuctionBrain",
    page_icon="🏠",
    layout="wide",
)

st.title("AuctionBrain — 아파트 경매 현황")

# Check DB existence before rendering anything else
if not os.path.exists(config.DB_PATH):
    st.warning(
        "DB 파일이 없습니다. 먼저 크롤러를 실행하세요:\n\n"
        "```\npython -m crawler.court_auction\n```"
    )
    st.stop()

all_rows, last_updated = load_data()

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("필터")

    region_options = ["전체"] + config.REGION_ENUM
    selected_region = st.selectbox("지역", region_options, index=0)

    today = date.today()
    three_months_later = today + timedelta(days=90)
    date_range = st.date_input(
        "입찰기일 범위",
        value=(today, three_months_later),
        min_value=today - timedelta(days=365),
        max_value=today + timedelta(days=365 * 2),
    )
    # date_input returns a tuple when range, or a single date if only one picked
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        date_start, date_end = date_range
    else:
        date_start = date_range if not isinstance(date_range, (list, tuple)) else date_range[0]
        date_end = date_start

    discount_cap = st.slider(
        "할인율 상한 (%)",
        min_value=0,
        max_value=100,
        value=100,
        help="최저가율이 이 값 이하인 물건만 표시 (낮을수록 더 많이 할인된 물건)",
    )

    failed_range = st.slider(
        "유찰횟수 범위",
        min_value=0,
        max_value=10,
        value=(0, 10),
    )
    failed_min, failed_max = failed_range

# ---------------------------------------------------------------------------
# Filter rows
# ---------------------------------------------------------------------------

def apply_filters(rows: list[dict]) -> list[dict]:
    filtered = []
    for r in rows:
        # Region
        if selected_region != "전체" and r.get("region") != selected_region:
            continue

        # Bid date range
        bid_date_str = r.get("bid_date") or ""
        try:
            bid_date = date.fromisoformat(bid_date_str)
        except Exception:
            continue
        if not (date_start <= bid_date <= date_end):
            continue

        # Discount rate (discount_rate in DB = min_bid / appraised * 100)
        dr = r.get("discount_rate")
        if dr is not None and dr > discount_cap:
            continue

        # Failed count
        fc = r.get("failed_count") or 0
        if not (failed_min <= fc <= failed_max):
            continue

        filtered.append(r)
    return filtered


filtered_rows = apply_filters(all_rows)

# ---------------------------------------------------------------------------
# Summary bar
# ---------------------------------------------------------------------------

col1, col2, col3 = st.columns(3)
col1.metric("전체 물건 수", f"{len(all_rows)}건")
col2.metric("필터 적용 후", f"{len(filtered_rows)}건")
col3.metric("마지막 수집", last_updated or "—")

st.divider()

# ---------------------------------------------------------------------------
# Main table rendered as HTML (supports clickable links)
# ---------------------------------------------------------------------------

if not filtered_rows:
    st.info("조건에 맞는 물건이 없습니다.")
    st.stop()

# Build HTML table
TABLE_HEADER = (
    "<thead><tr>"
    "<th>입찰기일</th>"
    "<th>법원/사건번호</th>"
    "<th>주소</th>"
    "<th>전용면적</th>"
    "<th>층수</th>"
    "<th>감정가</th>"
    "<th>최저입찰가</th>"
    "<th>할인율</th>"
    "<th>유찰횟수</th>"
    "</tr></thead>"
)

rows_html = []
for r in filtered_rows:
    bid_date_fmt = format_bid_date(r.get("bid_date") or "", today)
    court_link = format_court_link(
        r.get("court") or "",
        r.get("case_number") or "",
        r.get("detail_url") or "",
    )
    address = r.get("address") or ""
    area_fmt = format_area(r.get("exclusive_area"))
    floor_fmt = format_floor(r.get("current_floor"), r.get("total_floor"))
    appraised_fmt = format_price(r.get("appraised_value"))
    min_bid_fmt = format_price(r.get("min_bid_price"))
    discount_fmt = format_discount_rate(r.get("discount_rate"))
    failed_fmt = format_failed_count(r.get("failed_count") or 0)

    rows_html.append(
        f"<tr>"
        f"<td>{bid_date_fmt}</td>"
        f"<td>{court_link}</td>"
        f"<td>{address}</td>"
        f"<td>{area_fmt}</td>"
        f"<td>{floor_fmt}</td>"
        f"<td style='text-align:right'>{appraised_fmt}</td>"
        f"<td style='text-align:right'>{min_bid_fmt}</td>"
        f"<td style='text-align:right'>{discount_fmt}</td>"
        f"<td style='text-align:center'>{failed_fmt}</td>"
        f"</tr>"
    )

table_html = f"""
<style>
  .auction-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }}
  .auction-table th {{
    background: #1f4e79;
    color: white;
    padding: 8px 10px;
    text-align: left;
    white-space: nowrap;
  }}
  .auction-table td {{
    padding: 7px 10px;
    border-bottom: 1px solid #e0e0e0;
    vertical-align: middle;
  }}
  .auction-table tr:hover td {{
    background: #f0f4ff;
  }}
  .auction-table a {{
    color: #1a6fb0;
    text-decoration: none;
  }}
  .auction-table a:hover {{
    text-decoration: underline;
  }}
</style>
<table class="auction-table">
{TABLE_HEADER}
<tbody>
{"".join(rows_html)}
</tbody>
</table>
"""

st.markdown(table_html, unsafe_allow_html=True)
