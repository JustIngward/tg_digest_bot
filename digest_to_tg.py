#!/usr/bin/env python3
"""IT‑Digest Telegram bot — v10.0 (2025‑04‑22)

◼️ Откат от недоступного встроенного browser.
◼️ Реализуем **свой** инструмент `fetch_news` (NewsAPI) через function‑calling.
◼️ GPT‑4o вызывает функцию, Python тянет статьи, модель пишет краткие выжимки.
"""

from __future__ import annotations

import os, re, sqlite3, time, datetime as dt, requests, textwrap, json
from urllib.parse import urlparse
from dotenv import load_dotenv
from openai import OpenAI

# ───── CONFIG ─────
load_dotenv()
TZ                = dt.timezone(dt.timedelta(hours=3))
MODEL             = os.getenv("MODEL", "gpt-4o")
TEMPERATURE       = float(os.getenv("TEMPERATURE", 0.7))
MAX_AGE_DAYS      = int(os.getenv("MAX_AGE_DAYS", 7))
MIN_NEWS_LINES    = int(os.getenv("MIN_NEWS_LINES", 6))
MAX_RETRIES       = int(os.getenv("MAX_RETRIES", 3))
SQLITE_PATH       = os.getenv("DB_PATH", "sent_news.db")
TG_TOKEN          = os.environ["TG_TOKEN"]
CHAT_ID           = os.environ["CHAT_ID"]
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
if NEWS_API_KEY is None:
    raise EnvironmentError("NEWS_API_KEY is not set — добавь ключ NewsAPI в Secrets / .env")

WHITELIST = {
    "cnews.ru", "tadviser.ru", "vc.ru", "rbc.ru", "gazeta.ru",
    "1c.ru", "infostart.ru", "odysseyconsgroup.com",
    "rusbase.ru", "trends.rbc.ru", "novostiitkanala.ru", "triafly.ru",
}

client = OpenAI()

# ───── DB helpers ─────
SCHEMA = """CREATE TABLE IF NOT EXISTS sent (
    fp TEXT PRIMARY KEY,
    ts DATETIME DEFAULT CURRENT_TIMESTAMP
);"""

def db_conn():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute(SCHEMA)
    return conn

def fp(url:str):
    return str(hash(url))

def already_sent(url:str):
    with db_conn() as c:
        return c.execute("SELECT 1 FROM sent WHERE fp=?", (fp(url),)).fetchone() is not None

def mark_sent(url:str):
    with db_conn() as c:
        c.execute("INSERT OR IGNORE INTO sent(fp) VALUES (?)", (fp(url),))

# ───── Tool: fetch_news ─────

def fetch_news(topic:str, n:int=5):
    """Get n fresh articles for topic from NewsAPI restricted to WHITELIST."""
    url="https://newsapi.org/v2/everything"
    from_date=(dt.datetime.utcnow()-dt.timedelta(days=MAX_AGE_DAYS)).isoformat("T","seconds")
    params={
        "q":topic,
        "from":from_date,
        "language":"ru",
        "domains":",".join(WHITELIST),
        "pageSize":n,
        "sortBy":"publishedAt",
        "apiKey":NEWS_API_KEY,
    }
    data=requests.get(url,params=params,timeout=10).json()
    res=[]
    for art in data.get("articles",[]):
        res.append({
            "title":art["title"],
            "url":art["url"],
            "published":art["publishedAt"][:10],
            "source":art["source"]["name"],
            "description":art.get("description",""),
        })
    return res[:n]

# ───── PROMPT ─────

def build_prompt():
    today=dt.datetime.now(TZ).strftime("%d %b %Y")
    return textwrap.dedent(f"""
        Ты — русский IT‑аналитик. Используй функцию fetch_news для поиска статей.
        Сделай дайджест в Markdown, следуя правилам:
        • Формат новости: "- **Заголовок** — 1–2 предложения. [Источник](URL) (DD.MM.YYYY)".
        • Три секции: 🌍 **ГЛОБАЛЬНЫЙ IT**, 🇷🇺 **РОССИЙСКИЙ TECH**, 🟡 **ЭКОСИСТЕМА 1С**.
        • Минимум {MIN_NEWS_LINES} новостей суммарно.
        • В конце блок "💡 **Insight:**".
        • Заголовок: "🗞️ **IT‑Digest • {today}**".
        • Без UTM‑меток и лишних эмодзи.
    """).strip()

TOOLS=[{
    "type":"function",
    "function":{
        "name":"fetch_news",
        "description":"Возвращает список свежих статей по теме",
        "parameters":{
            "type":"object",
            "properties":{
                "topic":{"type":"string"},
                "n":{"type":"integer","default":5},
            },
            "required":["topic"]
        }
    }
}]

# ───── TOOL DISPATCH ─────
FUNCTIONS={"fetch_news":fetch_news}

NEWS_RE=re.compile(r"^\s*[-*]\s+\*\*.+?\*\*.+\[(?P<text>.*?)\]\((?P<url>https?://[^)\s]+)\)\s*\(\d{2}\.\d{2}\.\d{4}?\)")

def chat_digest():
    msgs=[{"role":"user","content":build_prompt()}]
    while True:
        resp=client.chat.completions.create(
            model=MODEL,
            messages=msgs,
            tools=TOOLS,
            tool_choice="auto",
            temperature=TEMPERATURE,
            max_completion_tokens=900,
        )
        choice=resp.choices[0]
        if choice.finish_reason=="tool_call":
            call=choice.message.tool_calls[0]
            name=call.function.name
            args=json.loads(call.function.arguments)
            result=FUNCTIONS[name](**args)
            msgs.append(choice.message)
            msgs.append({"role":"tool","tool_call_id":call.id,"name":name,"content":json.dumps(result,ensure_ascii=False)})
            continue
        return choice.message.content.strip()

# ───── VALIDATE ─────

def extract_urls(md:str):
    for line in md.splitlines():
        m=NEWS_RE.match(line)
        if m:
            yield m.group("url")

def validate(md:str):
    lines=[l for l in md.splitlines() if NEWS_RE.match(l)]
    if len(lines)<MIN_NEWS_LINES:
        return False
    for url in extract_urls(md):
        host=urlparse(url).netloc
        if not any(host.endswith(d) for d in WHITELIST):
            return False
        if already_sent(url):
            return False
    return True

# ───── TELEGRAM ─────

def send_tg(text:str):
    api=f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for chunk in (text[i:i+3900] for i in range(0,len(text),3900)):
        requests.post(api,json={"chat_id":CHAT_ID,"text":chunk,"parse_mode":"Markdown","disable_web_page_preview":False},timeout=20).raise_for_status()

# ───── MAIN ─────

def main():
    for attempt in range(1,MAX_RETRIES+1):
        md=chat_digest()
        if validate(md):
            for url in extract_urls(md):
                mark_sent(url)
            send_tg(md)
            print("Digest sent ✔︎")
            return
        print(f"Attempt {attempt}: invalid digest, retry …")
        time.sleep(3)
    raise RuntimeError("Не удалось собрать валидный дайджест")

if __name__=="__main__":
    main()
