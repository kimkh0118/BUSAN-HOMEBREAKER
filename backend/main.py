from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import requests
import xml.etree.ElementTree as ET
import os
import re
import time
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import date

# ── 환경변수 (키는 코드에 쓰지 말고 터미널에서 설정) ──────────────
#   export JUSO_KEY="..."  DATA_KEY="..."
JUSO_KEY = os.environ.get("JUSO_KEY", "")
DATA_KEY = os.environ.get("DATA_KEY", "")
DEBUG = os.environ.get("DEBUG_API", "1") == "1"

for _name, _val in (("JUSO_KEY", JUSO_KEY), ("DATA_KEY", DATA_KEY)):
    if not _val:
        print(f"⚠️  환경변수 {_name} 가 비어 있습니다. 해당 조회는 건너뜁니다.")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "..", "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def read_root():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Frontend index.html not found"}


JUSO_SEARCH_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
BUILDING_HUB_URL = "http://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"

SH_TRADE_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade"
TRADE_MONTHS = 12          # 최근 몇 개월 거래를 볼지
CACHE_TTL = 6 * 3600       # 월별 조회 결과 캐시(초)
_trade_cache = {}

PY = 0.3025  # ㎡ → 평


def log(*args):
    if DEBUG:
        print(*args)


def to_float(v):
    try:
        return float(v or 0)
    except (ValueError, TypeError):
        return 0.0


def fetch_buildings(sigungu, bjdong, plat_gb, bun, ji):
    """건축HUB 표제부 → 동별 목록"""
    if not (DATA_KEY and sigungu and bjdong):
        return []
    params = {
        'serviceKey': DATA_KEY, 'sigunguCd': sigungu, 'bjdongCd': bjdong,
        'platGbCd': plat_gb, 'bun': bun, 'ji': ji,
        'numOfRows': '100', 'pageNo': '1', '_type': 'json',
    }
    rows = []
    try:
        res = requests.get(BUILDING_HUB_URL, params=params, timeout=6)
        log("[HUB]", sigungu, bjdong, bun, ji, res.status_code, res.text[:200])
        if res.status_code != 200:
            return []
        try:
            items = res.json().get('response', {}).get('body', {}).get('items', {})
            raw = items.get('item', []) if isinstance(items, dict) else []
            rows = raw if isinstance(raw, list) else [raw]
        except Exception:
            root = ET.fromstring(res.content)
            for it in root.findall('.//item'):
                rows.append({c.tag: (c.text or '') for c in it})
    except Exception as e:
        log("건축HUB 오류:", e)
        return []

    out = []
    for b in rows:
        tot = to_float(b.get('totArea'))
        out.append({
            'bldNm': (b.get('bldNm') or '').strip(),
            'dongNm': (b.get('dongNm') or '').strip(),
            'mainPurpsCdNm': b.get('mainPurpsCdNm') or '-',
            'strctCdNm': b.get('strctCdNm') or '-',
            'platAreaM2': round(to_float(b.get('platArea')), 2),
            'totAreaM2': round(tot, 2),
            'totAreaPy': round(tot * PY, 2),
            'grndFlrCnt': b.get('grndFlrCnt') or '0',
            'ugrndFlrCnt': b.get('ugrndFlrCnt') or '0',
            'useAprDay': b.get('useAprDay') or '',
        })
    return out



# ── 시세(실거래가) ─────────────────────────────────────────────
def pick(d, *keys):
    """영문/한글 태그 이름 둘 다 대응"""
    for k in keys:
        v = d.get(k)
        if v not in (None, ''):
            return v
    return ''


def recent_months(n):
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def fetch_trade_month(sigungu, ymd):
    """단독/다가구 매매 실거래 1개월분 → (rows, error)"""
    key = (sigungu, ymd)
    hit = _trade_cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1], None

    params = {'serviceKey': DATA_KEY, 'LAWD_CD': sigungu, 'DEAL_YMD': ymd,
              'numOfRows': '1000', 'pageNo': '1'}
    rows, err, text = [], None, ''
    try:
        res = requests.get(SH_TRADE_URL, params=params, timeout=8)
        text = res.text
        log("[TRADE]", sigungu, ymd, res.status_code, text[:200].replace("\n", " "))
        root = ET.fromstring(res.content)
        code = root.findtext('.//resultCode')
        if code and code not in ('00', '000'):
            err = root.findtext('.//resultMsg') or f"resultCode {code}"
        elif root.findtext('.//errMsg'):
            err = root.findtext('.//returnAuthMsg') or root.findtext('.//errMsg')
        else:
            for it in root.iter('item'):
                rows.append({c.tag: (c.text or '').strip() for c in it})
            if rows and not getattr(fetch_trade_month, '_logged', False):
                fetch_trade_month._logged = True
                log("[TRADE 필드]", rows[0])
    except Exception as e:
        err = (text[:120] or str(e)).strip()

    if err is None:
        _trade_cache[key] = (time.time(), rows)
    return rows, err


def norm_trade(r):
    if pick(r, 'cdealType', '해제여부') == 'O':   # 계약 해제 건 제외
        return None
    amount = to_float(pick(r, 'dealAmount', '거래금액').replace(',', ''))   # 만원
    if amount <= 0:
        return None
    y, m, d = pick(r, 'dealYear', '년'), pick(r, 'dealMonth', '월'), pick(r, 'dealDay', '일')
    return {
        'date': f"{y}-{str(m).zfill(2)}-{str(d).zfill(2)}",
        'dong': pick(r, 'umdNm', '법정동'),
        'jibun': pick(r, 'jibun', '지번'),
        'houseType': pick(r, 'houseType', '주택유형'),
        'amount': amount,
        'totM2': to_float(pick(r, 'totalFloorAr', 'totalFloorArea', 'buildingAr', 'totArea', '연면적', '건물면적')),
        'platM2': to_float(pick(r, 'plottageAr', '대지면적')),
        'buildYear': pick(r, 'buildYear', '건축년도'),
    }


def quartiles(vals):
    q1, q2, q3 = statistics.quantiles(vals, n=4, method='inclusive')
    return q1, q2, q3


def estimate_market(trades, plat_m2, tot_m2):
    """인근 거래의 ㎡당 가격(사분위)에 내 면적을 곱한 참고 추정 (만원)"""
    est = {}
    for name, field, area in (('byLand', 'platM2', plat_m2), ('byFloor', 'totM2', tot_m2)):
        unit = [t['amount'] / t[field] for t in trades if t[field] > 0]
        if len(unit) >= 3 and area > 0:
            q1, q2, q3 = quartiles(unit)
            est[name] = {'low': round(q1 * area), 'mid': round(q2 * area),
                         'high': round(q3 * area), 'unitMid': round(q2, 1), 'n': len(unit)}
    return est


def fetch_market(sigungu, dong, plat_m2, tot_m2):
    if not (DATA_KEY and sigungu):
        return {'error': 'DATA_KEY 또는 시군구 코드가 없습니다.'}
    months = recent_months(TRADE_MONTHS)
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda ym: fetch_trade_month(sigungu, ym), months))

    raw = [r for rows, _ in results for r in rows]
    errors = [e for _, e in results if e]
    if not raw and errors:
        return {'error': errors[0]}

    trades = [t for t in (norm_trade(r) for r in raw) if t]
    same_dong = [t for t in trades if t['dong'] == dong] if dong else trades
    trades = sorted(same_dong, key=lambda t: t['date'], reverse=True)
    return {
        'error': None,
        'months': TRADE_MONTHS,
        'dong': dong,
        'count': len(trades),
        'estimate': estimate_market(trades, plat_m2, tot_m2),
        'recent': trades[:10],
    }


@app.get("/api/search-building")
def search_building_by_address(address: str = Query(..., description="조회할 주소")):
    if not JUSO_KEY:
        return {"status": "error", "message": "서버에 JUSO_KEY 환경변수가 설정되지 않았습니다.", "data": []}
    try:
        cleaned = re.sub(r'[^\w\s]', '', address).strip()
        res_juso = requests.get(JUSO_SEARCH_URL, params={
            'confmKey': JUSO_KEY, 'currentPage': '1', 'countPerPage': '5',
            'keyword': cleaned, 'resultType': 'json'}, timeout=5)
        res_juso.raise_for_status()
        results_data = res_juso.json().get('results', {})
        common = results_data.get('common', {})
        if common.get('errorCode') != "0":
            return {"status": "error", "message": f"주소 API 오류: {common.get('errorMessage')}", "data": []}

        juso_list = results_data.get('juso', [])
        if not juso_list:
            return {"status": "error", "message": "해당 주소의 검색 결과가 없습니다.", "data": []}

        results = []
        for item in juso_list:
            admCd = item.get('admCd', '')
            mtYn = str(item.get('mtYn', '0'))          # 0: 대지, 1: 산
            bun = str(item.get('lnbrMnnm') or '0').zfill(4)
            ji = str(item.get('lnbrSlno') or '0').zfill(4)
            bdMgtSn = item.get('bdMgtSn', '')

            sigungu = admCd[:5] if len(admCd) >= 10 else ""
            bjdong = admCd[5:10] if len(admCd) >= 10 else ""

            # 건축HUB: platGbCd 0=대지, 1=산
            buildings = fetch_buildings(sigungu, bjdong, mtYn, bun, ji)

            # PNU: 법정동코드(10) + 1(일반)/2(산) + 본번(4) + 부번(4)
            pnu = ""
            if len(admCd) >= 10:
                pnu = admCd[:10] + ('2' if mtYn == '1' else '1') + bun + ji
            log("[PNU]", item.get('jibunAddr'), pnu)

            plat_area = max((b['platAreaM2'] for b in buildings), default=0)
            tot_area = sum(b['totAreaM2'] for b in buildings)
            market = fetch_market(sigungu, (item.get('emdNm') or '').strip(), plat_area, tot_area)
            raw_nm = (item.get('bdNm') or '').strip()
            hub_nm = next((b['bldNm'] for b in buildings if b['bldNm']), '')
            title = raw_nm or hub_nm or item.get('roadAddr', '건물 정보')

            results.append({
                'displayTitle': title,
                'roadAddr': item.get('roadAddr'),
                'jibunAddr': item.get('jibunAddr'),
                'bdMgtSn': bdMgtSn,
                'pnu': pnu,
                'platAreaM2': plat_area,
                'platAreaPy': round(plat_area * PY, 2),
                'totalAreaM2': round(sum(b['totAreaM2'] for b in buildings), 2),
                'buildings': buildings,
                'market': market,
            })
        return {"status": "success", "data": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)