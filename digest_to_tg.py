#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v3.1  (2025‑04‑21)

🔧  Баг‑фиксы и пожелания пользователя
• Исправлена ошибка SQL «near ? : syntax error» — теперь плейсхолдеры генерируются `",".join("?"…)`.
• Temperature выставлена **равной 1** для Collector и Critic.
• Защита на случай `hashes==0` (пропускаем запрос IN ()).
"""
import os
import re
import sqlite3
import datetime as dt
from hashlib import md5
from typing import List

import requests
from dotenv import load_dotenv
from openai import OpenAI

# ────────────────────────────────── CONFIG ──╮
load_dotenv()
TZ               = dt.timezone(dt.timedelta(hours=3))  # Europe/Moscow
COLLECTOR_MODEL  = os.getenv("COLLECTOR_MODEL", "o3")
CRITIC_MODEL     = os.getenv("CRITIC_MODEL", COLLECTOR_MODEL)
MAX_AGE_HOURS    = int(os.getenv("MAX_AGE_HOURS", 48))
MIN_NEWS         = int(os.getenv("MIN_NEWS", 4))
HEAD_TIMEOUT     = 6
SQLITE_PATH      = "sent_hashes.db"
TG_TOKEN         = os.environ["TG_TOKEN"]
CHAT_ID          = os.environ["CHAT_ID"]

client = OpenAI()

# ─────────────────────────────── PROMPTS ──╯

def make_prompt(today: str) -> str:
    return f"""
Ты — IT‑аналитик. Сформируй **черновик** IT‑дайджеста в формате Markdown ⬇️.
Условия:
• Бери ТОЛЬКО статьи < {MAX_AGE_HOURS} ч от {today}. (Проверь дату у источника!)
• После каждой ссылки укажи дату (ДД.ММ).
• ≤ 30 слов на новость.
• Обязательные секции: 🌍 ГЛОБАЛЬНЫЙ IT, 🇷🇺 РОССИЙСКИЙ TECH, 🟡 ЭКОСИСТЕМА 1С.
• Минимум {MIN_NEWS} новостей суммарно.
• Пример строки:  - **Microsoft …** — два предложения. [Источник](https://ex.com) (21.04)
• Заверши блоком Insight (2‑3 предложения).
"""

CRITIC_SYSTEM = ("Ты — редактор. Получаешь черновик IT‑дайджеста.\n"
                 f"— Удали строки с новостями старше {MAX_AGE_HOURS} ч или без даты.\n"
                 "— HEAD‑проверь ссылку (4xx/5xx → удалить строку).\n"
                 "— Верни исправленный дайджест в том же формате.\n"
                 f"Если после чистки новостей < {MIN_NEWS} — ответь исключительно `RETRY`.\n")

# ─────────────────────────────── HELPERS ─╮
URL_DATE_RE = re.compile(r"\]\((https?://[^)]+)\)\s*\((\d{2})\.(\d{2})\)")

def hash_url(url: str) -> str:
    return md5(url.encode()).hexdigest()

# ─────────────────────────────── COLLECTOR ─╯

def call_collector() -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    resp = client.responses.create(
        model        = COLLECTOR_MODEL,
        tools        = [{"type": "web_search"}],
        input        = [{"role": "user", "content": make_prompt(today)}],
        temperature  = 1,   # ← по просьбе
        store        = False,
    )
    return resp.output_text.strip()

# ─────────────────────────────── CRITIC ───╯

def critic_pass(draft: str) -> str:
    resp = client.chat.completions.create(
        model        = CRITIC_MODEL,
        temperature  = 1,   # ← по просьбе
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM},
            {"role": "user", "content": draft},
        ],
    )
    return resp.choices[0].message.content.strip()

# ─────────────────────────────── PIPELINE ─╯

def produce_final_digest(max_iter: int = 4) -> str:
    for _ in range(max_iter):
        draft = call_collector()
        cleaned = critic_pass(draft)
        if cleaned == "RETRY":
            continue
        return cleaned
    raise RuntimeError("Не удалось получить свежий дайджест после нескольких попыток")

# ─────────────────────────────── TELEGRAM ─╮

def send_to_telegram(text: str):
    URL_API  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    CHUNK_SZ = 3900
    for i in range(0, len(text), CHUNK_SZ):
        chunk = text[i : i + CHUNK_SZ]
        r = requests.post(URL_API, json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        })
        if r.status_code != 200:
            raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")

# ─────────────────────────────── MAIN ──────╯
if __name__ == "__main__":
    db = sqlite3.connect(SQLITE_PATH)
    db.execute("CREATE TABLE IF NOT EXISTS sent (hash TEXT PRIMARY KEY)")

    digest = produce_final_digest()

    all_urls: List[str] = URL_DATE_RE.findall(digest)
    hashes = [hash_url(u) for u, *_ in all_urls]
    cur = db.cursor()

    if hashes:  # только если есть что проверять
        placeholders = ",".join("?" for _ in hashes)
        cur.execute(f"SELECT hash FROM sent WHERE hash IN ({placeholders})", hashes)
        exists = {h for (h,) in cur.fetchall()}
        if exists:
            for h in exists:
                digest = re.sub(r".*%s.*\n" % h, "", digest)
            digest = digest.strip()
        cur.executemany("INSERT OR IGNORE INTO sent(hash) VALUES(?)", [(h,) for h in hashes])
        db.commit()

    if digest:
        send_to_telegram(digest)
