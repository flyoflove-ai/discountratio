# -*- coding: utf-8 -*-
"""
데이터 수집기 — 전부 무료 소스
- 종목: 네이버 모바일 증권 API (m.stock.naver.com) + 외국인 보유율 HTML(frgn 페이지)
- 매크로: yfinance (^TNX, ^VIX, KRW=X, ^KS11)

주의: 네이버 API 응답 스키마는 예고 없이 바뀔 수 있음.
     모든 파싱은 .get 체인 + 예외 처리로 감싸 실패해도 부분 결과 반환.
"""
import re
import time
import requests
import yfinance as yf

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://m.stock.naver.com/",
}

NAVER_API = "https://m.stock.naver.com/api/stock/{code}/{path}"


def _get_json(url, retries=2):
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1 + i)
    return None


def _to_float(x):
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


# ────────────────────────────────────────────────────────
# 0. 종목명 → 코드 변환 (네이버 검색)
# ────────────────────────────────────────────────────────
def _extract_stock_items(obj, found):
    """JSON 어디에 있든 {6자리 코드 + 종목명} 딕셔너리를 재귀 탐색 (스키마 변동 대비)"""
    if isinstance(obj, dict):
        code = None
        for key in ("itemCode", "code", "cd", "stockCode"):
            v = obj.get(key)
            if isinstance(v, str) and re.fullmatch(r"\d{6}", v):
                code = v
                break
        name = None
        for key in ("stockName", "name", "nm", "itemName"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                name = v.strip()
                break
        if code and name:
            found.append((code, name))
        for v in obj.values():
            _extract_stock_items(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _extract_stock_items(v, found)


def resolve_stock(query: str):
    """
    입력: 종목명 또는 6자리 코드
    반환: ("ok", code, name) | ("ambiguous", [(code, name), ...]) | ("notfound", None)
    """
    from urllib.parse import quote
    q = query.strip()

    # 6자리 코드면 그대로 사용 (이름은 basic에서 확인)
    if re.fullmatch(r"\d{6}", q):
        basic = _get_json(NAVER_API.format(code=q, path="basic"))
        name = basic.get("stockName") if basic else q
        return ("ok", q, name)

    candidates = []

    # 1차: 네이버 모바일 통합검색 API
    data = _get_json(f"https://m.stock.naver.com/api/search/all"
                     f"?query={quote(q)}&page=1&pageSize=10")
    if data:
        _extract_stock_items(data, candidates)

    # 2차 fallback: 네이버 자동완성 (legacy, 리스트 of 리스트 형식)
    if not candidates:
        try:
            r = requests.get(
                f"https://ac.stock.naver.com/ac?q={quote(q)}"
                f"&target=stock&st=111&r_lt=111",
                headers=HEADERS, timeout=10)
            for group in r.json().get("items", []):
                for item in group:
                    flat = [x[0] if isinstance(x, list) else x for x in item]
                    codes = [x for x in flat
                             if isinstance(x, str) and re.fullmatch(r"\d{6}", x)]
                    names = [x for x in flat
                             if isinstance(x, str) and x and not x.isdigit()
                             and not re.fullmatch(r"\d{6}", x)]
                    if codes and names:
                        candidates.append((codes[0], names[0]))
        except Exception:
            pass

    # 중복 제거 (순서 유지)
    seen, uniq = set(), []
    for c, n in candidates:
        if c not in seen:
            seen.add(c)
            uniq.append((c, n))

    if not uniq:
        return ("notfound", None)

    # 정확히 일치하는 이름 우선 (예: '삼성전자' vs '삼성전자우')
    exact = [(c, n) for c, n in uniq if n == q]
    if exact:
        return ("ok", exact[0][0], exact[0][1])
    if len(uniq) == 1:
        return ("ok", uniq[0][0], uniq[0][1])
    # 첫 후보가 쿼리로 시작하고 나머지는 파생(우선주 등)인 경우가 대부분 → 상위 5개 제시
    return ("ambiguous", uniq[:5])


# ────────────────────────────────────────────────────────
# 1. 종목 기본 정보 + 컨센서스 (네이버)
# ────────────────────────────────────────────────────────
def fetch_stock_snapshot(code: str) -> dict:
    """
    반환 dict 키:
      name, price, target_price, per, pbr, industry_per,
      dividend_yield, foreign_rate
    (수집 실패한 항목은 None)
    """
    out = {k: None for k in
           ["name", "price", "target_price", "per", "pbr",
            "industry_per", "dividend_yield", "foreign_rate"]}

    # ── basic: 현재가/종목명 ──
    basic = _get_json(NAVER_API.format(code=code, path="basic"))
    if basic:
        out["name"] = basic.get("stockName")
        out["price"] = _to_float(basic.get("closePrice"))

    # ── integration: 컨센서스 목표가 + 투자지표 ──
    integ = _get_json(NAVER_API.format(code=code, path="integration"))
    if integ:
        cons = integ.get("consensusInfo") or {}
        out["target_price"] = _to_float(
            cons.get("priceTargetMean") or cons.get("targetPrice"))

        # totalInfos: [{code: "per", value: "12.3배"}, ...] 형태
        for item in integ.get("totalInfos", []):
            c = (item.get("code") or "").lower()
            v = _to_float(item.get("value"))
            if c == "per":
                out["per"] = v
            elif c == "pbr":
                out["pbr"] = v
            elif c in ("industrype", "industryper", "industry_per"):
                out["industry_per"] = v
            elif "dividend" in c or c == "dividendyieldratio":
                out["dividend_yield"] = v
            elif "foreign" in c:
                out["foreign_rate"] = v

    # ── fallback: finance.naver.com 메인 페이지 스크레이핑 ──
    if out["price"] is None or out["target_price"] is None:
        try:
            html = requests.get(
                f"https://finance.naver.com/item/main.naver?code={code}",
                headers=HEADERS, timeout=10).text
            if out["price"] is None:
                m = re.search(r'현재가\s*([\d,]+)', html)
                if not m:
                    m = re.search(r'<dd>현재가\s*([\d,]+)', html)
                if m:
                    out["price"] = _to_float(m.group(1))
            if out["target_price"] is None:
                # 투자의견 테이블 내 목표주가
                m = re.search(r'목표주가.*?<em>([\d,]+)</em>', html, re.S)
                if m:
                    out["target_price"] = _to_float(m.group(1))
        except Exception:
            pass

    return out


# ────────────────────────────────────────────────────────
# 2. 외국인 보유율 추이 + 순매매 (finance.naver.com/item/frgn)
# ────────────────────────────────────────────────────────
def fetch_foreign_trend(code: str, pages: int = 4) -> dict:
    """
    frgn 페이지 일별 테이블 파싱.
    반환: {ownership_series: [최근→과거 보유율 %], netbuy_20d: 외국인 20일 누적 순매매(주)}
    """
    ownership, netbuy = [], []
    for page in range(1, pages + 1):
        try:
            url = (f"https://finance.naver.com/item/frgn.naver"
                   f"?code={code}&page={page}")
            html = requests.get(url, headers=HEADERS, timeout=10).text
            # 행 패턴: 날짜 | 종가 | 전일비 | 등락률 | 거래량 | 기관 | 외국인 | 보유주수 | 보유율
            rows = re.findall(
                r'<td[^>]*>(\d{4}\.\d{2}\.\d{2})</td>(.*?)</tr>', html, re.S)
            for _, body in rows:
                cells = re.findall(r'<td[^>]*>\s*(?:<span[^>]*>)?([^<]*)', body)
                cells = [c.strip() for c in cells if c.strip()]
                nums = [c for c in cells if re.match(r'^[+\-]?[\d,.]+%?$', c)]
                if len(nums) >= 7:
                    own = _to_float(nums[-1])           # 마지막 = 보유율(%)
                    fnet = _to_float(nums[-3])          # 외국인 순매매량
                    if own is not None:
                        ownership.append(own)
                    if fnet is not None:
                        netbuy.append(fnet)
        except Exception:
            break
        time.sleep(0.3)

    return {
        "ownership_series": ownership,          # index 0 = 최신
        "netbuy_20d": sum(netbuy[:20]) if netbuy else None,
    }


# ────────────────────────────────────────────────────────
# 3. 매크로 (yfinance)
# ────────────────────────────────────────────────────────
def fetch_macro() -> dict:
    """미 10Y, VIX, USD/KRW, KOSPI — 1년 히스토리 기반 지표"""
    out = {}
    tickers = {"tnx": "^TNX", "vix": "^VIX", "krw": "KRW=X", "kospi": "^KS11"}
    for key, tk in tickers.items():
        try:
            h = yf.Ticker(tk).history(period="1y")["Close"].dropna()
            if len(h) < 20:
                out[key] = None
                continue
            cur = float(h.iloc[-1])
            out[key] = {
                "current": cur,
                "min": float(h.min()),
                "max": float(h.max()),
                "chg_3m": (cur / float(h.iloc[-63]) - 1) * 100 if len(h) > 63 else 0.0,
                "chg_20d": (cur / float(h.iloc[-20]) - 1) * 100,
                "chg_60d": (cur / float(h.iloc[-60]) - 1) * 100 if len(h) > 60 else 0.0,
                "series": h,
            }
        except Exception:
            out[key] = None
    return out


def fetch_stock_history(code: str) -> dict:
    """종목 60일 수익률 (KOSPI 대비 상대강도용) — yfinance .KS 티커"""
    try:
        h = yf.Ticker(f"{code}.KS").history(period="6mo")["Close"].dropna()
        if len(h) < 60:
            h = yf.Ticker(f"{code}.KQ").history(period="6mo")["Close"].dropna()
        if len(h) >= 60:
            return {"chg_60d": (float(h.iloc[-1]) / float(h.iloc[-60]) - 1) * 100}
    except Exception:
        pass
    return {"chg_60d": None}
