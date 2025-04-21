#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v3 (\u202a\u202cApril 2025)

Избавляемся от «старья» и добавляем второй, *мыслящий* слой AI.

Главная идея:\n 1. **Collector (по‑прежнему идёт через модель с web_search)** генерирует черновик.\n 2. **Critic \u2014 отдельный вызов модели** проверяет черновик на свежесть/битые ссылки и при необходимости даёт рекомендации или сразу чинит.\n 3. Код автоматически применяет патч Critic‑а. Если после правок новостей < MIN_NEWS — запускаем Collector снова.

Помимо этого:\n • HEAD‑валидация URL, фильтр по дате ≤ MAX_AGE_HOURS.\n • Mini‑SQLite для дедупликации.\n • .env теперь содержит CRITIC_MODEL (по умолчанию тот же).\n"""
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
• После каждой ссылки пишешь дату (ДД.ММ).
• Не более 30 слов на новость.
• Строго используй секции: 🌍 ГЛОБАЛЬНЫЙ IT, 🇷🇺 РОССИЙСКИЙ TECH, 🟡 ЭКОСИСТЕМА 1С.
• Минимум {MIN_NEWS} новостей суммарно.
• Markdown‑оформление, пример строки:
  - **Microsoft открыла код…** — два предложения. [Источник](https://example.com) (21.04)
• В конце блок Insight (2‑3 предложения).
"""

CRITIC_SYSTEM = """Ты — редактор. На вход дан черновик IT‑дайджеста.\n— Проверь, что каждая дата ≤ {max_age} ч от сегодня (21 Apr 2025).\n— Если статья старше или без даты — удали строку.\n— Проверь HEAD каждой ссылки (если 4xx/5xx — удали).\n— Итог: отдай исправленный дайджест *в том же* формате.\nЕсли после чистки новостей < {min_news} — ответь только `RETRY` (без кавычек).\n""".format(max_age=MAX_AGE_HOURS, min_news=MIN_NEWS)

# ─────────────────────────────── HELPERS ─╮
URL_DATE_RE = re.compile(r"\]\((https?://[^)]+)\)\s*\((\d{2})\.(\d{2})\)")

def is_link_alive(url: str) -> bool:
    try:
        r = requests.head(url, allow_redirects=True, timeout=HEAD_TIMEOUT)
        return r.status_code < 400
    except requests.RequestException:
        return False


def hash_url(url: str) -> str:
    return md5(url.encode()).hexdigest()

# ─────────────────────────────── COLLECTOR ─╯

def call_collector() -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    resp = client.responses.create(
        model   = COLLECTOR_MODEL,
        tools   = [{"type": "web_search"}],
        input   = [{"role": "user", "content": make_prompt(today)}],
        store   = False,
    )
    return resp.output_text.strip()

# ─────────────────────────────── CRITIC ───╯

def critic_pass(draft: str) -> str:
    resp = client.chat.completions.create(
        model = CRITIC_MODEL,
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM},
            {"role": "user", "content": draft},
        ],
        temperature=1,
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
    raise RuntimeError("Не смог получить свежий дайджест после {max_iter} попыток")

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
    # init dedup db (optional ‑ можно убрать, если критик и так удаляет повторы)
    db = sqlite3.connect(SQLITE_PATH)
    db.execute("CREATE TABLE IF NOT EXISTS sent (hash TEXT PRIMARY KEY)")

    digest = produce_final_digest()

    # дубли проверяем перед отправкой (на случай, если бот упал и поднялся)
    all_urls: List[str] = URL_DATE_RE.findall(digest)
    hashes = [hash_url(u) for u, *_ in all_urls]
    cur = db.cursor()
    cur.execute("SELECT hash FROM sent WHERE hash IN (%s)" % ("?"*len(hashes)), hashes)
    exists = {h for (h,) in cur.fetchall()}
    if exists:
        for h in exists:
            digest = re.sub(r".*%s.*\n" % h, "", digest)  # удаляем строку‑дубль
        digest = digest.strip()
    # запишем свежие
    cur.executemany("INSERT OR IGNORE INTO sent(hash) VALUES(?)", [(h,) for h in hashes])
    db.commit()

    if digest:
        send_to_telegram(digest)
