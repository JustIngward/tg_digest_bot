#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v5.0 (2025‑04‑21)

🔥 Полная перезагрузка логики — без «редактора»
──────────────────────────────────────────────
Цель: собирать свежие статьи только из проверенных источников, без выдуманных
новостей, с минимальным числом вызовов API.

Основная схема v5.0
───────────────────
1. **Collector** (одна модель, web_search) → черновик.
2. **Python‑валидатор**:
   • дата ≤ `MAX_AGE_HOURS`;
   • домен ∈ `ALLOWED_DOMAINS`;
   • HEAD <400;
   • формат строки.
3. Если годных строк < `MIN_NEWS`, делаем новую попытку (до `MAX_ITER`).
4. Готовый дайджест сразу шлём в Telegram.
"""
import os, re, sqlite3, datetime as dt, requests, time
from urllib.parse import urlparse
from dotenv import load_dotenv
from openai import OpenAI

# ─────────────── CONFIG ───────────────
load_dotenv()
TZ              = dt.timezone(dt.timedelta(hours=3))
MODEL           = os.getenv("MODEL", "o3")
MAX_AGE_HOURS   = int(os.getenv("MAX_AGE_HOURS", 168))
MIN_NEWS        = int(os.getenv("MIN_NEWS", 6))
MIN_NEWS_SOFT   = int(os.getenv("MIN_NEWS_SOFT", 3))
MAX_ITER        = int(os.getenv("MAX_ITER", 6))
TG_TOKEN        = os.environ["TG_TOKEN"]
CHAT_ID         = os.environ["CHAT_ID"]
ALLOWED_DOMAINS = [d.strip().lower() for d in os.getenv("ALLOWED_DOMAINS","").split(',') if d.strip()]

client = OpenAI()

NEWS_RE = re.compile(r"^\s*[-*]\s*(?:\*\*)?.+?\]\((https?://[^)\s]+)\)\s*\((\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\)")

# ─────────────── HELPERS ───────────────

def allowed_domain(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith('.'+d) for d in ALLOWED_DOMAINS) if ALLOWED_DOMAINS else True

def fresh_date(day:int, mon:int, year:int|None)->bool:
    y = year or dt.datetime.now(TZ).year
    pub = dt.datetime(y,mon,day,tzinfo=TZ)
    return (dt.datetime.now(TZ)-pub).total_seconds() <= MAX_AGE_HOURS*3600

def head_ok(url:str)->bool:
    try:
        return requests.head(url,allow_redirects=True,timeout=5).status_code<400
    except requests.RequestException:
        return False

# ─────────────── COLLECTOR ───────────────

def prompt(today:str)->str:
    days=MAX_AGE_HOURS//24
    whitelist=", ".join(ALLOWED_DOMAINS) if ALLOWED_DOMAINS else "любых проверенных"
    return (
        f"Сегодня {today}. Сформируй черновик еженедельного IT‑дайджеста (Markdown).\n"
        f"Бери ТОЛЬКО новости из доменов: {whitelist}. Каждая новость моложе {days} дней.\n"
        "Формат одной строки: `- **Заголовок** — кратко. [Источник](URL) (DD.MM или DD.MM.YYYY)`. ≤30 слов.\n"
        "Секции: 🌍 ГЛОБАЛЬНЫЙ IT, 🇷🇺 РОССИЙСКИЙ TECH, 🟡 ЭКОСИСТЕМА 1С.\n"
        f"Минимум {MIN_NEWS} строк суммарно. Заверши блоком Insight."
    )


def collect_once()->str:
    today=dt.datetime.now(TZ).strftime('%d %b %Y')
    resp=client.responses.create(model=MODEL,tools=[{"type":"web_search"}],input=[{"role":"user","content":prompt(today)}],temperature=1,store=False)
    return resp.output_text

# ─────────────── VALIDATOR ───────────────

def validate(raw:str):
    good=[]
    for ln in raw.splitlines():
        m=NEWS_RE.match(ln)
        if not m:
            continue
        url,day,mon,year=m.group(1),int(m.group(2)),int(m.group(3)), m.group(4)
        year=int(year) if year else None
        if not (allowed_domain(url) and fresh_date(day,mon,year) and head_ok(url)):
            continue
        good.append(ln.strip())
    return good

# ─────────────── PIPELINE ───────────────

def produce_digest():
    for i in range(1,MAX_ITER+1):
        draft=collect_once()
        lines=validate(draft)
        print(f"iter {i}: {len(lines)} valid lines")
        if len(lines)>=MIN_NEWS or (i==MAX_ITER and len(lines)>=MIN_NEWS_SOFT):
            return "\n".join(lines)
        time.sleep(2)  # маленькая пауза между попытками
    raise RuntimeError("Не удалось собрать дайджест: мало статей")

# ─────────────── SEND ───────────────

def send(msg:str):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for i in range(0,len(msg),3900):
        r=requests.post(url,json={"chat_id":CHAT_ID,"text":msg[i:i+3900],"parse_mode":"Markdown","disable_web_page_preview":False})
        if r.status_code!=200:
            raise RuntimeError(r.text)

# ─────────────── MAIN ───────────────
if __name__=='__main__':
    sqlite3.connect('sent_hashes.db').execute('CREATE TABLE IF NOT EXISTS sent(hash TEXT PRIMARY KEY)')
    digest=produce_digest()
    if digest:
        send(digest)
