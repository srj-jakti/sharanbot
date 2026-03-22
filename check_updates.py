import httpx
import json

BOT_TOKEN = "8385544370:AAG6_bbRsVSC-PmIsfqHJGo3waO4p_OgzN4"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

r = httpx.get(f"{API}/getUpdates", params={"limit": 10})
data = r.json()
if data.get("ok"):
    for u in data.get("result", []):
        msg = u.get("message", {})
        chat = msg.get("chat", {})
        text = msg.get("text", "")[:50]
        uid = u["update_id"]
        cid = chat.get("id")
        uname = msg.get("from", {}).get("username", "?")
        print(f"update={uid} chat={cid} user={uname} text={text}")
    if not data.get("result"):
        print("No updates found - send a message to @sharansrjbot on Telegram first")
else:
    print(f"Error: {data}")
