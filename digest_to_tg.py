#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v4.3 (2025‑04‑21)

Patch v4.3 — «ссылка обязательна»
────────────────────────────────
Проблема: модель иногда оставляет пункты без `[Источник](URL)` → читатель не
может кликнуть. Решение:
1. **NEWS_LINE_RE** теперь требует сразу и ссылку, и дату `(DD.MM)`.
2. **post_filter** строг: оставляет строку, только если совпадает с новым
   NEWS_LINE_RE *и* домен в whitelist.
3. Critic‑prompt дополнен требованием «Каждая новость ОБЯЗАТЕЛЬНО содержит
   ссылку формата `[Источник](URL)` и дату (ДД.ММ)».
"""
import os, re, sqlite3, datetime as dt
from hashlib import md5
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv
from openai import OpenAI

# ───────── CONFIG ────────
load_dotenv()
TZ = dt.timezone(dt.timedelta(hours=3))
COLLECTOR_MODEL = os.getenv("COLLECTOR_MODEL", "o3")
CRITIC_MODEL    = os.getenv("CRITIC_MODEL", COLLECTOR_MODEL)
MAX_AGE_HOURS   = int(os.getenv("MAX_AGE_HOURS", 168))
MIN_NEWS        = int(os.getenv("MIN_NEWS", 6))
MIN_NEWS_SOFT   = int(os.getenv("MIN_NEWS_SOFT", 3))
MAX_ITER        = int(os.getenv("MAX_ITER", 4))
SQLITE_PATH     = "sent_hashes.db"
TG_TOKEN        = os.environ["TG_TOKEN"]
CHAT_ID         = os.environ["CHAT_ID"]
ALLOWED_DOMAINS = [d.strip().lower() for d in os.getenv("ALLOWED_DOMAINS", "").split(",") if d.strip()]

# каждая новость: маркер, жирный заголовок, ссылка и дата
NEWS_LINE_RE = re.compile(r"^\s*[-*]\s*\*\*.*?\]\(https?://[^)\s]+\)\s*\(\d{2}\.\d{2}\)")
URL_M_RE  = re.compile(r"\((https?://[^)\s]+)\)")

client = OpenAI()

# ───────── HELPERS ───────

def allowed_domain(url: str) -> bool:
    if not ALLOWED_DOMAINS:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def count_news(text: str) -> int:
    return sum(1 for ln in text.splitlines() if NEWS_LINE_RE.match(ln))

# ───────── PROMPTS ───────

def domains_md() -> str:
    return ", ".join(ALLOWED_DOMAINS) if ALLOWED_DOMAINS else "любой авторитетный сайт"

days = MAX_AGE_HOURS // 24

PROMPT_TEMPLATE = (
    "Ты — IT‑аналитик. Сформируй **черновик** еженедельного IT‑дайджеста (Markdown).\n"
    f"• Бери ТОЛЬКО материалы с доменов: {domains_md()}.\n"
    f"• Источник моложе {days} дней.\n"
    "• Каждая новость ОБЯЗАТЕЛЬНО: формат `- **Заголовок** — текст. [Источник](URL) (DD.MM)`. ≤30 слов.\n"
    "• Секции: 🌍 ГЛОБАЛЬНЫЙ IT, 🇷🇺 РОССИЙСКИЙ TECH, 🟡 ЭКОСИСТЕМА 1С.\n"
    f"• Минимум {MIN_NEWS} новостей. Заверши Insight‑блоком (2‑3 предложения)."
)

CRITIC_SYSTEM = (
    "Ты — редактор. Проверь черновик: ссылка + дата обязательны; домен в whitelist; дата ≤ {days} дней; HEAD 4xx/5xx удалить. Если после правки < {MIN_NEWS} → `RETRY`."
).format(days=days, MIN_NEWS=MIN_NEWS)

# ───────── CORE ───────

def call_collector():
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    return client.responses.create(
        model=COLLECTOR_MODEL,
        tools=[{"type": "web_search"}],
        input=[{"role": "user", "content": PROMPT_TEMPLATE.format(today=today)}],
        temperature=1,
        store=False,
    ).output_text.strip()


def critic_pass(draft: str):
    return client.chat.completions.create(
        model=CRITIC_MODEL,
        temperature=1,
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM},
            {"role": "user", "content": draft},
        ],
    ).choices[0].message.content.strip()


def post_filter(text: str) -> str:
    good = []
    for ln in text.splitlines():
        if not NEWS_LINE_RE.match(ln):
            continue
        url = URL_M_RE.search(ln)
        if not url or not allowed_domain(url.group(1)):
            continue
        good.append(ln)
    return "\n".join(good).strip()


def produce_digest():
    for _ in range(MAX_ITER):
        draft = call_collector()
        cleaned = critic_pass(draft)
        if cleaned == "RETRY":
            continue
        filtered = post_filter(cleaned)
        if count_news(filtered) >= MIN_NEWS:
            return filtered
    raise RuntimeError("не удалось собрать дайджест — мало строк с валидной ссылкой")

# ───────── SEND ───────

def send(text: str):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0,len(text),3900):
        r=requests.post(url,json={"chat_id":CHAT_ID,"text":text[i:i+3900],"parse_mode":"Markdown","disable_web_page_preview":False})
        if r.status_code!=200:
            raise RuntimeError(r.text)

# ───────── MAIN ───────
if __name__=="__main__":
    sqlite3.connect(SQLITE_PATH).execute("CREATE TABLE IF NOT EXISTS sent(hash TEXT PRIMARY KEY)")
    send(produce_digest())
