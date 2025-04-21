#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v4.1 (2025‑04‑21)

Bug‑fix & weekly edition
────────────────────────
* Окно свежести — 7 дней (168 ч). Дайджест выходной по понедельникам.
* Гибкий счётчик новостей: `^\s*[-*]\s*\*\*` ловит `- **`, `* **`, пробелы/таб.
* Та же маска используется в post‑filter, чтобы не «терять» строки.
* В лог пишем размер raw‑draft и кол‑во строк на каждом шаге.
* Остальной функционал (whitelist, Critic, дедуп, мягкий порог) без изменений.
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
MAX_AGE_HOURS    = int(os.getenv("MAX_AGE_HOURS", 168))  # 7 дней
MIN_NEWS         = int(os.getenv("MIN_NEWS", 6))
MIN_NEWS_SOFT    = int(os.getenv("MIN_NEWS_SOFT", 3))
MAX_ITER         = int(os.getenv("MAX_ITER", 4))
SQLITE_PATH      = "sent_hashes.db"
TG_TOKEN         = os.environ["TG_TOKEN"]
CHAT_ID          = os.environ["CHAT_ID"]
ALLOWED_DOMAINS  = [d.strip().lower() for d in os.getenv("ALLOWED_DOMAINS", "").split(",") if d.strip()]

NEWS_LINE_RE = re.compile(r"^\s*[-*]\s*\*\*")  # flexible bullet matcher
URL_DATE_RE  = re.compile(r"\]\((https?://[^)]+)\)\s*\((\d{2})\.(\d{2})\)")

client = OpenAI()

# ─────────────────────────────── PROMPTS ──╯

def domains_md_list() -> str:
    return ", ".join(ALLOWED_DOMAINS) if ALLOWED_DOMAINS else "любой авторитетный сайт"


def make_prompt(today: str) -> str:
    allowed = domains_md_list()
    days    = MAX_AGE_HOURS // 24
    return f"""
Ты — IT‑аналитик. Сформируй **черновик** еженедельного IT‑дайджеста (Markdown).
• Бери ТОЛЬКО материалы с доменов: {allowed}.  
• Каждый источник моложе {days} дней от {today}.  
• После ссылки ставь дату (ДД.ММ). ≤ 30 слов.  
• Секции: 🌍 ГЛОБАЛЬНЫЙ IT, 🇷🇺 РОССИЙСКИЙ TECH, 🟡 ЭКОСИСТЕМА 1С.  
• Минимум {MIN_NEWS} новостей суммарно.  
• Заверши Insight‑блоком (2‑3 предложения)."""

CRITIC_SYSTEM = (
    "Ты — редактор. Получаешь черновик IT‑дайджеста.\n"
    f"— Удали строки с доменами вне списка ({domains_md_list()}).\n"
    f"— Удали статьи старше {MAX_AGE_HOURS//24} дней или без даты.\n"
    "— HEAD‑проверь ссылку: 4xx/5xx → удалить.\n"
    "— Верни дайджест в том же формате.\n"
    f"Если после чистки новостей < {MIN_NEWS} — ответь `RETRY`.\n")

# ─────────────────────────────── HELPERS ─╮

def hash_url(url: str) -> str:
    return md5(url.encode()).hexdigest()


def allowed_domain(url: str) -> bool:
    if not ALLOWED_DOMAINS:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def count_news(text: str) -> int:
    return sum(1 for ln in text.splitlines() if NEWS_LINE_RE.match(ln))

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
    if not ALLOWED_DOMAINS:
        return digest
    keep = []
    for ln in digest.splitlines():
        m = URL_DATE_RE.search(ln)
        if not m or allowed_domain(m.group(1)):
            keep.append(ln)
    return "\n".join(keep).strip()


def produce_final_digest() -> str:
    for attempt in range(1, MAX_ITER + 1):
        draft    = call_collector()
        print(f"Attempt {attempt}: raw draft lines = {len(draft.splitlines())}")
        cleaned  = critic_pass(draft)
        if cleaned == "RETRY":
            print("Critic запросил повтор.")
            continue
        filtered = post_filter(cleaned)
        news_cnt = count_news(filtered)
        print(f"Attempt {attempt}: новостей после фильтра = {news_cnt}.")
        if news_cnt >= MIN_NEWS or (attempt == MAX_ITER and news_cnt >= MIN_NEWS_SOFT):
            return filtered
        print("Мало новостей, пробуем ещё раз…")
    raise RuntimeError("Не удалось собрать дайджест: слишком строгие ограничения.")

# ───────────────────────────── TELEGRAM ─╮

def send_to_telegram(text: str):
    url      = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    CHUNK_SZ = 3900
    for i in range(0, len(text), CHUNK_SZ):
        chunk = text[i:i+CHUNK_SZ]
        r = requests.post(url, json={
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
