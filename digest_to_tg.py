#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v3.2 (2025‑04‑21)

Новые возможности
──────────────────
• **Whitelist‑фильтр** — бот публикует новости **только из «официальных» доменов**,
  перечисленных в переменной окружения `ALLOWED_DOMAINS` (через запятую).  
  Пример: `ALLOWED_DOMAINS=microsoft.com,apple.com,1c.ru,gov.ru`.
• Collector и Critic получают инструкцию работать исключительно с этим списком.  
• Post‑filter на Python удаляет любые строки с URL вне белого списка (защита
  от промахов модели).  
• Если после всех фильтров новостей < `MIN_NEWS` — идём на повторную генерацию
  (до `max_iter`).
• Сохранились все предыдущие фиксы (temperature = 1, SQL‑patch и т.д.).
"""
import os
import re
import sqlite3
import datetime as dt
from hashlib import md5
from typing import List
from urllib.parse import urlparse

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
ALLOWED_DOMAINS  = [d.strip().lower() for d in os.getenv("ALLOWED_DOMAINS", "").split(",") if d.strip()]

client = OpenAI()

# ─────────────────────────────── PROMPTS ──╯

def domains_md_list() -> str:
    """Возвращает маркдаун‑список доменов для встраивания в prompt."""
    if not ALLOWED_DOMAINS:
        return "любой авторитетный сайт"
    return ", ".join(ALLOWED_DOMAINS)


def make_prompt(today: str) -> str:
    allowed = domains_md_list()
    return f"""
Ты — IT‑аналитик. Сформируй **черновик** IT‑дайджеста в формате Markdown ⬇️.
Используй **ТОЛЬКО статьи с доменов: {allowed}.**  
Условия:
• Статья моложе {MAX_AGE_HOURS} ч от {today}.  
• После каждой ссылки дата (ДД.ММ).  ≤ 30 слов на новость.  
• Секции: 🌍 ГЛОБАЛЬНЫЙ IT, 🇷🇺 РОССИЙСКИЙ TECH, 🟡 ЭКОСИСТЕМА 1С.  
• Минимум {MIN_NEWS} новостей суммарно.  
• Заверши Insight‑блоком (2‑3 предложения).
"""

if ALLOWED_DOMAINS:
    allowed_regex = r"|".join(re.escape(d) + r"$" for d in ALLOWED_DOMAINS)
else:
    allowed_regex = r".*"  # любой домен

CRITIC_SYSTEM = (
    "Ты — редактор. Получаешь черновик IT‑дайджеста.\n"
    f"— Удали строки, где ссылка не принадлежит доменам ({domains_md_list()}).\n"
    f"— Удали строки со статьями старше {MAX_AGE_HOURS} ч или без даты.\n"
    "— HEAD‑проверь ссылку: 4xx/5xx → удалить строку.\n"
    "— Верни исправленный дайджест в том же формате.\n"
    f"Если после чистки новостей < {MIN_NEWS} — ответь `RETRY`.\n"
)

# ─────────────────────────────── HELPERS ─╮
URL_DATE_RE = re.compile(r"\]\((https?://[^)]+)\)\s*\((\d{2})\.(\d{2})\)")


def hash_url(url: str) -> str:
    return md5(url.encode()).hexdigest()


def allowed_domain(url: str) -> bool:
    if not ALLOWED_DOMAINS:
        return True
    hostname = urlparse(url).hostname or ""
    hostname = hostname.lower()
    return any(hostname == d or hostname.endswith("." + d) for d in ALLOWED_DOMAINS)

# ─────────────────────────────── COLLECTOR ─╯

def call_collector() -> str:
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    resp = client.responses.create(
        model        = COLLECTOR_MODEL,
        tools        = [{"type": "web_search"}],
        input        = [{"role": "user", "content": make_prompt(today)}],
        temperature  = 1,
        store        = False,
    )
    return resp.output_text.strip()

# ─────────────────────────────── CRITIC ───╯

def critic_pass(draft: str) -> str:
    resp = client.chat.completions.create(
        model        = CRITIC_MODEL,
        temperature  = 1,
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM},
            {"role": "user", "content": draft},
        ],
    )
    return resp.choices[0].message.content.strip()

# ─────────────────────────────── PIPELINE ─╯

def post_filter(digest: str) -> str:
    """Удаляет строки с URL вне белого списка на случай промаха модели."""
    if not ALLOWED_DOMAINS:
        return digest
    good_lines = []
    for ln in digest.splitlines():
        m = URL_DATE_RE.search(ln)
        if not m:
            good_lines.append(ln)
            continue
        url = m.group(1)
        if allowed_domain(url):
            good_lines.append(ln)
    return "\n".join(good_lines).strip()


def produce_final_digest(max_iter: int = 4) -> str:
    for _ in range(max_iter):
        draft   = call_collector()
        cleaned = critic_pass(draft)
        if cleaned == "RETRY":
            continue
        filtered = post_filter(cleaned)
        if sum(1 for l in filtered.splitlines() if l.startswith("- **")) >= MIN_NEWS:
            return filtered
    raise RuntimeError("Не удалось собрать дайджест, удовлетворяющий ограничениям доменов")

# ───────────────────────────── TELEGRAM ─╮

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

    if hashes:
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
