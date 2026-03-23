"""Quick test: can we establish a WebSocket connection to OpenClaw bridge?"""
import asyncio
import aiohttp

async def test():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.ws_connect("http://127.0.0.1:18789/") as ws:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=3)
                print(f"SUCCESS: WebSocket connected, got: {msg}")
                await ws.close()
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")

asyncio.run(test())
