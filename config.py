# -*- coding: utf-8 -*-
"""
할인율 진단 봇 설정
- 가중치 합계 = 100 (매크로 대시보드와 동일한 스코어링 철학)
- 각 요인은 0~100 '할인 압력 점수'로 정규화됨 (높을수록 할인 압력 큼)
"""
import os

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")  # 미지정 시 수신 chat에 회신

# ── 요인 가중치 (합계 100) ──────────────────────────────
WEIGHTS = {
    # ① 매크로 (30)
    "rate":            10,   # 미 10Y 레벨 + 추세
    "fx":              10,   # USD/KRW 1년 밴드 위치
    "risk":            10,   # VIX 레벨
    # ② 수급 (25)
    "foreign_trend":   15,   # 외국인 보유율 60일 변화
    "foreign_netbuy":  10,   # 외국인 최근 20일 순매매
    # ③ 업황/모멘텀 (25)
    "sector_momentum": 12,   # 업종/시장 지수 모멘텀
    "rel_strength":    13,   # 종목 vs KOSPI 상대강도 (60일)
    # ④ 멀티플 (10)
    "multiple":        10,   # PER/PBR vs 업종 평균
    # ⑤ 거버넌스/주주환원 (10)
    "shareholder":     10,   # 배당수익률 vs 시장 평균
}

CATEGORY_MAP = {
    "rate": "매크로", "fx": "매크로", "risk": "매크로",
    "foreign_trend": "수급", "foreign_netbuy": "수급",
    "sector_momentum": "업황/모멘텀", "rel_strength": "업황/모멘텀",
    "multiple": "멀티플",
    "shareholder": "거버넌스/주주환원",
}

FACTOR_LABELS = {
    "rate": "금리 (미 10Y)",
    "fx": "환율 (USD/KRW)",
    "risk": "리스크 (VIX)",
    "foreign_trend": "외국인 보유율 추이",
    "foreign_netbuy": "외국인 순매매 (20일)",
    "sector_momentum": "시장 모멘텀 (KOSPI)",
    "rel_strength": "상대강도 (vs KOSPI)",
    "multiple": "멀티플 (PER/PBR vs 업종)",
    "shareholder": "주주환원 (배당수익률)",
}

# 시장 평균 배당수익률 가정 (KOSPI, 필요 시 조정)
KOSPI_AVG_DIV_YIELD = 2.0

# 신호등 임계값 (압력 점수 기준)
GREEN_MAX = 35
YELLOW_MAX = 65
