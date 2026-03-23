"""OpenClaw WebSocket Bridge — makes SharanBot appear as the OpenClaw AI backend.

Tandem Browser's Wingman panel connects to ws://127.0.0.1:18789 expecting an
OpenClaw-protocol server.  This bridge implements that protocol, routing chat
messages to the SharanBot agent and streaming responses back.

Protocol (JSON-RPC over WebSocket):
  Server→Client: {type:"event", event:"connect.challenge", payload:{nonce}}
  Client→Server: {type:"req", id, method:"connect", params:{...}}
  Server→Client: {type:"res", id, ok:true, payload:{status:"connected"}}
  Client→Server: {type:"req", id, method:"chat.send", params:{sessionKey, message, idempotencyKey}}
  Server→Client: {type:"event", event:"chat", payload:{state:"delta"|"final", message:{...}}}
  Client→Server: {type:"req", id, method:"chat.history", params:{sessionKey, limit}}

Also serves HTTP endpoints:
  GET /v1/status           — OpenClaw detection (tandem's openclaw-detect.ts)
  GET /config/openclaw-connect  — Connect params for the challenge flow
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Callable, Awaitable

from aiohttp import web, WSMsgType
from loguru import logger

OPENCLAW_PORT = 18789
_chat_history: list[dict[str, Any]] = []
_MAX_HISTORY = 100


class OpenClawBridge:
    """WebSocket + HTTP server implementing minimal OpenClaw protocol."""

    def __init__(
        self,
        port: int = OPENCLAW_PORT,
        on_chat: Callable[[str, str], Awaitable[str]] | None = None,
    ):
        self.port = port
        self._on_chat = on_chat  # async (session_key, message) -> response
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._ws_clients: list[web.WebSocketResponse] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    @web.middleware
    async def _cors_middleware(request: web.Request, handler):
        """Add CORS headers so the Electron shell (file:// origin) can fetch."""
        if request.method == "OPTIONS":
            resp = web.Response(status=204)
        else:
            resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    async def start(self) -> None:
        self._app = web.Application(middlewares=[self._cors_middleware])
        self._app.router.add_get("/v1/status", self._handle_status)
        self._app.router.add_get("/config/openclaw-connect", self._handle_connect_params)
        self._app.router.add_get("/ws", self._handle_ws)
        # Also accept WebSocket at root (openclaw-backend.js connects to ws://host:port)
        self._app.router.add_get("/", self._handle_ws_or_root)

        # Accept activity events from Tandem (Wingman Vision)
        self._app.router.add_post("/api/sessions/main/events", self._handle_activity_event)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await site.start()
        logger.info("OpenClaw Bridge started on port {}", self.port)

    async def _handle_activity_event(self, request: web.Request) -> web.Response:
        """Accept activity events from Tandem's WingmanStream (POST /api/sessions/main/events)."""
        try:
            data = await request.json()
        except Exception:
            data = None
        logger.debug(f"Received activity event: {data}")
        return web.json_response({"ok": True})

    async def stop(self) -> None:
        for ws in list(self._ws_clients):
            await ws.close()
        self._ws_clients.clear()
        if self._runner:
            await self._runner.cleanup()
        logger.info("OpenClaw Bridge stopped")

    # ------------------------------------------------------------------
    # HTTP handlers
    # ------------------------------------------------------------------

    async def _handle_status(self, _request: web.Request) -> web.Response:
        """Respond to OpenClaw detection check."""
        return web.json_response({
            "ok": True,
            "version": "1.0.0",
            "service": "sharanbot-openclaw-bridge",
            "gateway": {
                "hooks": {
                    "token": "sharanbot-bridge-token",
                }
            },
        })

    async def _handle_connect_params(self, request: web.Request) -> web.Response:
        """Return connect params for the challenge flow."""
        nonce = request.query.get("nonce", "")
        if not nonce:
            return web.json_response({"error": "nonce required"}, status=400)

        return web.json_response({
            "params": {
                "token": "sharanbot-bridge-token",
                "nonce": nonce,
                "deviceId": "sharanbot-agent",
                "deviceName": "SharanBot",
                "platform": "sharanbot",
            }
        })

    # ------------------------------------------------------------------
    # WebSocket handler
    # ------------------------------------------------------------------

    async def _handle_ws_or_root(self, request: web.Request) -> web.StreamResponse:
        """Handle root path — WebSocket upgrade or simple HTML."""
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._handle_ws(request)
        return web.Response(text="SharanBot OpenClaw Bridge", content_type="text/plain")

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.append(ws)
        logger.info("OpenClaw Bridge: client connected")

        # Send connect.challenge with embedded connect params so the client
        # does NOT need an HTTP fetch (avoids CORS/file:// origin issues).
        nonce = str(uuid.uuid4())
        await ws.send_json({
            "type": "event",
            "event": "connect.challenge",
            "payload": {
                "nonce": nonce,
                "connectParams": {
                    "token": "sharanbot-bridge-token",
                    "nonce": nonce,
                    "deviceId": "sharanbot-agent",
                    "deviceName": "SharanBot",
                    "platform": "sharanbot",
                },
            },
        })

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._handle_message(ws, msg.data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self._ws_clients.remove(ws)
            logger.info("OpenClaw Bridge: client disconnected")

        return ws

    async def _handle_message(self, ws: web.WebSocketResponse, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type", "")
        if msg_type != "req":
            logger.debug("OpenClaw Bridge: ignoring non-req message type={}", msg_type)
            return

        msg_id = data.get("id", "")
        method = data.get("method", "")
        params = data.get("params", {})
        logger.debug("OpenClaw Bridge: req method={}", method)

        if method == "connect":
            logger.info("OpenClaw Bridge: client authenticated (device={})" , params.get("deviceId", "unknown"))
            await ws.send_json({
                "type": "res",
                "id": msg_id,
                "ok": True,
                "payload": {
                    "status": "connected",
                    "sessionKey": params.get("sessionKey", "agent:main:main"),
                },
            })

        elif method == "chat.send":
            session_key = params.get("sessionKey", "agent:main:main")
            message_text = params.get("message", "")
            idempotency_key = params.get("idempotencyKey", str(uuid.uuid4()))

            # Acknowledge the request
            run_id = str(uuid.uuid4())
            await ws.send_json({
                "type": "res",
                "id": msg_id,
                "ok": True,
                "payload": {
                    "runId": run_id,
                    "status": "started",
                },
            })

            # Store user message in history
            _chat_history.append({
                "id": idempotency_key,
                "role": "user",
                "content": message_text,
                "timestamp": int(time.time() * 1000),
            })
            if len(_chat_history) > _MAX_HISTORY:
                _chat_history.pop(0)

            # Process via SharanBot agent (async)
            asyncio.create_task(
                self._process_chat(ws, session_key, message_text, run_id)
            )

        elif method == "chat.history":
            limit = params.get("limit", 20)
            recent = _chat_history[-limit:]
            await ws.send_json({
                "type": "res",
                "id": msg_id,
                "ok": True,
                "payload": {
                    "messages": recent,
                },
            })

        else:
            await ws.send_json({
                "type": "res",
                "id": msg_id,
                "ok": False,
                "error": {"code": "UNKNOWN_METHOD", "message": f"Unknown method: {method}"},
            })

    async def _process_chat(
        self,
        ws: web.WebSocketResponse,
        session_key: str,
        message: str,
        run_id: str,
    ) -> None:
        """Route chat to SharanBot agent and stream response."""
        try:
            if self._on_chat:
                response = await self._on_chat(session_key, message)
            else:
                response = "SharanBot OpenClaw bridge is running but no agent callback is configured."

            # Send streaming delta
            if not ws.closed:
                await ws.send_json({
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "state": "delta",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": response}],
                        },
                    },
                })

            # Send final
            if not ws.closed:
                msg_id = str(uuid.uuid4())
                await ws.send_json({
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "state": "final",
                        "message": {
                            "id": msg_id,
                            "role": "assistant",
                            "content": [{"type": "text", "text": response}],
                        },
                    },
                })

            # Store assistant message in history
            _chat_history.append({
                "id": msg_id if not ws.closed else str(uuid.uuid4()),
                "role": "assistant",
                "content": response,
                "timestamp": int(time.time() * 1000),
            })
            if len(_chat_history) > _MAX_HISTORY:
                _chat_history.pop(0)

        except Exception as e:
            logger.error("OpenClaw Bridge chat error: {}", e)
            if not ws.closed:
                await ws.send_json({
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "state": "error",
                        "message": {"text": str(e)},
                    },
                })
