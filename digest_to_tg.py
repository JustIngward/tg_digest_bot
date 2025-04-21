#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v4.4 (2025‑04‑21)

Patch v4.4 — Regex «помягче», больше попыток
────────────────────────────────────────────
1. **NEWS_LINE_RE** ослаблен:
   • разрешает однозначный день/месяц `(7.4)` или с нулями `(07.04)`;
   • допускает URL c параметрами/скобками; главное — закрывающая `)`.
2. **MAX_ITER** повышен до 6 (можно переопределить Secret‑ом).
3. post_filter использует новый regex и даёт debug‑print «отфильтровано N».
"""
import os, re, sqlite3, datetime as dt
from hashlib import md5
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
TZ = dt.timezone(dt.timedelta(hours=3))
COLLECTOR_MODEL = os.getenv("COLLECTOR_MODEL", "o3")
CRITIC_MODEL    = os.getenv("CRITIC_MODEL", COLLECTOR_MODEL)
MAX_AGE_HOURS   = int(os.getenv("MAX_AGE_HOURS", 168))
MIN_NEWS        = int(os.getenv("MIN_NEWS", 6))
MIN_NEWS_SOFT   = int(os.getenv("MIN_NEWS_SOFT", 3))
MAX_ITER        = int(os.getenv("MAX_ITER", 6))  # повышено
TG_TOKEN        = os.environ["TG_TOKEN"]
CHAT_ID         = os.environ["CHAT_ID"]
ALLOWED_DOMAINS = [d.strip().lower() for d in os.getenv("ALLOWED_DOMAINS", "").split(",") if d.strip()]

NEWS_LINE_RE = re.compile(r"^\s*[-*]\s*\*\*.*?\]\(https?://[^)\s]+\)\s*\((\d{1,2})\.(\d{1,2})\)")
URL_M_RE     = re.compile(r"\((https?://[^)\s]+)\)")

client = OpenAI()

# helpers

def allowed_domain(url: str) -> bool:
    if not ALLOWED_DOMAINS:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)

def count_news(text: str) -> int:
    return sum(1 for ln in text.splitlines() if NEWS_LINE_RE.match(ln))

def domains_md() -> str:
    return ", ".join(ALLOWED_DOMAINS) if ALLOWED_DOMAINS else "любой авторитетный сайт"

# prompts

days = MAX_AGE_HOURS // 24
PROMPT_TEMPLATE = (
    "Ты — IT‑аналитик. Сформируй **черновик** еженедельного IT‑дайджеста (Markdown).\n"
    f"• Бери ТОЛЬКО материалы с доменов: {domains_md()}.\n"
    f"• Источник моложе {days} дней.\n"
    "• Каждая новость ОБЯЗАТЕЛЬНО: `- **Заголовок** — текст. [Источник](URL) (DD.MM)`. ≤30 слов.\n"
    "• Секции: 🌍 ГЛОБАЛЬНЫЙ IT, 🇷🇺 РОССИЙСКИЙ TECH, 🟡 ЭКОСИСТЕМА 1С.\n"
    f"• Минимум {MIN_NEWS} новостей. Insight в конце."
)
CRITIC_SYSTEM = (
    "Проверь: ссылка+дата обязательны; домен в whitelist; дата ≤ {days} дней; HEAD 4xx/5xx убрать. Если после правки < {MN} → `RETRY`."
).format(days=days, MN=MIN_NEWS)

# core funcs

def call_collector():
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    resp = client.responses.create(
        model=COLLECTOR_MODEL,
        tools=[{"type": "web_search"}],
        input=[{"role": "user", "content": PROMPT_TEMPLATE.format(today=today)}],
        temperature=1,
        store=False,
    )
    return resp.output_text.strip()

def critic_pass(draft: str):
    return client.chat.completions.create(
        model=CRITIC_MODEL,
        temperature=1,
        messages=[{"role":"system","content":CRITIC_SYSTEM},{"role":"user","content":draft}],
    ).choices[0].message.content.strip()

def post_filter(txt: str) -> str:
    keep=[]
    for ln in txt.splitlines():
        if not NEWS_LINE_RE.match(ln):
            continue
        m=URL_M_RE.search(ln)
        if m and allowed_domain(m.group(1)):
            keep.append(ln)
    print(f"after filter: {len(keep)} lines")
    return "\n".join(keep)

def produce_digest():
    for i in range(MAX_ITER):
        draft=call_collector()
        clean=critic_pass(draft)
        if clean=="RETRY":
            continue
        filtered=post_filter(clean)
        if count_news(filtered)>=MIN_NEWS or (i==MAX_ITER-1 and count_news(filtered)>=MIN_NEWS_SOFT):
            return filtered
    raise RuntimeError("не удалось собрать дайджест — мало строк с валидной ссылкой")

# send

def send(text:str):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0,len(text),3900):
        r=requests.post(url,json={"chat_id":CHAT_ID,"text":text[i:i+3900],"parse_mode":"Markdown","disable_web_page_preview":False})
        if r.status_code!=200:
            raise RuntimeError(r.text)

if __name__=="__main__":
    sqlite3.connect("sent_hashes.db").execute("CREATE TABLE IF NOT EXISTS sent(hash TEXT PRIMARY KEY)")
    send(produce_digest())
