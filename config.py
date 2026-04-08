# 대법원 경매정보 지역 설정
# courtauction.go.kr 에서 사용하는 법원 지역 코드

REGIONS = {
    "서울": {
        "name": "서울",
        "court_codes": [
            "B000201",  # 서울중앙지방법원
            "B000202",  # 서울동부지방법원
            "B000203",  # 서울남부지방법원
            "B000204",  # 서울북부지방법원
            "B000205",  # 서울서부지방법원
        ],
    },
    "용인수지": {
        "name": "용인수지",
        "court_codes": [
            "B000261",  # 수원지방법원 성남지원
        ],
    },
    "성남분당": {
        "name": "성남분당",
        "court_codes": [
            "B000261",  # 수원지방법원 성남지원
        ],
    },
}

# 물건 종류 코드 (아파트)
PROPERTY_TYPE_APARTMENT = "001"

# 크롤링 설정
CRAWL_RETRY_COUNT = 3
CRAWL_DELAY_SECONDS = 1.5  # 요청 간격

# DB 설정
DB_PATH = "data/auction.db"

# 지역 enum 허용값
REGION_ENUM = ["서울", "용인수지", "성남분당"]
