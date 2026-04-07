# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.9.0"]
# ///
"""Lightweight HTTP + SSE dashboard server.

Receives tool call events from the MCP server via POST /api/events,
narrates them, stores them, and broadcasts via SSE to browser clients.

Usage:
    uv run displayer/server.py [--port 15580]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure sibling modules are importable regardless of cwd
_pkg_dir = Path(__file__).resolve().parent
if str(_pkg_dir) not in sys.path:
    sys.path.insert(0, str(_pkg_dir))

from aiohttp import web  # noqa: E402

from event_store import EventStore  # noqa: E402
from narration import NarrationEngine  # noqa: E402

store = EventStore()
narration = NarrationEngine()


async def handle_index(request: web.Request) -> web.Response:
    """Serve the dashboard HTML."""
    html_path = Path(__file__).parent / "static" / "dashboard.html"
    return web.FileResponse(html_path)


async def handle_post_event(request: web.Request) -> web.Response:
    """Receive a tool call event from the MCP server."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    tool_name = data.get("tool", "unknown")
    params = data.get("params", {})
    result = data.get("result", "")

    text = narration.narrate(tool_name, params, result)
    if text is None:
        return web.json_response({"status": "ok", "suppressed": True})

    event_type = "thinking"
    if any(tool_name.startswith(p) for p in ("combat_play", "mp_combat_play", "use_potion", "mp_use_potion")):
        event_type = "action"

    event = store.append(
        text=text,
        event_type=event_type,
        tool_name=tool_name,
        raw_data={"params": params},
    )

    return web.json_response({"status": "ok", "event_id": event.id})


async def handle_events_stream(request: web.Request) -> web.StreamResponse:
    """SSE endpoint — streams events to the browser."""
    response = web.StreamResponse()
    response.content_type = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Access-Control-Allow-Origin"] = "*"
    await response.prepare(request)

    queue = store.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
                await response.drain()
                continue

            if event is None:
                await response.write(b"event: clear\ndata: {}\n\n")
                await response.drain()
                continue

            payload = json.dumps(event.to_dict(), ensure_ascii=False)
            msg = f"id: {event.id}\nevent: message\ndata: {payload}\n\n"
            await response.write(msg.encode("utf-8"))
            await response.drain()
    except (ConnectionResetError, ConnectionError, asyncio.CancelledError):
        pass
    finally:
        store.unsubscribe(queue)

    return response


async def handle_events_history(request: web.Request) -> web.Response:
    """Return past events as JSON for history recovery on page load."""
    since_id = int(request.query.get("since_id", "0"))
    events = store.get_history(since_id)
    return web.json_response(
        [e.to_dict() for e in events],
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_clear(request: web.Request) -> web.Response:
    """Clear all events."""
    store.clear()
    return web.json_response(
        {"status": "ok"},
        headers={"Access-Control-Allow-Origin": "*"},
    )


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/events", handle_post_event)
    app.router.add_get("/events/stream", handle_events_stream)
    app.router.add_get("/events/history", handle_events_history)
    app.router.add_post("/api/clear", handle_clear)
    app.router.add_static("/static/", Path(__file__).parent / "static")
    return app


def main():
    parser = argparse.ArgumentParser(description="STS2 AI Thinking Dashboard")
    parser.add_argument("--port", type=int, default=15580, help="Dashboard server port")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Dashboard server host")
    args = parser.parse_args()

    app = create_app()
    print(f"🎮 STS2 AI Dashboard: http://localhost:{args.port}", file=sys.stderr)
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
