#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v6.1 (2025‑04‑21)

Изменения v6.1
──────────────
* **Whitelist убран** — теперь пропускаем любой домен, если семантический
  скоринг ≥ 3 и HEAD < 400.  
* Prompt адаптируется: если список доменов пуст, фраза про whitelist исчезает.  
* Функция `allowed_domain` теперь всегда `True` (оставил хук, если решите
  вернуть список).  
Остальная логика (HEAD, свежесть, scoring, дедуп, сортировка) без изменений.
"""
import os, re, sqlite3, datetime as dt, requests, time, hashlib
from urllib.parse import urlparse, urlsplit, urlunsplit, parse_qsl, urlencode
from dotenv import load_dotenv
from openai import OpenAI

# ───────── CONFIG ─────────
load_dotenv()
TZ              = dt.timezone(dt.timedelta(hours=3))
MODEL           = os.getenv("MODEL", "o3")
TEMP_GEN        = float(os.getenv("TEMPERATURE", 1))
TEMP_SCORE      = 0.2
MAX_AGE_HOURS   = int(os.getenv("MAX_AGE_HOURS", 168))
MIN_NEWS        = int(os.getenv("MIN_NEWS", 6))
MIN_NEWS_SOFT   = int(os.getenv("MIN_NEWS_SOFT", 3))
MAX_ITER        = int(os.getenv("MAX_ITER", 6))
TG_TOKEN        = os.environ["TG_TOKEN"]
CHAT_ID         = os.environ["CHAT_ID"]
# пустой список → нет доменного фильтра
ALLOWED_DOMAINS = [d.strip().lower() for d in os.getenv("ALLOWED_DOMAINS","").split(',') if d.strip()]

client = OpenAI()

NEWS_RE     = re.compile(r"^\s*[-*]\s*(?:\*\*)?.+?\]\((https?://[^)\s]+)\)\s*\((\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\)")
SECTION_RE  = re.compile(r"^\s*(🌍|🇷🇺|🟡)")
SECT_ORDER  = {"🌍":0, "🇷🇺":1, "🟡":2}

# ───── HELPERS ─────

def strip_utm(url:str)->str:
    p=list(urlsplit(url)); p[3]=urlencode([(k,v) for k,v in parse_qsl(p[3]) if not k.startswith('utm_')]); return urlunsplit(p)

def allowed_domain(url:str)->bool:
    if not ALLOWED_DOMAINS:
        return True
    host=(urlparse(url).hostname or "").lower()
    return any(host==d or host.endswith('.'+d) for d in ALLOWED_DOMAINS)

def fresh(d:int,m:int,y:int|None)->bool:
    y=y or dt.datetime.now(TZ).year
    return (dt.datetime.now(TZ)-dt.datetime(y,m,d,tzinfo=TZ)).total_seconds()<=MAX_AGE_HOURS*3600

def head_ok(u:str)->bool:
    try:
        return requests.head(u,allow_redirects=True,timeout=5).status_code<400
    except requests.RequestException:
        return False

def md5u(u:str)->str:
    return hashlib.md5(u.encode()).hexdigest()

# ───── PROMPT ─────

def make_prompt(today:str)->str:
    days=MAX_AGE_HOURS//24
    domain_line=f"• Новости ≤{days} дней." if not ALLOWED_DOMAINS else f"• Новости ≤{days} дней, домены: {', '.join(ALLOWED_DOMAINS)}."
    return f"""
Сегодня {today}. Сформируй черновик IT‑дайджеста. Правила:
{domain_line}
• Формат: - **Заголовок** — суть. [Источник](URL) (DD.MM.YYYY)
• Секции: 🌍 ГЛОБАЛЬНЫЙ IT (пустая строка) 🇷🇺 ... (пустая строка) 🟡 ...
• Минимум {MIN_NEWS} новостей. Insight в конце.
"""

# ───── SCORER ─────

def relevance_score(line:str)->int:
    q=f"Оцени полезность новости для ИТ‑специалиста (0‑5). Только число.\nНовость: {line}"
    r=client.chat.completions.create(model=MODEL,temperature=TEMP_SCORE,messages=[{"role":"user","content":q}])
    try:return int(r.choices[0].message.content.strip()[:1])
    except: return 0

# ───── COLLECTOR ─────

def collect_once():
    r=client.responses.create(model=MODEL,tools=[{"type":"web_search"}],input=[{"role":"user","content":make_prompt(dt.datetime.now(TZ).strftime('%d %b %Y'))}],temperature=TEMP_GEN,store=False)
    return r.output_text

# ───── VALIDATOR ─────

def validate(raw:str,dedup:set):
    buckets={0:[],1:[],2:[]}
    for ln in raw.splitlines():
        sm=SECTION_RE.match(ln); sect=SECT_ORDER.get(sm.group(1)) if sm else None
        m=NEWS_RE.search(ln)
        if sect is None or not m:continue
        url,day,mon,year=m.group(1),int(m.group(2)),int(m.group(3)),m.group(4)
        year=int(year) if year else None
        url=strip_utm(url)
        h=md5u(url)
        if h in dedup:continue
        if not (allowed_domain(url) and fresh(day,mon,year) and head_ok(url)):continue
        score=relevance_score(ln)
        if score<3:continue
        buckets[sect].append((score, ln.replace(m.group(1), url)))
        dedup.add(h)
    out=[]
    for s in range(3):
        out.extend([ln for _,ln in sorted(buckets[s],key=lambda t:t[0],reverse=True)])
        out.append('')
    return out

# ───── MAIN PIPELINE ─────

def produce_digest():
    db=sqlite3.connect('sent_hashes.db'); db.execute('CREATE TABLE IF NOT EXISTS sent(hash TEXT PRIMARY KEY)'); seen={h for (h,) in db.execute('SELECT hash FROM sent')}
    for i in range(1,MAX_ITER+1):
        raw=collect_once(); lines=[ln for ln in validate(raw,seen) if ln.strip()]; print(f"iter {i}: {len(lines)} valid lines")
        if len(lines)>=MIN_NEWS or (i==MAX_ITER and len(lines)>=MIN_NEWS_SOFT):
            db.executemany('INSERT OR IGNORE INTO sent VALUES(?)',[(md5u(l),) for l in lines]); db.commit(); return "\n".join(lines)
        time.sleep(2)
    raise RuntimeError('Не удалось собрать дайджест')

# ───── SEND ─────

def send(msg:str):
    url=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"; [requests.post(url,json={"chat_id":CHAT_ID,"text":msg[i:i+3900],"parse_mode":"Markdown","disable_web_page_preview":False}) for i in range(0,len(msg),3900)]

if __name__=='__main__':
    send(produce_digest())
