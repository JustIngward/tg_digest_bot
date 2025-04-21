#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v9.1 (2025‑04‑22)

► Переход на **Assistants API + GPT‑4o + встроенный browser** — ChatCompletion
  пока не поддерживает `web_search`, поэтому мигрируем на ассистента.
► Логика та же: модель готовит Markdown‑дайджест, мы проверяем, отправляем.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
import datetime as dt
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from openai import OpenAI

# ───── CONFIG ─────
load_dotenv()
TZ                = dt.timezone(dt.timedelta(hours=3))
MODEL             = os.getenv("MODEL", "gpt-4o")
TEMPERATURE       = float(os.getenv("TEMPERATURE", 0.7))
MAX_AGE_DAYS      = int(os.getenv("MAX_AGE_DAYS", 7))
MIN_NEWS_LINES    = int(os.getenv("MIN_NEWS_LINES", 6))
MAX_RETRIES       = int(os.getenv("MAX_RETRIES", 3))
SQLITE_PATH       = os.getenv("DB_PATH", "sent_news.db")
TG_TOKEN          = os.environ["TG_TOKEN"]
CHAT_ID           = os.environ["CHAT_ID"]

WHITELIST = {
    "cnews.ru", "tadviser.ru", "vc.ru", "rbc.ru", "gazeta.ru",
    "1c.ru", "infostart.ru", "odysseyconsgroup.com",
    "rusbase.ru", "trends.rbc.ru", "novostiitkanala.ru", "triafly.ru",
}

client = OpenAI()

# ───── DB helpers ─────
SCHEMA = """CREATE TABLE IF NOT EXISTS sent (
    fp TEXT PRIMARY KEY,
    ts DATETIME DEFAULT CURRENT_TIMESTAMP
);"""

def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute(SCHEMA)
    return conn

def url_fp(url: str) -> str:
    return str(hash(url))

def already_sent(url: str) -> bool:
    with db_conn() as c:
        return c.execute("SELECT 1 FROM sent WHERE fp=?", (url_fp(url),)).fetchone() is not None

def mark_sent(url: str):
    with db_conn() as c:
        c.execute("INSERT OR IGNORE INTO sent(fp) VALUES (?)", (url_fp(url),))

# ───── PROMPT ─────

def build_prompt() -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    return f"""
Ты — русский IT‑аналитик. Собери дайджест в Markdown.
• Статьи только с доменов white‑list и возрастом ≤ {MAX_AGE_DAYS} дней.
• Формат: "- **Заголовок** — 1–2 предложения. [Источник](URL) (DD.MM.YYYY)".
• Три секции в порядке: 🌍 **ГЛОБАЛЬНЫЙ IT**; 🇷🇺 **РОССИЙСКИЙ TECH**; 🟡 **ЭКОСИСТЕМА 1С**.
• Минимум {MIN_NEWS_LINES} новостей суммарно.
• Заверши блоком "💡 **Insight:**" — 2–3 предложения выводов.
• Заголовок всего: "🗞️ **IT‑Digest • {today}**".
• Без UTM‑меток и лишних эмодзи.
""".strip()

# ───── ASSISTANT SETUP (cached) ─────

def get_assistant_id() -> str:
    cache = ".assistant_id"
    if os.path.exists(cache):
        return open(cache).read().strip()
    assistant = client.beta.assistants.create(
        name="IT Digest Bot",
        model=MODEL,
        tools=[{"type": "browser"}],
        temperature=TEMPERATURE,
    )
    with open(cache, "w") as f:
        f.write(assistant.id)
    return assistant.id

ASSISTANT_ID = get_assistant_id()

# ───── GENERATE DIGEST VIA THREAD/RUN ─────
NEWS_RE = re.compile(r"^\s*[-*]\s+\*\*.+?\*\*.+\[.*?](https?://[^)\s]+)\s*\(\d{2}\.\d{2}\.\d{4}?\)")


def assistant_digest() -> str:
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=build_prompt(),
    )
    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=ASSISTANT_ID,
    )
    # polling until completed (simplest)
    while run.status in {"queued", "in_progress"}:
        time.sleep(5)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
    if run.status != "completed":
        raise RuntimeError(f"Assistant run failed: {run.status}")
    msgs = client.beta.threads.messages.list(thread_id=thread.id)
    return msgs.data[0].content[0].text.value.strip()

# ───── VALIDATE ─────

def extract_urls(md: str):
    for line in md.splitlines():
        m = NEWS_RE.match(line)
        if m:
            yield m.group(1)

def validate(md: str) -> bool:
    lines = [l for l in md.splitlines() if NEWS_RE.match(l)]
    if len(lines) < MIN_NEWS_LINES:
        return False
    for url in extract_urls(md):
        host = urlparse(url).netloc
        if not any(host.endswith(d) for d in WHITELIST):
            return False
        if already_sent(url):
            return False
    return True

# ───── TELEGRAM ─────

def send_tg(text: str):
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for chunk in (text[i:i+3900] for i in range(0, len(text), 3900)):
        requests.post(api, json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }, timeout=20).raise_for_status()

# ───── MAIN ─────

def main():
    for attempt in range(1, MAX_RETRIES + 1):
        md = assistant_digest()
        if validate(md):
            for url in extract_urls(md):
                mark_sent(url)
            send_tg(md)
            print("Digest sent ✔︎")
            return
        print(f"Attempt {attempt}: digest invalid, retry…")
    raise RuntimeError("Не удалось собрать валидный дайджест")

if __name__ == "__main__":
    main()
