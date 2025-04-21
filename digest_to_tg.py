#!/usr/bin/env python3
import os, textwrap, datetime, openai, requests

MODEL = "o3"              # << сюда можно вписать gpt-4o-mini или o3
TZ = datetime.timezone(datetime.timedelta(hours=3))   # Moscow

def prompt(period: str) -> str:
    today = datetime.datetime.now(TZ).strftime("%d %b %Y")
    #if period == "weekly":
        #start = (datetime.datetime.now(TZ) - datetime.timedelta(days=6)).strftime("%d %b")
        #return (f"Сделай недельный IT‑дайджест ({start} – {today}): 7 новостей, "
                #"ссылки, минимум один пункт о 1С.")
    #if period == "monthly":
        #return (f"Сделай месячный IT‑дайджест ({today}): 10 трендов, ссылки, эффект для бизнеса.")
    #return (f"Сделай IT‑дайджест за {today}: 5 новостей за 24 ч, 2‑3 строки каждая, со ссылками.")

def build(period: str) -> str:
    openai.api_key = os.getenv("OPENAI_API_KEY")
    resp = openai.chat.completions.create(
        model=MODEL,
        messages=[{"role":"user", "content": prompt(period)}],
        temperature=0.4,
        max_tokens=2048,
    )
    return resp.choices[0].message.content.strip()

def send(text: str):
    url = f"https://api.telegram.org/bot{os.getenv('TG_TOKEN')}/sendMessage"
    for chunk in textwrap.wrap(text, 4096):
        requests.post(url, json={
            "chat_id": os.getenv("CHAT_ID"),
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False})

if __name__ == "__main__":
    send(build(os.getenv("PERIOD", "daily")))
