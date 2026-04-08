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


def _extract_state_snapshot(state: dict) -> dict | None:
    """Extract minimal stats from game state for the dashboard status bar."""
    player = state.get("player")
    run = state.get("run")
    if not player and not run:
        return None
    snap: dict = {}
    if player:
        p: dict = {}
        for k in ("hp", "max_hp", "energy", "gold", "block"):
            if k in player:
                p[k] = player[k]
        if "deck" in player and isinstance(player["deck"], list):
            p["deck"] = player["deck"]
        if p:
            snap["player"] = p
    if run:
        r: dict = {}
        if "floor" in run:
            r["floor"] = run["floor"]
        if r:
            snap["run"] = r
    snap["state_type"] = state.get("state_type", "")
    return snap if snap else None


async def handle_post_event(request: web.Request) -> web.Response:
    """Receive a tool call event from the MCP server."""
    try:
        data = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    tool_name = data.get("tool", "unknown")
    params = data.get("params", {})
    result = data.get("result", "")
    state_json = data.get("state")  # Optional JSON state for narration cache

    # Parse state if it's a JSON string
    if isinstance(state_json, str):
        try:
            state_json = json.loads(state_json)
        except (json.JSONDecodeError, TypeError):
            state_json = None

    # Emit a "thinking" event for the reason parameter (skip for narrate —
    # its text param IS the thinking content and already gets displayed).
    reason = params.get("reason")
    reason_event = None
    if reason and tool_name not in ("narrate", "mp_narrate"):
        reason_event = store.append(
            text=f"💭 {reason}",
            event_type="thinking",
            tool_name=tool_name,
            raw_data={"reason": reason},
        )

    text, event_type = narration.narrate(tool_name, params, result, state_data=state_json)
    if text is None:
        if reason_event:
            return web.json_response({"status": "ok", "event_id": reason_event.id})
        return web.json_response({"status": "ok", "suppressed": True})

    # Attach compact state snapshot for the dashboard status bar
    raw = {"params": params}
    if state_json and isinstance(state_json, dict):
        snap = _extract_state_snapshot(state_json)
        if snap:
            raw["state_snapshot"] = snap

    event = store.append(
        text=text,
        event_type=event_type,
        tool_name=tool_name,
        raw_data=raw,
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
                continue

            if event is None:
                await response.write(b"event: clear\ndata: {}\n\n")
                continue

            payload = json.dumps(event.to_dict(), ensure_ascii=False)
            msg = f"id: {event.id}\nevent: message\ndata: {payload}\n\n"
            await response.write(msg.encode("utf-8"))
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
