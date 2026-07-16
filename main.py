# -*- coding: utf-8 -*-
"""
할인율 진단 텔레봇 — GitHub Actions 크론 실행 (기존 리서치 에이전트와 동일 패턴)

동작:
1. getUpdates로 미처리 메시지 수집 (Telegram 서버가 24h 보관 → 무상태 가능)
2. /discount <종목코드> 명령 처리 → 데이터 수집 → 스코어링 → 회신
3. 처리 후 offset 확정(acknowledge)하여 중복 처리 방지
4. 실패 시 Telegram 오류 알림 (silent failure 방지)

명령:
  /discount 005930   특정 종목 할인율 진단
  /d 005930          단축 명령
  /help              도움말
"""
import re
import sys
import traceback
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from collectors import (fetch_stock_snapshot, fetch_foreign_trend,
                        fetch_macro, fetch_stock_history)
from scoring import score_factors, build_report

API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HELP_TEXT = (
    "📊 <b>할인율 진단 봇</b>\n\n"
    "/discount 종목코드 — 목표가 컨센서스 대비 할인율과 "
    "요인별(매크로/수급/업황·모멘텀/멀티플/거버넌스) 기여도를 진단합니다.\n\n"
    "예: <code>/discount 005930</code> (삼성전자)\n"
    "단축: <code>/d 000660</code>\n\n"
    "⏱ GitHub Actions 크론 실행이라 응답까지 최대 15분 지연될 수 있습니다."
)


def send(chat_id, text):
    requests.post(f"{API}/sendMessage", json={
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }, timeout=15)


def get_updates():
    r = requests.get(f"{API}/getUpdates", params={"timeout": 0}, timeout=20)
    return r.json().get("result", [])


def ack(last_update_id):
    """offset 확정 → 다음 실행에서 중복 처리 방지"""
    requests.get(f"{API}/getUpdates",
                 params={"offset": last_update_id + 1, "limit": 1, "timeout": 0},
                 timeout=20)


def handle_discount(chat_id, code):
    send(chat_id, f"🔍 {code} 진단 중... (약 30초 소요)")
    stock = fetch_stock_snapshot(code)
    foreign = fetch_foreign_trend(code)
    macro = fetch_macro()
    hist = fetch_stock_history(code)
    factors = score_factors(stock, foreign, macro, hist)
    send(chat_id, build_report(code, stock, factors))


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN 미설정", file=sys.stderr)
        sys.exit(1)

    updates = get_updates()
    if not updates:
        print("처리할 메시지 없음")
        return

    last_id = updates[-1]["update_id"]
    macro_cache_used = False

    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if not chat_id or not text:
            continue

        try:
            m = re.match(r"^/(discount|d)(?:@\w+)?\s+(\d{6})\s*$", text)
            if m:
                handle_discount(chat_id, m.group(2))
            elif text.startswith(("/help", "/start")):
                send(chat_id, HELP_TEXT)
            elif text.startswith(("/discount", "/d")):
                send(chat_id, "형식: <code>/discount 005930</code> (6자리 종목코드)")
        except Exception:
            err = traceback.format_exc()
            print(err, file=sys.stderr)
            try:
                send(chat_id, f"⚠️ 처리 중 오류 발생:\n<code>{err[-500:]}</code>")
            except Exception:
                pass

    ack(last_id)
    print(f"{len(updates)}건 처리 완료")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # silent failure 방지 — 매크로 대시보드와 동일한 실패 알림 원칙
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            try:
                send(TELEGRAM_CHAT_ID, f"🔴 할인율 봇 실행 실패:\n<code>{err[-500:]}</code>")
            except Exception:
                pass
        sys.exit(1)
