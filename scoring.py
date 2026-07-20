# -*- coding: utf-8 -*-
"""
스코어링 엔진
- 각 요인을 0~100 '할인 압력 점수'로 정규화 (높을수록 주가 할인 압력 큼)
- 총 괴리율 = (목표가 컨센 − 현재가) / 목표가
- 기여도_i = 총 괴리율 × (가중치_i × 압력_i) / Σ(가중치_j × 압력_j)
  → 인과 분해가 아닌 휴리스틱 배분임을 리포트에 명시
"""
from config import (WEIGHTS, CATEGORY_MAP, FACTOR_LABELS,
                    KOSPI_AVG_DIV_YIELD, KOSPI_AVG_PER, GREEN_MAX, YELLOW_MAX)


def _clip(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _band_position(cur, lo, hi):
    """1년 밴드 내 위치 0~100 (상단=100)"""
    if cur is None or lo is None or hi is None or hi <= lo:
        return None
    return _clip((cur - lo) / (hi - lo) * 100)


def _emoji(score):
    if score is None:
        return "⚪"
    if score <= GREEN_MAX:
        return "🟢"
    if score <= YELLOW_MAX:
        return "🟡"
    return "🔴"


# ────────────────────────────────────────────────────────
# 요인별 압력 점수 (0~100)
# ────────────────────────────────────────────────────────
def score_factors(stock, foreign, macro, hist) -> dict:
    """각 요인: {"score": 0~100 or None, "detail": str}"""
    f = {}

    # ── 금리: 밴드 위치 70% + 3개월 상승 추세 30% ──
    tnx = macro.get("tnx")
    if tnx:
        pos = _band_position(tnx["current"], tnx["min"], tnx["max"])
        trend = _clip(50 + tnx["chg_3m"] * 5)  # 3개월 +10% 상승 → 100
        score = _clip(pos * 0.7 + trend * 0.3)
        f["rate"] = {"score": score,
                     "detail": f"미10Y {tnx['current']:.2f}% (1Y밴드 {pos:.0f}% 위치, 3M {tnx['chg_3m']:+.1f}%)"}
    else:
        f["rate"] = {"score": None, "detail": "데이터 없음"}

    # ── 환율: 원화 약세(밴드 상단) = 압력 ──
    krw = macro.get("krw")
    if krw:
        pos = _band_position(krw["current"], krw["min"], krw["max"])
        f["fx"] = {"score": pos,
                   "detail": f"USD/KRW {krw['current']:,.0f} (1Y밴드 {pos:.0f}% 위치)"}
    else:
        f["fx"] = {"score": None, "detail": "데이터 없음"}

    # ── 리스크: VIX 절대 레벨 (12→0, 35→100 선형) ──
    vix = macro.get("vix")
    if vix:
        score = _clip((vix["current"] - 12) / (35 - 12) * 100)
        f["risk"] = {"score": score, "detail": f"VIX {vix['current']:.1f}"}
    else:
        f["risk"] = {"score": None, "detail": "데이터 없음"}

    # ── 외국인 보유율 추이: 60일 변화 -2%p → 100, +2%p → 0 ──
    own = foreign.get("ownership_series") or []
    if len(own) >= 40:
        chg = own[0] - own[min(len(own) - 1, 60)]
        score = _clip(50 - chg * 25)
        f["foreign_trend"] = {"score": score,
                              "detail": f"보유율 {own[0]:.2f}% ({chg:+.2f}%p / {min(len(own),60)}일)"}
    else:
        f["foreign_trend"] = {"score": None, "detail": "데이터 부족"}

    # ── 외국인 20일 순매매: 방향만 반영 ──
    nb = foreign.get("netbuy_20d")
    if nb is not None:
        score = 25 if nb > 0 else 75
        f["foreign_netbuy"] = {"score": score,
                               "detail": f"20일 누적 {'순매수' if nb > 0 else '순매도'} {abs(nb):,.0f}주"}
    else:
        f["foreign_netbuy"] = {"score": None, "detail": "데이터 없음"}

    # ── 시장 모멘텀: KOSPI 20/60일 (하락 = 압력) + EWY 교차검증 ──
    kospi = macro.get("kospi")
    ewy = macro.get("ewy")
    if kospi:
        c20, c60 = kospi["chg_20d"], kospi["chg_60d"]
        suspect = abs(c20) > 12 and (c20 * c60 < 0 or abs(c20) > abs(c60) * 2.5)
        if suspect and ewy:
            # EWY(한국 ETF)도 같은 방향으로 크게 움직였으면 글리치가 아닌 실제 급변동
            e20 = ewy["chg_20d"]
            if c20 * e20 > 0 and abs(e20) > 8:
                suspect = False
        if suspect:
            f["sector_momentum"] = {"score": 50,
                                    "detail": f"⚠️ KOSPI 데이터 이상 의심 (20D {c20:+.1f}%, EWY 교차검증 불일치) → 중립(50) 처리"}
        else:
            mom = c20 * 0.5 + c60 * 0.5
            score = _clip(50 - mom * 5)  # -10% → 100
            f["sector_momentum"] = {"score": score,
                                    "detail": f"KOSPI 20D {c20:+.1f}% / 60D {c60:+.1f}%"}
    else:
        f["sector_momentum"] = {"score": None, "detail": "데이터 없음"}

    # ── 상대강도: 종목 60일 − KOSPI 60일 (소외 = 압력) ──
    s60 = hist.get("chg_60d")
    if s60 is not None and kospi:
        rel = s60 - kospi["chg_60d"]
        score = _clip(50 - rel * 3)  # 시장 대비 -15%p 소외 → ~95
        f["rel_strength"] = {"score": score,
                             "detail": f"60D 상대수익 {rel:+.1f}%p (종목 {s60:+.1f}%)"}
    else:
        f["rel_strength"] = {"score": None, "detail": "데이터 없음"}

    # ── 멀티플: PER vs 업종 (낮을수록 = 이미 할인 반영 = 압력 점수 높음) ──
    per, ind_per, pbr = stock.get("per"), stock.get("industry_per"), stock.get("pbr")
    if per and ind_per and ind_per > 0:
        ratio = per / ind_per
        score = _clip((1 - ratio) * 100 + 50)  # 업종 대비 50% 할인 → 100
        f["multiple"] = {"score": score,
                         "detail": f"PER {per:.1f}x vs 업종 {ind_per:.1f}x" + (f", PBR {pbr:.2f}x" if pbr else "")}
    elif per and per > 0:
        ratio = per / KOSPI_AVG_PER
        score = _clip((1 - ratio) * 100 + 50)  # 시장 평균 대비 근사
        f["multiple"] = {"score": score,
                         "detail": f"PER {per:.1f}x vs KOSPI평균 {KOSPI_AVG_PER:.0f}x" + (f", PBR {pbr:.2f}x" if pbr else "")}
    elif pbr:
        score = _clip((1.0 - pbr) * 60 + 50)  # PBR 1배 기준 근사
        f["multiple"] = {"score": score, "detail": f"PBR {pbr:.2f}x (업종 PER 미확보)"}
    else:
        f["multiple"] = {"score": None, "detail": "데이터 없음"}

    # ── 주주환원: 배당수익률 vs KOSPI 평균 (낮으면 환원 미흡 = 압력) ──
    dy = stock.get("dividend_yield")
    if dy is not None:
        score = _clip(50 + (KOSPI_AVG_DIV_YIELD - dy) * 20)
        f["shareholder"] = {"score": score,
                            "detail": f"배당수익률 {dy:.2f}% (시장평균 {KOSPI_AVG_DIV_YIELD:.1f}%)"}
    else:
        f["shareholder"] = {"score": None, "detail": "데이터 없음"}

    return f


# ────────────────────────────────────────────────────────
# 총 괴리율 + 기여도 배분
# ────────────────────────────────────────────────────────
def build_report(code, stock, factors) -> str:
    name = stock.get("name") or code
    price, target = stock.get("price"), stock.get("target_price")

    lines = [f"📊 <b>{name} ({code}) 할인율 진단</b>", ""]

    if price and target and target > 0:
        gap = (target - price) / target * 100
        chg = stock.get("chg_pct")
        chg_str = f" (전일比 {chg:+.1f}%)" if chg is not None else ""
        tdate = stock.get("target_date")
        tdate_str = f" (컨센 기준 {tdate})" if tdate else ""
        lines.append(f"현재가 {price:,.0f}원{chg_str} / 목표가 컨센 {target:,.0f}원{tdate_str}")
        if gap >= 0:
            lines.append(f"➡️ <b>총 할인율(괴리율): {gap:.1f}%</b>")
            if gap > 35:
                lines.append("⚠️ <i>괴리율이 이례적으로 큼 — 급락 국면에서 컨센서스는 후행하므로 "
                             "목표가 하향 리비전 리스크를 감안해 해석할 것</i>")
        else:
            lines.append(f"➡️ <b>목표가 대비 프리미엄: {abs(gap):.1f}%</b> (목표가 상향 후행 가능성)")
    else:
        gap = None
        lines.append("⚠️ 현재가/목표가 컨센서스 수집 실패 — 압력 점수만 표시")
    lines.append("")

    # 유효 요인만 배분에 사용 — 실패 요인의 가중치 몫은 '미배분'으로 정직하게 표시
    valid = {k: v for k, v in factors.items() if v["score"] is not None}
    total_wp = sum(WEIGHTS[k] * v["score"] for k, v in valid.items()) or 1.0
    coverage = sum(WEIGHTS[k] for k in valid) / sum(WEIGHTS.values())  # 0~1
    alloc_gap = (gap * coverage) if (gap is not None and gap > 0) else None

    # 카테고리별 집계
    cat_contrib, cat_score_w, cat_w = {}, {}, {}
    for k, v in valid.items():
        cat = CATEGORY_MAP[k]
        wp = WEIGHTS[k] * v["score"]
        cat_contrib[cat] = cat_contrib.get(cat, 0) + wp
        cat_score_w[cat] = cat_score_w.get(cat, 0) + wp
        cat_w[cat] = cat_w.get(cat, 0) + WEIGHTS[k]

    lines.append("<b>■ 요인별 진단</b>")
    for cat in ["매크로", "수급", "업황/모멘텀", "멀티플", "거버넌스/주주환원"]:
        keys = [k for k in WEIGHTS if CATEGORY_MAP[k] == cat]
        if not any(k in valid for k in keys):
            continue
        avg = cat_score_w.get(cat, 0) / max(cat_w.get(cat, 1), 1)
        share = cat_contrib.get(cat, 0) / total_wp * 100
        head = f"{_emoji(avg)} <b>{cat}</b> — 압력 {avg:.0f}/100"
        if alloc_gap is not None:
            head += f", 할인 기여 {alloc_gap * share / 100:.1f}%p"
        lines.append(head)
        for k in keys:
            v = factors[k]
            lines.append(f"   · {FACTOR_LABELS[k]}: {v['detail']}"
                         + (f" [{v['score']:.0f}]" if v["score"] is not None else ""))
        lines.append("")

    # 수집 실패 항목 + 미배분 몫 명시 (조용한 생략/왜곡 방지)
    missing = [FACTOR_LABELS[k] for k, v in factors.items() if v["score"] is None]
    if missing:
        lines.append("⚪ <b>수집 실패로 제외</b>: " + ", ".join(missing))
        if gap is not None and gap > 0:
            lines.append(f"   → 총 할인율 중 <b>{gap * (1 - coverage):.1f}%p는 미배분</b> "
                         f"(데이터 커버리지 {coverage*100:.0f}%)")
        lines.append("")

    # 구조 vs 시점 할인 구분
    if gap is not None and gap > 0:
        cyc = sum(cat_contrib.get(c, 0) for c in ["매크로", "수급", "업황/모멘텀"]) / total_wp
        struct = sum(cat_contrib.get(c, 0) for c in ["멀티플", "거버넌스/주주환원"]) / total_wp
        lines.append(f"<b>■ 할인 성격</b>: 시점(사이클) {cyc*100:.0f}% vs 구조 {struct*100:.0f}%")
        lines.append("")

    lines.append("<i>※ 기여도는 가중 압력점수 기반 휴리스틱 배분 (인과 분해 아님). "
                 "투자 판단의 참고 자료이며 자문이 아님.</i>")
    return "\n".join(lines)
