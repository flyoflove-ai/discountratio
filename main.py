# -*- coding: utf-8 -*-
"""
할인율 진단 텔레봇 — GitHub Actions 5분 크론 폴링 (순수 GitHub, 외부 서비스 없음)

사용법: 봇에게 종목명 또는 코드를 그냥 보내면 진단
  삼성전자
  SK하이닉스
  005930
  /help — 도움말

동작:
1. 실행 시 deleteWebhook 호출 (과거 웹훅 설정이 남아있어도 자동 정리)
2. getUpdates로 미처리 메시지 일괄 수집 → 각각 진단 후 회신
3. offset 확정(acknowledge)으로 중복 처리 방지 → 무상태 실행 가능
4. 실행 자체가 실패하면 Telegram 오류 알림 (silent failure 방지)
"""
import re
import sys
import traceback
import requests

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from collectors import (resolve_stock, fetch_stock_snapshot,
                        fetch_foreign_trend, fetch_macro, fetch_stock_history)
from scoring import score_factors, build_report

API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

HELP_TEXT = (
    "📊 <b>할인율 진단 봇</b>\n\n"
    "종목명이나 종목코드를 그냥 보내면 진단합니다.\n"
    "예: <code>삼성전자</code> / <code>SK하이닉스</code> / <code>005930</code>\n\n"
    "목표가 컨센서스 대비 할인율과 요인별"
    "(매크로/수급/업황·모멘텀/멀티플/거버넌스) 기여도를 회신합니다.\n\n"
    "⏱ 5분 주기 실행이라 응답까지 보통 2~10분 걸립니다."
)


def send(chat_id, text):
    requests.post(f"{API}/sendMessage", json={
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }, timeout=15)


def run_diagnosis(chat_id, code, name):
    send(chat_id, f"🔍 <b>{name}</b> ({code}) 진단 중... (약 30초)")
    stock = fetch_stock_snapshot(code)
    if not stock.get("name"):
        stock["name"] = name
    foreign = fetch_foreign_trend(code)
    macro = fetch_macro()
    hist = fetch_stock_history(code)
    factors = score_factors(stock, foreign, macro, hist)
    send(chat_id, build_report(code, stock, factors))


def handle_text(chat_id, text):
    text = text.strip()

    # 명령어 처리
    if text.startswith(("/help", "/start")):
        send(chat_id, HELP_TEXT)
        return
    if text.startswith("/debug"):
        # 원격 진단: 검색 레이어별 상태 회신 (예: /debug 삼성전자)
        m = re.match(r"^/debug(?:@\w+)?\s+(.+)$", text)
        q = m.group(1).strip() if m else "삼성전자"
        _, logs = resolve_stock(q, debug=True)
        send(chat_id, "🔧 <b>검색 레이어 진단</b> — '" + q + "'\n\n"
             + "\n".join(f"· <code>{l}</code>" for l in logs))
        return
    if text.startswith("/"):
        # 구버전 호환: /discount 삼성전자, /d 005930
        m = re.match(r"^/(discount|d)(?:@\w+)?\s+(.+)$", text)
        if m:
            text = m.group(2).strip()
        else:
            send(chat_id, HELP_TEXT)
            return

    # 평문 = 종목명/코드 → 진단
    query = text
    if len(query) > 30:  # 종목명이 아닌 긴 텍스트는 무시
        send(chat_id, "종목명 또는 6자리 코드를 보내주세요. 예: <code>삼성전자</code>")
        return

    status, *rest = resolve_stock(query)
    if status == "ok":
        code, name = rest[0], rest[1] if len(rest) > 1 else rest[0]
        run_diagnosis(chat_id, code, name)
    elif status == "ambiguous":
        cands = rest[0]
        lines = ["🔎 여러 종목이 검색됐습니다. 정확한 이름이나 코드로 다시 보내주세요:\n"]
        lines += [f"· {n} — <code>{c}</code>" for c, n in cands]
        send(chat_id, "\n".join(lines))
    else:
        send(chat_id, f"'{query}' 종목을 찾지 못했습니다. "
                      "정식 종목명 또는 6자리 코드로 다시 시도해주세요.")


def process_update(u):
    msg = u.get("message") or u.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = (msg.get("chat") or {}).get("id")
    if not chat_id or not text:
        return
    try:
        handle_text(chat_id, text)
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        try:
            send(chat_id, f"⚠️ 처리 중 오류:\n<code>{err[-500:]}</code>")
        except Exception:
            pass


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN 미설정", file=sys.stderr)
        sys.exit(1)

    # 과거 웹훅 설정이 남아있으면 getUpdates가 막히므로 항상 해제 (멱등)
    try:
        requests.get(f"{API}/deleteWebhook", timeout=15)
    except Exception:
        pass

    r = requests.get(f"{API}/getUpdates", params={"timeout": 0}, timeout=20).json()
    if not r.get("ok"):
        raise RuntimeError(f"getUpdates 실패: {r.get('description', '')}")

    updates = r.get("result", [])
    if not updates:
        print("처리할 메시지 없음")
        return

    for u in updates:
        process_update(u)

    # offset 확정 → 다음 실행에서 중복 처리 방지
    requests.get(f"{API}/getUpdates",
                 params={"offset": updates[-1]["update_id"] + 1,
                         "limit": 1, "timeout": 0}, timeout=20)
    print(f"{len(updates)}건 처리 완료")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            try:
                send(TELEGRAM_CHAT_ID, f"🔴 할인율 봇 실행 실패:\n<code>{err[-500:]}</code>")
            except Exception:
                pass
        sys.exit(1)
