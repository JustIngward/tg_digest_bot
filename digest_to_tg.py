#!/usr/bin/env python3
#IT‑Digest Telegram bot — v2

#Изменения:
#• Фильтр по дате (<= 48 ч) и HEAD‑валидация URL → старьё и битые ссылки не пройдут.
#• Автоповтор генерации, если новостей слишком мало.
#• Простой счётчик уже отправленных — чтобы не дублировать при перезапуске.

import os
import re
import sqlite3
import datetime
import requests
from hashlib import md5
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ──────────────────────────────────────────── CONSTANTS ─╮
TZ               = datetime.timezone(datetime.timedelta(hours=3))  # Moscow/UTC+3
MODEL            = os.getenv("OPENAI_MODEL", "o3")
MAX_AGE_HOURS    = 48                       # свежесть статей
HEAD_TIMEOUT     = 6                        # сек
MIN_NEWS         = 4                        # минимум годных статей → иначе реген
SQLITE_PATH      = "sent_hashes.db"         # чтобы не дублировали ссылки
TG_TOKEN         = os.environ["TG_TOKEN"]
CHAT_ID          = os.environ["CHAT_ID"]

client = OpenAI()

# ──────────────────────────────────────────── PROMPT ─────╯

def make_prompt() -> str:
    """Промпт‑шаблон для OpenAI; жёстко требует свежих ссылок."""
    today = datetime.datetime.now(TZ).strftime("%d %b %Y")
    return f"""
Ты — IT‑аналитик. Сформируй дайджест в строгом формате Markdown ниже.
⚠️ Используй ТОЛЬКО источники, опубликованные не старше 48 часов от {today}.
⚠️ Укажи дату публикации (ДД.ММ) после ссылки.

Формат:
🗞️ **IT‑Digest • {today}**

🌍 **ГЛОБАЛЬНЫЙ IT**
- **Жирный заголовок** — 1‑2 предложения сути. [Источник](URL) (DD.MM)

🇷🇺 **РОССИЙСКИЙ TECH**
- **Жирный заголовок** — 1‑2 предложения. [Источник](URL) (DD.MM)

🟡 **ЭКОСИСТЕМА 1С**
- **Жирный заголовок** — 1‑2 предложения. [Источник](URL) (DD.MM)

💡 **Insight:** 2‑3 предложения, почему события важны PM‑ам.

Правила: каждая новость с новой строки; секции разделяй пустой строкой; не больше 30 слов на новость.
"""

# ──────────────────────────────────────────── VALIDATION ─╮

def is_fresh(day: int, month: int) -> bool:
    today   = datetime.datetime.now(TZ)
    pubdate = datetime.datetime(today.year, month, day, tzinfo=TZ)
    return (today - pubdate).total_seconds() <= MAX_AGE_HOURS * 3600


def is_alive(url: str) -> str | None:
    """Возвращает конечный URL, если HEAD <400; иначе None."""
    try:
        r = requests.head(url, allow_redirects=True, timeout=HEAD_TIMEOUT)
        if r.status_code < 400:
            return r.url  # финальный после редиректов
    except requests.RequestException:
        pass
    return None


URL_DATE_RE = re.compile(r"\]\((https?://[^\)]+)\)\s*\((\d{2})\.(\d{2})\)")


def validate_digest(text: str) -> str:
    """Фильтрует старые/битые ссылки, удаляет дубликаты."""
    lines, good = text.splitlines(), []
    seen_hashes = {h for (h,) in DB.execute("SELECT hash FROM sent").fetchall()}

    for ln in lines:
        m = URL_DATE_RE.search(ln)
        if not m:              # строка без ссылки → пасс‑сквозь
            good.append(ln)
            continue
        url, day, month = m.group(1), int(m.group(2)), int(m.group(3))
        if not is_fresh(day, month):
            continue
        final = is_alive(url)
        if not final:
            continue
        h = md5(final.encode()).hexdigest()
        if h in seen_hashes:
            continue           # уже отправляли
        ln = ln.replace(url, final)
        good.append(ln)
        DB.execute("INSERT OR IGNORE INTO sent VALUES (?, ?)", (h, int(datetime.datetime.now().timestamp())))
        seen_hashes.add(h)
    DB.commit()
    return "\n".join(good)


def count_news(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.startswith("- **"))

# ──────────────────────────────────────────── OPENAI LOOP ─╯

def fetch_valid_digest(max_attempts: int = 3) -> str:
    prompt = make_prompt()
    for attempt in range(1, max_attempts + 1):
        resp = client.responses.create(
            model   = MODEL,
            tools   = [{"type": "web_search"}],
            input   = [{"role": "user", "content": prompt}],
            store   = False
        )
        raw = resp.output_text.strip()
        fixed = validate_digest(raw)
        if count_news(fixed) >= MIN_NEWS:
            return fixed
        print(f"⚠️ Попытка {attempt}: мало годных статей, пробуем ещё раз…")
    return fixed  # отдаём последнее, даже если мало

# ──────────────────────────────────────────── TELEGRAM ────╮

def send_to_telegram(text: str):
    url       = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    CHUNK_sz  = 3900  # чуть меньше лимита 4096
    for i in range(0, len(text), CHUNK_sz):
        chunk = text[i:i + CHUNK_sz]
        resp  = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        })
        if resp.status_code != 200:
            print("Telegram error:", resp.text)

# ──────────────────────────────────────────── MAIN ────────╯
if __name__ == "__main__":
    # init tiny DB for deduplication
    DB = sqlite3.connect(SQLITE_PATH)
    DB.execute("CREATE TABLE IF NOT EXISTS sent (hash TEXT PRIMARY KEY, ts INTEGER)")

    digest = fetch_valid_digest()
    send_to_telegram(digest)
