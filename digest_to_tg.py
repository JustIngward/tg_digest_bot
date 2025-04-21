#!/usr/bin/env python3
import os, textwrap, datetime, openai, requests

MODEL = "o3"              # << сюда можно вписать gpt-4o-mini или o3
TZ = datetime.timezone(datetime.timedelta(hours=3))   # Moscow
def get_headlines(query: str, hours: int = 96, n: int = 7) -> list[dict]:
    """Возвращает n свежих статей по теме за последние hours часов"""
    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query,
        "from": (dt.datetime.utcnow() - dt.timedelta(hours=hours)).isoformat(),
        "sortBy": "publishedAt",
        "pageSize": n,
        "language": "ru",
        "apiKey": os.getenv("NEWS_API_KEY"),
    }
    data = requests.get(url, params=params, timeout=10).json()
    return [{"title": a["title"], "url": a["url"], "published": a["publishedAt"]}
            for a in data["articles"]]
tools = [{
    "type": "function",
    "function": {
        "name": "get_headlines",
        "description": "Получает свежие публикации из интернета",
        "parameters": {
            "type": "object",
            "properties": {
                "query":  {"type": "string",  "description": "тема, напр. '1С'"},
                "hours":  {"type": "integer", "description": "за сколько часов"},
                "n":      {"type": "integer", "description": "сколько статей"},
            },
            "required": ["query"]
        }
    }
}]
def prompt(period: str) -> str:
    today = datetime.datetime.now(TZ).strftime("%d %b %Y")
    #if period == "weekly":
        #start = (datetime.datetime.now(TZ) - datetime.timedelta(days=6)).strftime("%d %b")
        #return (f"Сделай недельный IT‑дайджест ({start} – {today}): 7 новостей, "
                #"ссылки, минимум один пункт о 1С.")
    #if period == "monthly":
        #return (f"Сделай месячный IT‑дайджест ({today}): 10 трендов, ссылки, эффект для бизнеса.")
    return (f"Сделай IT‑дайджест за {today}: 5 новостей за 48 ч, 2‑3 строки каждая, со ссылками. Дайджест должен содержать обзор мирового IT-рынка, Российского IT рынка и новостей 1С")
def build(period: str) -> str:
    openai.api_key = os.getenv("OPENAI_API_KEY")
    resp = openai.chat.completions.create(
        model=MODEL,
        messages=[{"role":"user", "content": prompt(period)}],
        tools=tools,
        tool_choice="auto",
        temperature=1, # было 0.4. Новая версия поддерживает только 1
        max_completion_tokens=2048,  #было max_tokens
    )
    return resp.choices[0].message.content.strip()
while resp.choices[0].finish_reason == "tool_call":
        call = resp.choices[0].message.tool_calls[0]
        args = eval(call.function.arguments)           # json.loads в проде!
        result = get_headlines(**args)                 # поход в интернет
        msgs += [
            resp.choices[0].message,                   # сам tool_call
            {"role": "tool", "tool_call_id": call.id,
             "name": call.function.name,
             "content": str(result)},
        ]
        resp = openai.chat.completions.create(
            model = MODEL,
            messages = msgs,
            tools = tools,
            max_completion_tokens = 2048,
            temperature = 1,
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
