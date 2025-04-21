#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v16.1 (2025‑04‑22)
RSS‑only, async‑HTML, GPT‑ранж, приоритет 1С (≥40 %).
"""
from __future__ import annotations

import asyncio, datetime as dt, html as _html, json, os, re, textwrap
from urllib.parse import urlparse

import feedparser, httpx, requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from openai import OpenAI

# ───── ENV / CONFIG ─────
load_dotenv()
TG_TOKEN   = os.getenv("TG_TOKEN")
CHAT_ID    = os.getenv("CHAT_ID")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
assert TG_TOKEN and CHAT_ID and OPENAI_KEY, "TG_TOKEN, CHAT_ID, OPENAI_API_KEY required"

MODEL        = os.getenv("MODEL", "gpt-4o")
TZ           = dt.timezone(dt.timedelta(hours=3))
MAX_DAYS     = 7               # сколько дней назад берём
MAX_PER_FEED = 50              # максимум элементов из каждой ленты
MAX_HTML     = 250             # максимум HTML‑скачиваний
DIGEST_MIN   = 8
DIGEST_MAX   = 12
PERC_ONEC    = 0.4             # доля 1С‑новостей в дайджесте

client = OpenAI()
CUTOFF = dt.datetime.utcnow() - dt.timedelta(days=MAX_DAYS)

# ───── Ключевые слова ─────
ONEC_DOMAINS = {"1c.ru", "infostart.ru", "odysseyconsgroup.com"}
ONEC_KEYS = {
    "1с", "1c", "1‑с", "1-с", "1с:erp", "erp", "зуп", "унф",
    "управление торговлей", "бухгалтерия", "wms",
}
INCLUDE = set(ONEC_KEYS) | {
    "ai", "devops", "облач", "цифров", "интеграц", "миграц",
    "kubernetes", "crm", "автоматиза",
}
EXCLUDE = {
    "crypto", "iphone", "lifestyle", "шоколад", "фисташк", "авто",
    "биржа", "банкомат", "здоровье",
}

# ───── RSS‑ленты ─────
RSS_FEEDS = [
    "https://habr.com/ru/rss/all/all/?fl=ru",
    "https://vc.ru/rss",
    "https://www.rbc.ru/technology/rss/full/",
    "https://tadviser.ru/index.php/Статья:Новости?feed=rss",
    "https://novostiitkanala.ru/feed/",
    "https://www.kommersant.ru/RSS/section-tech.xml",
    "https://1c.ru/news/all.rss",
    "https://infostart.ru/rss/news/",
    "https://trends.rbc.ru/trends.rss",
    "https://rusbase.com/feed/",
]

# ───── Helper functions ─────

def plain(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ").lower()

async def fetch_html(url: str, client: httpx.AsyncClient) -> str | None:
    try:
        r = await client.get(url, timeout=6)
        return r.text if r.status_code == 200 else None
    except Exception:
        return None

# ───── Stage 0: Collect raw (50 × feeds) ─────

def collect_raw():
    pool_onec, pool_other = [], []
    for feed in RSS_FEEDS:
        try:
            fp = feedparser.parse(feed)
        except Exception:
            continue
        for entry in fp.entries[:MAX_PER_FEED]:
            link = entry.get("link", "")
            title = entry.get("title", "")
            date_str = (entry.get("published") or entry.get("updated") or entry.get("dc_date") or "")[:10]
            try:
                d = dt.datetime.strptime(date_str, "%Y-%m-%d")
            except Exception:
                d = dt.datetime.utcnow()
            if d < CUTOFF:
                continue
            rec = {
                "title": title,
                "url": link,
                "date": d.strftime("%d.%m.%Y"),
                "t": title.lower(),
            }
            target = pool_onec if (urlparse(link).netloc in ONEC_DOMAINS or any(k in rec["t"] for k in ONEC_KEYS)) else pool_other
            target.append(rec)
    return pool_onec, pool_other

# ───── Stage 1: Title filter ─────

def title_filter(lst):
    ok = []
    for a in lst:
        t = a["t"]
        if any(w in t for w in EXCLUDE):
            continue
        if any(k in t for k in INCLUDE):
            ok.append(a)
    return ok

# ───── Stage 2: Async HTML filter ─────

async def body_filter(cand: list[dict]):
    subset = cand[:MAX_HTML]
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as cl:
        pages = await asyncio.gather(*[fetch_html(a["url"], cl) for a in subset])
    out = []
    for art, html in zip(subset, pages):
        if html and any(k in plain(html) for k in INCLUDE):
            out.append(art)
    return out

# ───── Stage 3: GPT ranking ─────

def gpt_rank(pool: list[dict]):
    prompt = (
        "Оцени по шкале 0‑10 важность новости для интегратора 1С. "
        "Ответ JSON вида {\"idx\":score}. Не добавляй ничего."
    )
    mini = [{"idx": i, "title": a["title"], "url": a["url"]} for i, a in enumerate(pool)]
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt + json.dumps(mini, ensure_ascii=False)}],
        temperature=0,
        max_tokens=300,
    )
    try:
        scores = json.loads(resp.choices[0].message.content)
    except Exception:
        scores = {str(i): 5 for i in range(len(pool))}
    ranked = sorted(pool, key=lambda x: -scores.get(str(pool.index(x)), 0))
    return ranked[: DIGEST_MAX * 2]

# ───── Layout final list ─────

def layout(arts):
    onec = [a for a in arts if (urlparse(a["url"]).netloc in ONEC_DOMAINS or any(k in a["t"] for k in ONEC_KEYS))]
    other = [a for a in arts if a not in onec]
    need_onec = max(3, int(DIGEST_MAX * PERC_ONEC))
    final = (onec[:need_onec] + other)[:DIGEST_MAX]
    return final

# ───── Prompt for final digest ─────

def build_prompt(arts):
    today = dt.datetime.now(TZ).strftime("%d %b %Y")
    return textwrap.dedent(
        f"""
        Ты — редактор B2B‑дайджеста для интеграторов 1С. Используй ТОЛЬКО данные JSON, ничего не выдумывай.
        Требования: 8‑12 строк; секции 🌍/🇷🇺/🟡; ≥40 % строк про 1С; формат "- <b>Заголовок</b> — … (DD.MM.YYYY)".
        В конце блок "💡 <b>Insight</b>:" — 2 предложения.
        JSON: ```{json.dumps(arts, ensure_ascii=False)}```
        """
    ).strip()

# ───── Sanitize + Telegram send ─────

def sanitize(html_txt: str) -> str:
    html_txt = re.sub(r'href="([^"]+)"', lambda m: f'href="{m.group(1).replace("&", "&amp;")}"', html_txt)
    parts = re.split(r'(<[^>]+>)', html_txt)
    return ''.join(p if p.startswith('<') else _html.escape(p) for p in parts)


def send(html: str):
    api = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0, len(html), 3800):
        chunk = sanitize(html[i : i + 3800])
        r = requests.post(api, json={"chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML"})
        r.raise_for_status()

# ───── MAIN ─────

def main():
    pool_onec, pool_other = collect_raw()
    pool_onec = title_filter(pool_onec)[: int(PERC_ONEC * 500)]
    pool_other = title_filter(pool_other)[: 500 - len(pool_onec)]
    merged = pool_onec + pool_other

    filtered = asyncio.run(body_filter(merged))
    ranked = gpt_rank(filtered)
    digest_list = layout(ranked)

    prompt = build_prompt(digest_list)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=1200,
    )
    send(resp.choices[0].message.content.strip())

if __name__ == "__main__":
    main()
