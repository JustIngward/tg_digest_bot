#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v5.3 (2025‑04‑21)

• Исправлена синтаксическая ошибка в строке `ALLOWED_DOMAINS` (кавычки).  
• Убрали emoji 🛠 — некоторые CI‑линтеры ломались.  
• Остальной функционал без изменений.
"""
import os, re, sqlite3, datetime as dt, requests, time
from urllib.parse import urlparse
from dotenv import load_dotenv
from openai import OpenAI

# ───────── CONFIG ─────────
load_dotenv()
TZ              = dt.timezone(dt.timedelta(hours=3))
MODEL           = os.getenv("MODEL", "o3")
TEMPERATURE     = float(os.getenv("TEMPERATURE", 1))
MAX_AGE_HOURS   = int(os.getenv("MAX_AGE_HOURS", 168))
MIN_NEWS        = int(os.getenv("MIN_NEWS", 6))
MIN_NEWS_SOFT   = int(os.getenv("MIN_NEWS_SOFT", 3))
MAX_ITER        = int(os.getenv("MAX_ITER", 6))
TG_TOKEN        = os.environ["TG_TOKEN"]
CHAT_ID         = os.environ["CHAT_ID"]
ALLOWED_DOMAINS = [d.strip().lower() for d in os.getenv("ALLOWED_DOMAINS", "").split(',') if d.strip()]

client = OpenAI()

NEWS_RE = re.compile(r"^\s*[-*]\s*(?:\*\*)?.+?\]\((https?://[^)\s]+)\)\s*\((\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\)")

# ───── HELPERS ─────

def allowed_domain(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith('.' + d) for d in ALLOWED_DOMAINS) if ALLOWED_DOMAINS else True

def fresh(day: int, mon: int, year: int | None) -> bool:
    y = year or dt.datetime.now(TZ).year
    pub = dt.datetime(y, mon, day, tzinfo=TZ)
    return (dt.datetime.now(TZ) - pub).total_seconds() <= MAX_AGE_HOURS * 3600

def head_ok(url: str) -> bool:
    try:
        return requests.head(url, allow_redirects=True, timeout=5).status_code < 400
    except requests.RequestException:
        return False

# ───── PROMPT ─────

def make_prompt(today: str) -> str:
    days = MAX_AGE_HOURS // 24
    wl = ", ".join(ALLOWED_DOMAINS) if ALLOWED_DOMAINS else "проверенных источников"
    return f"""
**ЗАДАЧА**: сформировать RAW‑дайджест (Markdown) для IT‑департамента.

⚠️ **ЖЁСТКИЕ ПРАВИЛА**
1. Используй ТОЛЬКО реальные статьи, опубликованные ≤ {days} дней назад, с доменов: {wl}.
2. Не выдумывай новости. Если статьи нет — ищи другую в whitelist.
3. Каждая новость = ОДНА строка (≤30 слов) в формате:
   - **Короткий жирный заголовок** — суть. [Источник](URL) (DD.MM.YYYY)
4. Пиши на русском языке. Сохраняй русские названия компаний.
5. Секции выводи в порядке:🌍, 🇷🇺, 🟡. **Между секциями оставляй одну пустую строку.**
6. Без лишних эмодзи и UTM‑меток.
7. Минимум {MIN_NEWS} строк суммарно.
8. В конце добавь:
   💡 **Insight:** 2‑3 предложения, почему эти события важны.

Дата сегодня: {today}
"""

# ───── COLLECTOR ─────

def collect_once() -> str:
    prompt = make_prompt(dt.datetime.now(TZ).strftime('%d %b %Y'))
    resp = client.responses.create(
        model=MODEL,
        tools=[{"type": "web_search"}],
        input=[{"role": "user", "content": prompt}],
        temperature=TEMPERATURE,
        store=False,
    )
    return resp.output_text

# ───── VALIDATOR ─────

def validate(raw: str):
    valid = []
    for ln in raw.splitlines():
        m = NEWS_RE.match(ln)
        if not m:
            continue
        url, day, mon, year = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        year = int(year) if year else None
        if allowed_domain(url) and fresh(day, mon, year) and head_ok(url):
            valid.append(ln.rstrip())
    return valid

# ───── PIPELINE ─────

def produce_digest():
    for i in range(1, MAX_ITER + 1):
        draft = collect_once()
        lines = validate(draft)
        print(f"iter {i}: {len(lines)} valid lines")
        if len(lines) >= MIN_NEWS or (i == MAX_ITER and len(lines) >= MIN_NEWS_SOFT):
            return "\n".join(lines)
        time.sleep(2)
    raise RuntimeError("Не удалось собрать дайджест: мало статей")

# ───── SEND ─────

def send(msg: str):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0, len(msg), 3900):
        r = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": msg[i:i + 3900],
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        })
        if r.status_code != 200:
            raise RuntimeError(r.text)

# ───── MAIN ─────
if __name__ == '__main__':
    sqlite3.connect('sent_hashes.db').execute('CREATE TABLE IF NOT EXISTS sent(hash TEXT PRIMARY KEY)')
    digest = produce_digest()
    if digest:
        send(digest)
