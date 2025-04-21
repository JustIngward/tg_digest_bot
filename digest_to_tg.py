#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v9.0 (2025‑04‑22)

▪ GPT‑4o + встроенный web_search (whitelist) — модель сразу генерирует готовый
  Markdown‑дайджест с краткими выжимками и кликабельными ссылками.
▪ SQLite‑«память» отсекает новости, уже отправленные ранее (дубликаты ≤ 30 дней).
▪ Fallback‑лооп — до 3 попыток, если модель дала слишком мало уникальных ссылок.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
import datetime as dt
import textwrap
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from openai import OpenAI

# ───── CONFIG ─────
load_dotenv()
TZ                = dt.timezone(dt.timedelta(hours=3))  # Europe/Moscow
MODEL             = os.getenv("MODEL", "gpt-4o")
TEMPERATURE       = float(os.getenv("TEMPERATURE", 0.7))
MAX_AGE_DAYS      = int(os.getenv("MAX_AGE_DAYS", 7))
MIN_NEWS_LINES    = int(os.getenv("MIN_NEWS_LINES", 6))
MAX_RETRIES       = int(os.getenv("MAX_RETRIES", 3))
SQLITE_PATH       = os.getenv("DB_PATH", "sent_news.db")
TG_TOKEN          = os.environ["TG_TOKEN"]
CHAT_ID           = os.environ["CHAT_ID"]

# Домены‑white‑list
WHITELIST = {
    "cnews.ru", "tadviser.ru", "vc.ru", "rbc.ru", "gazeta.ru",
    "1c.ru", "infostart.ru", "odysseyconsgroup.com",
    "rusbase.ru", "trends.rbc.ru", "novostiitkanala.ru", "triafly.ru",
}

client = OpenAI()

# ───── DB helpers ─────
SCHEMA = """CREATE TABLE IF NOT EXISTS sent (
    fp  TEXT PRIMARY KEY,
    ts  DATETIME DEFAULT CURRENT_TIMESTAMP
);"""

def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute(SCHEMA)
    return conn

def url_fp(url: str) -> str:
    return str(hash(url))  # достаточно для дедупликации

def already_sent(url: str) -> bool:
    with db_conn() as c:
        return c.execute("SELECT 1 FROM sent WHERE fp=?", (url_fp(url),)).fetchone() is not None

def mark_sent(url: str):
    with db_conn() as c:
        c.execute("INSERT OR IGNORE INTO sent(fp) VALUES (?)", (url_fp(url),))

# ───── PROMPT ─────

def build_prompt() -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    rules = textwrap.dedent(f"""
        Ты — русский IT‑аналитик. Составь дайджест: краткие выжимки + ссылки.
        Условия:
        • Бери только статьи с доменов whitelist и возрастом ≤ {MAX_AGE_DAYS} дней.
        • Формат новости: "- **Заголовок** — 1–2 предложения. [Источник](URL) (DD.MM.YYYY)".
        • Строго три секции в порядке и пустой строкой между:
          🌍 **ГЛОБАЛЬНЫЙ IT**
          🇷🇺 **РОССИЙСКИЙ TECH**
          🟡 **ЭКОСИСТЕМА 1С**
        • Минимум {MIN_NEWS_LINES} новостей суммарно.
        • В конце добавь блок "💡 **Insight:**" — 2–3 предложения выводов.
        • Заголовок всего: "🗞️ **IT‑Digest • {today}**".
        • Пиши по‑русски, без UTM‑меток и эмодзи кроме заданных.
    """)
    return rules.strip()

# ───── GENERATE ─────
NEWS_RE = re.compile(r"^\s*[-*]\s+\*\*.+?\*\*.+\[.*?](https?://[^)\s]+)\s*\(\d{2}\.\d{2}\.\d{4}?\)")


def generate_digest() -> str:
    tools = [{"type": "web_search", "domains": list(WHITELIST), "top_k": 12}]
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": build_prompt()}],
        tools=tools,
        tool_choice="auto",
        temperature=TEMPERATURE,
        max_completion_tokens=900,
    )
    return response.choices[0].message.content.strip()

# ───── VALIDATE & DEDUPE ─────

def extract_urls(md: str) -> list[str]:
    urls = []
    for line in md.splitlines():
        m = NEWS_RE.match(line)
        if m:
            url = m.group(1)
            urls.append(url)
    return urls


def validate_digest(md: str) -> bool:
    lines = [l for l in md.splitlines() if NEWS_RE.match(l)]
    if len(lines) < MIN_NEWS_LINES:
        return False
    # все ссылки whitelist + не отправлялись ранее
    for url in extract_urls(md):
        host = urlparse(url).netloc
        if not any(host.endswith(d) for d in WHITELIST):
            return False
        if already_sent(url):
            return False
    return True

# ───── SEND TO TELEGRAM ─────

def send_telegram(text: str):
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for chunk in (text[i:i+3900] for i in range(0, len(text), 3900)):
        requests.post(api, json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }, timeout=15).raise_for_status()

# ───── MAIN LOOP ─────

def main():
    for attempt in range(1, MAX_RETRIES + 1):
        digest = generate_digest()
        if validate_digest(digest):
            # сохраняем ссылки в БД, чтобы не повторять в будущем
            for url in extract_urls(digest):
                mark_sent(url)
            send_telegram(digest)
            print("Digest sent ✔︎")
            return
        print(f"Attempt {attempt}: validation failed, retrying…")
        time.sleep(3)
    raise RuntimeError("Не удалось сгенерировать валидный дайджест за отведённые попытки")

if __name__ == "__main__":
    main()
