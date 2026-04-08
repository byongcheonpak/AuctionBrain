"""
대법원 경매정보 사이트 크롤러
https://www.courtauction.go.kr/

실행: python -m crawler.court_auction [--show-browser]
"""

import sys
import time
import re
import os
from datetime import datetime

from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

# 프로젝트 루트를 sys.path에 추가 (python -m 실행 시 필요)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from db.models import init_db
from db.repository import get_connection, upsert_property

BASE_URL = "https://www.courtauction.go.kr"

# ---------------------------------------------------------------------------
# 유틸리티 함수
# ---------------------------------------------------------------------------

def extract_number(text: str) -> int | None:
    """텍스트에서 숫자만 추출 (콤마 제거).

    Examples:
        '5,000만원' → 50000000  (만원 단위 감지)
        '1,500,000,000' → 1500000000
        '3억 5,000만' → 350000000
    """
    if not text:
        return None
    text = text.strip().replace(",", "")

    # 억 + 만 혼합 (예: "3억 5000만")
    match_uk_man = re.search(r"(\d+)억\s*(\d+)만", text)
    if match_uk_man:
        uk = int(match_uk_man.group(1))
        man = int(match_uk_man.group(2))
        return uk * 100_000_000 + man * 10_000

    # 억 단독 (예: "3억")
    match_uk = re.search(r"(\d+)억", text)
    if match_uk:
        return int(match_uk.group(1)) * 100_000_000

    # 만원 단위 (예: "5000만원", "50000만")
    match_man = re.search(r"(\d+)만", text)
    if match_man:
        return int(match_man.group(1)) * 10_000

    # 순수 숫자
    match_num = re.search(r"(\d+)", text)
    if match_num:
        return int(match_num.group(1))

    return None


def extract_url_from_onclick(onclick: str) -> str | None:
    """onclick 속성에서 URL 또는 함수 인자를 추출해 detail_url 형태로 반환.

    일반적인 패턴:
        fn_detail('2024타경12345', '001')  → 파라미터 보존
        location.href='someUrl?param=val'  → URL 추출
        goDetail('caseNo')                  → 파라미터 보존
    """
    if not onclick:
        return None

    # location.href = '...' 패턴
    match = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", onclick)
    if match:
        url = match.group(1)
        if not url.startswith("http"):
            url = BASE_URL + ("" if url.startswith("/") else "/") + url
        return url

    # TODO: 실제 사이트의 JS 함수명 확인 후 패턴 추가
    # 일반적인 함수 호출 패턴: fn_name('arg1', 'arg2', ...)
    match = re.search(r"(\w+)\(([^)]+)\)", onclick)
    if match:
        fn_name = match.group(1)
        args_raw = match.group(2)
        # 문자열 인자 추출
        args = re.findall(r"['\"]([^'\"]+)['\"]", args_raw)
        if args:
            # 쿼리스트링 형태로 보존 (나중에 실제 URL 구조에 맞게 수정)
            return f"{BASE_URL}/?fn={fn_name}&" + "&".join(
                f"arg{i}={v}" for i, v in enumerate(args)
            )

    return None


def parse_floor(text: str) -> tuple[int | None, int | None]:
    """층 정보 파싱.

    Examples:
        '10 / 25층'  → (10, 25)
        '10층 / 25층' → (10, 25)
        '지하1층'     → (-1, None)
        '10층'        → (10, None)
    """
    if not text:
        return None, None

    text = text.strip()

    # 지하층 처리
    basement = re.search(r"지하\s*(\d+)", text)

    # X / Y층 패턴
    match_frac = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if match_frac:
        current = -int(match_frac.group(1)) if basement else int(match_frac.group(1))
        total = int(match_frac.group(2))
        return current, total

    # 단독 숫자층 패턴 (예: "10층")
    match_single = re.search(r"(\d+)층", text)
    if match_single:
        current = -int(match_single.group(1)) if basement else int(match_single.group(1))
        return current, None

    return None, None


def safe_text(element) -> str:
    """Playwright 엘리먼트에서 안전하게 텍스트 추출."""
    try:
        return element.inner_text().strip()
    except Exception:
        return ""


def parse_date(text: str) -> str | None:
    """날짜 텍스트를 YYYY-MM-DD 형식으로 변환.

    Examples:
        '2024.03.15'  → '2024-03-15'
        '2024-03-15'  → '2024-03-15'
        '24.03.15'    → '2024-03-15'
    """
    if not text:
        return None
    text = text.strip()

    # YYYY.MM.DD or YYYY-MM-DD
    match = re.search(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"

    # YY.MM.DD
    match = re.search(r"(\d{2})[.\-](\d{1,2})[.\-](\d{1,2})", text)
    if match:
        return f"20{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"

    return None


# ---------------------------------------------------------------------------
# 크롤링 핵심 함수
# ---------------------------------------------------------------------------

def navigate_to_search(page: Page) -> bool:
    """메인 페이지 → 부동산 물건검색 페이지로 이동.

    Returns:
        True if navigation succeeded, False otherwise.
    """
    try:
        print(f"  메인 페이지 접속: {BASE_URL}")
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)
        time.sleep(1)

        # TODO: 실제 메뉴 구조 확인 필요
        # 시도 1: "물건검색" 메뉴 텍스트로 클릭
        menu_candidates = [
            "text=물건검색",
            "text=부동산",
            "a:has-text('물건검색')",
            "a:has-text('부동산')",
            "#menuArea a:has-text('물건')",
        ]

        clicked_menu = False
        for selector in menu_candidates:
            try:
                locator = page.locator(selector).first
                if locator.is_visible(timeout=2_000):
                    locator.click()
                    time.sleep(0.5)
                    clicked_menu = True
                    print(f"  메뉴 클릭 성공: {selector}")
                    break
            except Exception:
                continue

        if not clicked_menu:
            # 직접 URL로 이동 시도
            # TODO: 실제 검색 페이지 URL 확인 필요
            search_url = f"{BASE_URL}/RetrieveRealEstList.laf"
            print(f"  직접 URL 접속 시도: {search_url}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            time.sleep(1)

        return True

    except PlaywrightTimeoutError as e:
        print(f"  [오류] 페이지 이동 타임아웃: {e}")
        return False
    except Exception as e:
        print(f"  [오류] 페이지 이동 실패: {e}")
        return False


def set_search_filters(page: Page, court_code: str) -> bool:
    """검색 필터 설정: 물건종류=아파트, 법원코드 선택.

    Args:
        court_code: config.py의 court_codes 값 (예: 'B000201')

    Returns:
        True if filters set successfully.
    """
    try:
        # TODO: 실제 폼 필드 selector 확인 필요

        # 물건종류 = 아파트 선택
        apt_selectors = [
            f"select[name='mulKndCd'] option[value='{config.PROPERTY_TYPE_APARTMENT}']",
            "select[name='mulKndCd']",
            "#mulKndCd",
        ]
        for sel in apt_selectors:
            try:
                if "option" in sel:
                    page.locator(sel).first.click(timeout=3_000)
                else:
                    page.select_option(sel, config.PROPERTY_TYPE_APARTMENT, timeout=3_000)
                print(f"  물건종류 선택 성공")
                break
            except Exception:
                continue

        time.sleep(0.3)

        # 법원 선택
        court_selectors = [
            "select[name='cortOfcCd']",
            "select[name='cortNo']",
            "#cortOfcCd",
            "#cortNo",
        ]
        for sel in court_selectors:
            try:
                page.select_option(sel, court_code, timeout=3_000)
                print(f"  법원코드 선택 성공: {court_code}")
                break
            except Exception:
                continue

        time.sleep(0.3)

        # 검색 버튼 클릭
        search_btn_selectors = [
            "button:has-text('검색')",
            "input[type='submit'][value='검색']",
            "a:has-text('검색')",
            "#btnSearch",
            ".btn_search",
        ]
        for sel in search_btn_selectors:
            try:
                locator = page.locator(sel).first
                if locator.is_visible(timeout=2_000):
                    locator.click()
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    print(f"  검색 버튼 클릭 성공")
                    return True
            except Exception:
                continue

        print("  [경고] 검색 버튼을 찾지 못했습니다.")
        return False

    except Exception as e:
        print(f"  [오류] 필터 설정 실패: {e}")
        return False


def parse_property_row(row, court_name: str, region_name: str) -> dict | None:
    """목록 테이블의 한 행을 파싱해 property_data dict 반환.

    실제 컬럼 순서는 사이트 확인 후 조정 필요.
    TODO: 실제 컬럼 매핑 확인 필요
    """
    try:
        cells = row.locator("td").all()
        if len(cells) < 4:
            return None  # 헤더 행 또는 빈 행 스킵

        # TODO: 실제 컬럼 인덱스 확인 필요
        # 일반적인 대법원 경매 목록 컬럼 추정:
        # 0: 사건번호, 1: 물건번호, 2: 소재지(주소), 3: 물건종류,
        # 4: 감정가, 5: 최저매각가격, 6: 유찰횟수, 7: 매각기일, 8: 전용면적/층

        def cell_text(idx: int) -> str:
            if idx < len(cells):
                return safe_text(cells[idx])
            return ""

        case_number = cell_text(0).strip()
        property_number = cell_text(1).strip() or "001"
        address = cell_text(2).strip()

        appraised_raw = cell_text(4)
        min_bid_raw = cell_text(5)
        failed_raw = cell_text(6)
        bid_date_raw = cell_text(7)
        area_floor_raw = cell_text(8)  # 전용면적 + 층 정보가 같은 셀일 수 있음

        if not case_number:
            return None

        # 전용면적 파싱
        exclusive_area = None
        area_match = re.search(r"([\d.]+)\s*㎡", area_floor_raw)
        if area_match:
            try:
                exclusive_area = float(area_match.group(1))
            except ValueError:
                pass

        # 층 파싱
        current_floor, total_floor = parse_floor(area_floor_raw)

        # 상세 URL 추출
        detail_url = None
        link_candidates = row.locator("a").all()
        for link in link_candidates:
            try:
                href = link.get_attribute("href")
                onclick = link.get_attribute("onclick")
                if href and href not in ("#", "javascript:void(0)", "javascript:;", ""):
                    if not href.startswith("http"):
                        href = BASE_URL + ("" if href.startswith("/") else "/") + href
                    detail_url = href
                    break
                if onclick:
                    extracted = extract_url_from_onclick(onclick)
                    if extracted:
                        detail_url = extracted
                        break
            except Exception:
                continue

        # onclick이 row 자체에 있을 수도 있음
        if not detail_url:
            try:
                row_onclick = row.get_attribute("onclick")
                if row_onclick:
                    detail_url = extract_url_from_onclick(row_onclick)
            except Exception:
                pass

        return {
            "case_number": case_number,
            "property_number": property_number,
            "court": court_name,
            "address": address or None,
            "property_type": "아파트",
            "appraised_value": extract_number(appraised_raw),
            "min_bid_price": extract_number(min_bid_raw),
            "failed_count": extract_number(failed_raw) or 0,
            "bid_date": parse_date(bid_date_raw),
            "exclusive_area": exclusive_area,
            "current_floor": current_floor,
            "total_floor": total_floor,
            "image_url": None,  # TODO: 상세 페이지에서 추출
            "detail_url": detail_url,
            "region": region_name,
        }

    except Exception as e:
        print(f"  [경고] 행 파싱 오류 (스킵): {e}")
        return None


def get_total_pages(page: Page) -> int:
    """현재 페이지에서 전체 페이지 수 추출.

    Returns:
        총 페이지 수. 파악 불가 시 1 반환.
    """
    try:
        # TODO: 실제 페이지네이션 구조 확인 필요
        # 패턴 1: "1 / 5 페이지" 텍스트
        pager_text_selectors = [
            ".pager",
            ".pagination",
            "#pageInfo",
            ".page_num",
        ]
        for sel in pager_text_selectors:
            try:
                text = page.locator(sel).first.inner_text(timeout=2_000)
                match = re.search(r"(\d+)\s*/\s*(\d+)", text)
                if match:
                    return int(match.group(2))
                match = re.search(r"전체\s*(\d+)\s*페이지", text)
                if match:
                    return int(match.group(1))
            except Exception:
                continue

        # 패턴 2: 페이지 링크 중 최대값
        page_links = page.locator("a[href*='pageNo'], a[onclick*='goPage']").all()
        max_page = 1
        for link in page_links:
            try:
                link_text = safe_text(link)
                if link_text.isdigit():
                    max_page = max(max_page, int(link_text))
            except Exception:
                continue
        if max_page > 1:
            return max_page

    except Exception as e:
        print(f"  [경고] 총 페이지 수 파악 실패 (1페이지로 처리): {e}")

    return 1


def go_to_next_page(page: Page, current_page: int) -> bool:
    """다음 페이지로 이동.

    Returns:
        True if navigation succeeded.
    """
    try:
        # TODO: 실제 다음 페이지 버튼 selector 확인 필요
        next_selectors = [
            "a:has-text('다음')",
            "a.next",
            ".pager a:has-text('>')",
            f"a[onclick*='goPage({current_page + 1})']",
            f"a[onclick*='pageNo={current_page + 1}']",
        ]
        for sel in next_selectors:
            try:
                locator = page.locator(sel).first
                if locator.is_visible(timeout=2_000):
                    locator.click()
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    time.sleep(config.CRAWL_DELAY_SECONDS)
                    return True
            except Exception:
                continue

        print(f"  [경고] 다음 페이지 버튼 없음 (페이지 {current_page} → {current_page + 1})")
        return False

    except Exception as e:
        print(f"  [오류] 다음 페이지 이동 실패: {e}")
        return False


def parse_list_page(page: Page, court_name: str, region_name: str) -> list[dict]:
    """현재 목록 페이지에서 모든 물건 파싱.

    Returns:
        property_data dict 리스트.
    """
    properties = []
    try:
        # TODO: 실제 테이블 selector 확인 필요
        table_selectors = [
            "table.list_table tbody tr",
            "table.tbl_list tbody tr",
            "#listTable tbody tr",
            ".list_area table tbody tr",
            "table tbody tr",
        ]

        rows = []
        for sel in table_selectors:
            try:
                found = page.locator(sel).all()
                if found:
                    rows = found
                    break
            except Exception:
                continue

        if not rows:
            print("  [경고] 목록 테이블을 찾을 수 없습니다.")
            return []

        for row in rows:
            prop = parse_property_row(row, court_name, region_name)
            if prop:
                properties.append(prop)

    except Exception as e:
        print(f"  [오류] 목록 페이지 파싱 실패: {e}")

    return properties


def crawl_court(
    page: Page,
    court_code: str,
    court_name: str,
    region_name: str,
    conn,
) -> int:
    """단일 법원 크롤링. 저장된 물건 수 반환."""
    total_saved = 0

    if not navigate_to_search(page):
        return 0

    if not set_search_filters(page, court_code):
        print(f"  [경고] {court_name} 필터 설정 실패, 스킵")
        return 0

    time.sleep(config.CRAWL_DELAY_SECONDS)

    total_pages = get_total_pages(page)
    print(f"  [{region_name}] {court_name} - 총 {total_pages}페이지")

    for page_num in range(1, total_pages + 1):
        properties = parse_list_page(page, court_name, region_name)
        print(
            f"  [{region_name}] {court_name} 페이지 {page_num}/{total_pages}"
            f" - {len(properties)}개 물건 처리"
        )

        for prop in properties:
            try:
                upsert_property(conn, prop)
                total_saved += 1
            except Exception as e:
                print(f"    [경고] 저장 실패 ({prop.get('case_number', '?')}): {e}")

        if page_num < total_pages:
            if not go_to_next_page(page, page_num):
                break
            time.sleep(config.CRAWL_DELAY_SECONDS)

    return total_saved


def crawl_region(page: Page, region_name: str, region_config: dict, conn) -> int:
    """특정 지역 크롤링. 저장된 물건 수 반환. 3회 retry 포함."""
    region_total = 0
    court_codes = region_config.get("court_codes", [])

    for court_code in court_codes:
        court_name = _get_court_name(court_code)

        for attempt in range(config.CRAWL_RETRY_COUNT):
            try:
                saved = crawl_court(page, court_code, court_name, region_name, conn)
                region_total += saved
                break  # 성공 시 retry 루프 탈출
            except Exception as e:
                wait_sec = 2 ** attempt  # 1s, 2s, 4s
                print(
                    f"  [오류] {court_name} 크롤링 실패 "
                    f"(시도 {attempt + 1}/{config.CRAWL_RETRY_COUNT}): {e}"
                )
                if attempt < config.CRAWL_RETRY_COUNT - 1:
                    print(f"  {wait_sec}초 후 재시도...")
                    time.sleep(wait_sec)
                else:
                    print(f"  [포기] {court_name} 크롤링 포기.")

        time.sleep(config.CRAWL_DELAY_SECONDS)

    return region_total


def _get_court_name(court_code: str) -> str:
    """법원 코드로 법원명 반환.

    TODO: 실제 코드-이름 매핑 완성 필요
    """
    court_name_map = {
        "B000201": "서울중앙지방법원",
        "B000202": "서울동부지방법원",
        "B000203": "서울남부지방법원",
        "B000204": "서울북부지방법원",
        "B000205": "서울서부지방법원",
        "B000261": "수원지방법원 성남지원",
    }
    return court_name_map.get(court_code, court_code)


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def run_crawl():
    """전체 크롤링 실행. 모든 지역 순회."""
    headless = "--show-browser" not in sys.argv

    # DB 초기화
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    init_db(config.DB_PATH)
    conn = get_connection(config.DB_PATH)

    start_time = datetime.now()
    print(f"=== AuctionBrain 크롤링 시작: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"DB 경로: {config.DB_PATH}")
    print(f"헤드리스 모드: {headless}")
    print()

    grand_total = 0

    with sync_playwright() as pw:
        # 시스템 Chrome 우선 시도, 실패 시 Playwright 번들 Chromium 사용
        launch_kwargs = dict(headless=headless)
        try:
            browser = pw.chromium.launch(channel="chrome", **launch_kwargs)
        except Exception:
            try:
                browser = pw.chromium.launch(channel="msedge", **launch_kwargs)
            except Exception:
                browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            for region_name, region_cfg in config.REGIONS.items():
                print(f"[지역 시작] {region_name}")
                region_total = crawl_region(page, region_name, region_cfg, conn)
                grand_total += region_total
                print(f"[지역 완료] {region_name}: {region_total}개 저장\n")
                time.sleep(config.CRAWL_DELAY_SECONDS * 2)

        except KeyboardInterrupt:
            print("\n[중단] 사용자 요청으로 크롤링 중단.")
        finally:
            context.close()
            browser.close()
            conn.close()

    end_time = datetime.now()
    elapsed = (end_time - start_time).seconds
    print(f"=== 크롤링 완료: {grand_total}개 저장, 소요 시간: {elapsed}초 ===")


if __name__ == "__main__":
    run_crawl()
