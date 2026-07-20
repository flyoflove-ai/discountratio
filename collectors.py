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


def _headers(referer=None):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    h["Accept"] = "application/json, text/html, */*"
    h["Accept-Language"] = "ko-KR,ko;q=0.9,en;q=0.8"
    return h


def _get_json_status(url, referer=None, retries=1):
    """(status_code, json|None) 반환 — 진단 로깅용"""
    last = "NO_RESPONSE"
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=_headers(referer), timeout=10)
            last = r.status_code
            if r.status_code == 200:
                return r.status_code, r.json()
        except ValueError:
            return f"{last}:NOT_JSON", None
        except Exception as e:
            last = f"ERR:{type(e).__name__}"
        time.sleep(0.5 + i)
    return last, None


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
    """'12.3배', '1.44%', '279,500' 등에서 숫자만 추출"""
    if x is None:
        return None
    s = str(x).replace(",", "")
    m = re.search(r'-?\d+\.?\d*', s)
    return float(m.group()) if m else None


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
        # legacy 자동완성 포맷: ["005930", "삼성전자", ...] 문자열 리스트
        strs = [x for x in obj if isinstance(x, str)]
        if strs:
            codes = [x for x in strs if re.fullmatch(r"\d{6}", x)]
            names = [x for x in strs
                     if x and not re.fullmatch(r"[\d,.%]+", x)
                     and not x.startswith("http")]
            if codes and names:
                found.append((codes[0], names[0]))
        for v in obj:
            _extract_stock_items(v, found)


def resolve_stock(query: str, debug: bool = False):
    """
    입력: 종목명 또는 6자리 코드
    반환: ("ok", code, name) | ("ambiguous", [(code, name), ...]) | ("notfound", None)
    debug=True 시 ("debug", [레이어별 결과 문자열]) 반환

    5중 fallback (네이버가 해외 IP를 차단해도 다음/야후 레이어로 커버):
      L1 네이버 자동완성 → L2 네이버 모바일 검색 → L3 네이버 데스크톱 검색(EUC-KR)
      → L4 다음 금융 검색 → L5 야후 파이낸스 검색
    """
    from urllib.parse import quote
    q = query.strip()
    logs = []

    # 6자리 코드면 그대로 사용 (이름은 basic에서 확인)
    if re.fullmatch(r"\d{6}", q):
        basic = _get_json(NAVER_API.format(code=q, path="basic"))
        name = basic.get("stockName") if basic else q
        return ("ok", q, name)

    # ── L1: 네이버 자동완성 (신구 스키마 모두 재귀 추출) ──
    def layer_naver_ac():
        url = (f"https://ac.stock.naver.com/ac?q={quote(q)}"
               f"&target=index%2Cstock%2Cmarketindicator")
        st, data = _get_json_status(url, referer="https://finance.naver.com/")
        found = []
        if data:
            _extract_stock_items(data, found)
        return st, found

    # ── L2: 네이버 모바일 통합검색 ──
    def layer_naver_mobile():
        url = (f"https://m.stock.naver.com/api/search/all"
               f"?query={quote(q)}&page=1&pageSize=10")
        st, data = _get_json_status(url, referer="https://m.stock.naver.com/")
        found = []
        if data:
            _extract_stock_items(data, found)
        return st, found

    # ── L3: 네이버 데스크톱 검색 (구형이지만 안정적, EUC-KR 인코딩 필요) ──
    def layer_naver_desktop():
        try:
            eq = quote(q.encode("euc-kr"))
        except Exception:
            eq = quote(q)
        url = f"https://finance.naver.com/search/searchList.naver?query={eq}"
        try:
            r = requests.get(url, headers=_headers("https://finance.naver.com/"),
                             timeout=10)
            st = r.status_code
            html = r.content.decode("euc-kr", errors="ignore")
            raw = re.findall(
                r'item/main\.naver\?code=(\d{6})[^>]*>(.*?)</a>', html, re.S)
            found = []
            for c, inner in raw:
                nm = re.sub(r'<[^>]+>', '', inner).strip()
                if nm:
                    found.append((c, nm))
            return st, found
        except Exception as e:
            return f"ERR:{type(e).__name__}", []

    # ── L4: 다음 금융 검색 ──
    def layer_daum():
        url = f"https://finance.daum.net/api/search/quotes?q={quote(q)}"
        st, data = _get_json_status(url, referer="https://finance.daum.net/")
        found = []
        if data:
            _extract_stock_items(data, found)
            # 다음은 "A005930" 형식 symbolCode 사용 → 재귀 추출이 놓치면 직접 스캔
            def scan(obj):
                if isinstance(obj, dict):
                    sym = obj.get("symbolCode") or obj.get("symbol") or ""
                    nm = obj.get("name") or obj.get("shortName") or ""
                    m = re.fullmatch(r"A(\d{6})", str(sym))
                    if m and nm:
                        found.append((m.group(1), str(nm).strip()))
                    for v in obj.values():
                        scan(v)
                elif isinstance(obj, list):
                    for v in obj:
                        scan(v)
            scan(data)
        return st, found

    # ── L5: 야후 파이낸스 검색 (미국 IP에서 가장 안정적) ──
    def layer_yahoo():
        url = (f"https://query2.finance.yahoo.com/v1/finance/search"
               f"?q={quote(q)}&quotesCount=10&newsCount=0")
        st, data = _get_json_status(url, referer=None)
        found = []
        for item in (data or {}).get("quotes", []):
            sym = str(item.get("symbol") or "")
            m = re.fullmatch(r"(\d{6})\.(KS|KQ)", sym)
            if m:
                nm = item.get("longname") or item.get("shortname") or q
                found.append((m.group(1), str(nm).strip()))
        return st, found

    layers = [("naver_ac", layer_naver_ac), ("naver_mobile", layer_naver_mobile),
              ("naver_desktop", layer_naver_desktop), ("daum", layer_daum),
              ("yahoo", layer_yahoo)]

    candidates = []
    for name_, fn in layers:
        try:
            st, found = fn()
        except Exception as e:
            st, found = f"ERR:{type(e).__name__}", []
        logs.append(f"{name_}: status={st}, {len(found)}건"
                    + (f" (예: {found[0][1]} {found[0][0]})" if found else ""))
        print(f"[resolve] {logs[-1]}")  # Actions 로그용
        if found and not debug:
            candidates = found
            break
        candidates = candidates or found

    if debug:
        return ("debug", logs)

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
    # 야후 레이어는 영문명 반환 → 정확 일치가 없어도 첫 후보가 사실상 정답인 경우가 많음
    return ("ambiguous", uniq[:5])


# ────────────────────────────────────────────────────────
# 1. 종목 기본 정보 + 컨센서스 (네이버)
# ────────────────────────────────────────────────────────
def fetch_yf_fundamentals(code: str) -> dict:
    """야후 파이낸스 펀더멘털 (네이버 실패 시 fallback): PER, PBR, 배당수익률"""
    out = {"per": None, "pbr": None, "dividend_yield": None}
    for suffix in (".KS", ".KQ"):
        try:
            info = yf.Ticker(f"{code}{suffix}").info or {}
            if not info.get("trailingPE") and not info.get("priceToBook"):
                continue
            out["per"] = info.get("trailingPE")
            out["pbr"] = info.get("priceToBook")
            dy = info.get("dividendYield")
            if dy is not None:
                # yfinance는 0.021 또는 2.1 두 형식이 혼재 → 1 미만이면 비율로 간주
                out["dividend_yield"] = dy * 100 if dy < 1 else dy
            break
        except Exception:
            continue
    return out


def fetch_stock_snapshot(code: str) -> dict:
    """
    반환 dict 키:
      name, price, target_price, per, pbr, industry_per,
      dividend_yield, foreign_rate
    (수집 실패한 항목은 None)
    """
    out = {k: None for k in
           ["name", "price", "target_price", "target_date", "chg_pct",
            "per", "pbr", "industry_per", "dividend_yield", "foreign_rate"]}

    # ── basic: 현재가/종목명/전일비 ──
    basic = _get_json(NAVER_API.format(code=code, path="basic"))
    if basic:
        out["name"] = basic.get("stockName")
        out["price"] = _to_float(basic.get("closePrice"))
        out["chg_pct"] = _to_float(basic.get("fluctuationsRatio"))

    # ── integration: 컨센서스 목표가 + 투자지표 ──
    integ = _get_json(NAVER_API.format(code=code, path="integration"))
    if integ:
        cons = integ.get("consensusInfo") or {}
        out["target_price"] = _to_float(
            cons.get("priceTargetMean") or cons.get("targetPrice"))
        out["target_date"] = cons.get("createDate")

        # industryCompareInfo에서 업종 PER 탐색 (구조 미확정 → 재귀 스캔)
        def _scan_industry_per(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    kl = str(k).lower()
                    if "per" in kl and ("industry" in kl or "upjong" in kl
                                        or "average" in kl or "compare" in kl):
                        fv = _to_float(v)
                        if fv and 0 < fv < 500:
                            return fv
                for v in obj.values():
                    r = _scan_industry_per(v)
                    if r:
                        return r
            elif isinstance(obj, list):
                for v in obj:
                    r = _scan_industry_per(v)
                    if r:
                        return r
            return None
        out["industry_per"] = _scan_industry_per(
            integ.get("industryCompareInfo"))

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

    # ── 최종 fallback: 야후 펀더멘털 (PER/PBR/배당) — 네이버 totalInfos 실패 대비 ──
    if out["per"] is None or out["pbr"] is None or out["dividend_yield"] is None:
        yff = fetch_yf_fundamentals(code)
        for k in ("per", "pbr", "dividend_yield"):
            if out[k] is None:
                out[k] = yff.get(k)

    # ── 배당 정합성: '주당배당금(원)'이 수익률(%)로 오인된 경우 교정 ──
    dy = out.get("dividend_yield")
    if dy is not None and dy > 15:
        if out.get("price"):
            out["dividend_yield"] = dy / out["price"] * 100  # DPS(원) → 수익률(%)
        else:
            out["dividend_yield"] = None  # 판별 불가 시 배제 (왜곡 방지)

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
            r = requests.get(url, headers=_headers("https://finance.naver.com/"),
                             timeout=10)
            html = r.content.decode("euc-kr", errors="ignore")
            # 날짜가 포함된 <tr> 단위로 자른 뒤 태그 전체 제거 → 토큰 파싱 (구조 변화에 강건)
            for rowhtml in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
                if not re.search(r'\d{4}\.\d{2}\.\d{2}', rowhtml):
                    continue
                text = re.sub(r'<[^>]+>', ' ', rowhtml)
                # 숫자형 토큰만 추출: 부호/콤마/%/소수점 허용, 날짜는 제외
                toks = [t for t in re.findall(r'[+\-]?[\d,]+\.?\d*%?', text)
                        if not re.fullmatch(r'\d{4}', t)]
                # 열 구성: 종가, 전일비, 등락률%, 거래량, 기관순매매, 외국인순매매, 보유주수, 보유율%
                pct = [t for t in toks if t.endswith('%')]
                if len(toks) >= 6 and pct:
                    own = _to_float(pct[-1])            # 마지막 % 토큰 = 보유율
                    if own is not None and 0 <= own <= 100:
                        ownership.append(own)
                    plain = [t for t in toks if not t.endswith('%')]
                    if len(plain) >= 3:
                        fnet = _to_float(plain[-2])     # 보유주수 바로 앞 = 외국인 순매매
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
# 3. 매크로 (yfinance) — 실행당 1회 캐시
# ────────────────────────────────────────────────────────
_MACRO_CACHE = None


def fetch_macro() -> dict:
    """미 10Y, VIX, USD/KRW, KOSPI — 1년 히스토리 기반 지표 (실행당 1회만 조회)"""
    global _MACRO_CACHE
    if _MACRO_CACHE is not None:
        return _MACRO_CACHE

    out = {}
    tickers = {"tnx": "^TNX", "vix": "^VIX", "krw": "KRW=X", "kospi": "^KS11",
               "ewy": "EWY"}  # EWY: KOSPI 데이터 교차검증용
    for key, tk in tickers.items():
        try:
            h = yf.Ticker(tk).history(period="1y")["Close"].dropna()
            h = h[h > 0]  # 0/음수 글리치 제거
            if len(h) < 20:
                out[key] = None
                continue
            cur = float(h.iloc[-1])

            def base(n):
                """단일 이상 틱 방어: n일 전 시점 ±2일 평균을 기준가로 사용"""
                if len(h) <= n + 2:
                    return float(h.iloc[0])
                return float(h.iloc[-(n + 2):-(n - 2)].mean())

            out[key] = {
                "current": cur,
                "min": float(h.min()),
                "max": float(h.max()),
                "chg_3m": (cur / base(63) - 1) * 100 if len(h) > 65 else 0.0,
                "chg_20d": (cur / base(20) - 1) * 100,
                "chg_60d": (cur / base(60) - 1) * 100 if len(h) > 62 else 0.0,
            }
        except Exception:
            out[key] = None
    _MACRO_CACHE = out
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


# ────────────────────────────────────────────────────────
# 원격 진단: 네이버 원본 응답 요약 (/raw 명령용)
# ────────────────────────────────────────────────────────
def raw_probe(code: str) -> str:
    """integration/basic/frgn의 실제 응답 구조를 요약해 반환 → 파서 원격 보정용"""
    import json as _json
    lines = [f"🔬 RAW PROBE — {code}"]

    st, basic = _get_json_status(NAVER_API.format(code=code, path="basic"),
                                 referer="https://m.stock.naver.com/")
    lines.append(f"\n[basic] status={st}")
    if basic:
        lines.append("keys: " + ", ".join(list(basic.keys())[:15]))
        lines.append(f"stockName={basic.get('stockName')} "
                     f"closePrice={basic.get('closePrice')}")

    st, integ = _get_json_status(NAVER_API.format(code=code, path="integration"),
                                 referer="https://m.stock.naver.com/")
    lines.append(f"\n[integration] status={st}")
    if integ:
        lines.append("keys: " + ", ".join(list(integ.keys())[:15]))
        cons = integ.get("consensusInfo")
        lines.append("consensusInfo: " + _json.dumps(cons, ensure_ascii=False)[:350])
        ici = integ.get("industryCompareInfo")
        lines.append("industryCompareInfo: " + _json.dumps(ici, ensure_ascii=False)[:500])
        ti = integ.get("totalInfos")
        if isinstance(ti, list):
            lines.append(f"totalInfos {len(ti)}건, 샘플:")
            for item in ti[:6]:
                lines.append("  " + _json.dumps(item, ensure_ascii=False)[:120])
        else:
            lines.append(f"totalInfos 타입: {type(ti).__name__}")

    try:
        r = requests.get(f"https://finance.naver.com/item/frgn.naver?code={code}",
                         headers=_headers("https://finance.naver.com/"), timeout=10)
        html = r.content.decode("euc-kr", errors="ignore")
        n_dates = len(re.findall(r'\d{4}\.\d{2}\.\d{2}', html))
        lines.append(f"\n[frgn] status={r.status_code}, 날짜행 {n_dates}건, "
                     f"길이 {len(html)}")
    except Exception as e:
        lines.append(f"\n[frgn] ERR:{type(e).__name__}")

    return "\n".join(lines)[:3800]  # 텔레그램 메시지 한도 내
