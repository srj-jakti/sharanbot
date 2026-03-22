"""Send a real-life challenge to SharanBot via Telegram and monitor responses."""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

BOT_TOKEN = "8385544370:AAG6_bbRsVSC-PmIsfqHJGo3waO4p_OgzN4"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# The challenge message
CHALLENGE = """Build me a 3D car racing game using React Native + Expo with the following requirements:

1. **Research Phase**: Research the best approach for 3D game development in React Native/Expo (Three.js, expo-three, react-three-fiber, etc.)

2. **Development Phase**:
   - 3D car model with basic physics (acceleration, braking, steering)
   - A race track with turns, scenery (trees, barriers, sky)
   - Particle effects for tire smoke/dust when drifting
   - Speed indicator HUD
   - Lap counter and timer
   - At least 2 different camera angles (behind car, top-down)
   - Touch controls (left/right steering, accelerate/brake buttons)
   - Start screen with "Start Race" button
   - Game over screen with lap times

3. **Bundle Phase**: 
   - Create a complete Expo project that can be built
   - Provide the command to start the Expo dev server
   - Generate an Expo QR code so I can test on my phone

Build the COMPLETE project with all files. Use your team orchestration to assign specialists for different parts. Start working now - research, plan, code, and deliver."""


async def get_chat_id():
    """Get the chat ID from recent updates."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(f"{API}/getUpdates", params={"limit": 5, "offset": -5})
        data = r.json()
        if data.get("ok") and data.get("result"):
            for update in reversed(data["result"]):
                msg = update.get("message", {})
                chat = msg.get("chat", {})
                if chat.get("id"):
                    return chat["id"]
    return None


async def send_message(chat_id: int, text: str):
    """Send a message to the bot."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        })
        data = r.json()
        if data.get("ok"):
            print(f"[SENT] Message sent to chat {chat_id} (msg_id: {data['result']['message_id']})")
            return data["result"]["message_id"]
        else:
            print(f"[ERROR] Failed to send: {data}")
            return None


async def poll_responses(chat_id: int, after_msg_id: int, timeout_minutes: int = 15):
    """Poll for bot responses after our message."""
    print(f"\n[MONITOR] Watching for responses (timeout: {timeout_minutes} min)...")
    start = time.time()
    seen_ids = set()
    responses = []
    last_update_id = 0

    async with httpx.AsyncClient(timeout=60) as client:
        while (time.time() - start) < timeout_minutes * 60:
            try:
                params = {"timeout": 10, "allowed_updates": ["message"]}
                if last_update_id:
                    params["offset"] = last_update_id + 1

                r = await client.get(f"{API}/getUpdates", params=params)
                data = r.json()

                if data.get("ok"):
                    for update in data.get("result", []):
                        last_update_id = max(last_update_id, update["update_id"])
                        msg = update.get("message", {})
                        if not msg:
                            continue
                        msg_id = msg.get("message_id", 0)
                        from_user = msg.get("from", {})

                        # Only capture bot responses in our chat
                        if msg.get("chat", {}).get("id") != chat_id:
                            continue
                        if msg_id in seen_ids:
                            continue
                        seen_ids.add(msg_id)

                        if from_user.get("is_bot"):
                            text = msg.get("text", "")
                            elapsed = time.time() - start
                            print(f"\n[BOT RESPONSE] ({elapsed:.0f}s) Length: {len(text)} chars")
                            # Print first 500 chars safely
                            safe_text = text[:500].encode("ascii", errors="replace").decode()
                            print(f"  Preview: {safe_text}")
                            if len(text) > 500:
                                print(f"  ... ({len(text) - 500} more chars)")
                            responses.append({
                                "msg_id": msg_id,
                                "elapsed_s": round(elapsed, 1),
                                "length": len(text),
                                "text": text,
                            })

                # Check if bot is still processing (no response for a while)
                if responses:
                    last_resp_time = start + responses[-1]["elapsed_s"]
                    idle = time.time() - last_resp_time
                    if idle > 120:  # 2 min idle after last response = likely done
                        print(f"\n[MONITOR] Bot idle for {idle:.0f}s after last response. Assuming done.")
                        break

            except Exception as e:
                print(f"[POLL ERROR] {e}")
                await asyncio.sleep(5)

            await asyncio.sleep(2)

    return responses


async def main():
    print("=" * 70)
    print("SHARANBOT REAL-LIFE CHALLENGE TEST")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 70)

    # Get chat ID
    print("\n[SETUP] Getting chat ID from recent updates...")
    chat_id = await get_chat_id()
    if not chat_id:
        print("[ERROR] No chat ID found. Send any message to @sharansrjbot first on Telegram.")
        print("[FALLBACK] Sending via process_direct instead...")
        # Use the internal API
        sys.path.insert(0, ".")
        from sharanbot.bus.events import InboundMessage
        from sharanbot.bus.queue import MessageBus

        bus = MessageBus()
        msg = InboundMessage(
            channel="telegram",
            sender_id="test",
            chat_id="direct",
            content=CHALLENGE,
        )
        await bus.publish_inbound(msg)
        print("[SENT] Message published to internal bus")
        return

    print(f"[SETUP] Chat ID: {chat_id}")

    # Send challenge
    print(f"\n[CHALLENGE] Sending 3D car racing game challenge...")
    msg_id = await send_message(chat_id, CHALLENGE)
    if not msg_id:
        print("[ERROR] Failed to send message")
        return

    # Monitor responses
    responses = await poll_responses(chat_id, msg_id, timeout_minutes=15)

    # Save results
    print(f"\n{'=' * 70}")
    print(f"RESULTS: {len(responses)} responses received")
    print(f"{'=' * 70}")

    output_path = Path("challenge_results.json")
    output_path.write_text(json.dumps({
        "challenge": "3D Car Racing Game",
        "sent_at": datetime.now().isoformat(),
        "chat_id": chat_id,
        "responses": [{
            "msg_id": r["msg_id"],
            "elapsed_s": r["elapsed_s"],
            "length": r["length"],
            "preview": r["text"][:1000],
        } for r in responses],
        "total_responses": len(responses),
    }, indent=2, ensure_ascii=True))
    print(f"\nResults saved to {output_path}")

    # Print full responses to file
    full_output = Path("challenge_full_output.txt")
    with open(full_output, "w", encoding="utf-8") as f:
        f.write(f"SHARANBOT CHALLENGE TEST - {datetime.now().isoformat()}\n")
        f.write(f"Challenge: 3D Car Racing Game\n")
        f.write(f"{'=' * 70}\n\n")
        for i, r in enumerate(responses, 1):
            f.write(f"--- Response {i} ({r['elapsed_s']}s, {r['length']} chars) ---\n")
            f.write(r["text"])
            f.write("\n\n")
    print(f"Full output saved to {full_output}")


if __name__ == "__main__":
    asyncio.run(main())
