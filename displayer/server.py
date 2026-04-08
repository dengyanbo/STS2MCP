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
import re
import sys
from pathlib import Path

# Ensure sibling modules are importable regardless of cwd
_pkg_dir = Path(__file__).resolve().parent
if str(_pkg_dir) not in sys.path:
    sys.path.insert(0, str(_pkg_dir))

from aiohttp import web  # noqa: E402

from event_store import EventStore  # noqa: E402
from narration import NarrationEngine  # noqa: E402
from turn_tracker import CombatTurnTracker  # noqa: E402

store = EventStore()
narration = NarrationEngine()
turn_tracker = CombatTurnTracker()

# Auto-detect mistakes mentioned in narrate() but not reported via report_mistake()
_MISTAKE_RE = re.compile(
    r"失误|犯错|漏打|打错|忘了先|上回合.*错|错误.*导致|白白损失|浪费了.*能量"
)


def _extract_mistake_from_narration(text: str) -> str | None:
    """If a narrate() text contains a self-reported mistake, extract it."""
    if not _MISTAKE_RE.search(text):
        return None
    # Prefer lines explicitly marked with ❌
    lines = text.split("\n")
    mistake_lines = [l.strip() for l in lines if l.strip().startswith("❌")]
    if mistake_lines:
        return "\n".join(mistake_lines)
    # Fallback: extract the paragraph containing the keyword
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if _MISTAKE_RE.search(para):
            return para.strip()
    return None


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
        for k in ("hp", "max_hp", "energy", "gold", "block",
                   "draw_pile_count", "discard_pile_count", "exhaust_pile_count"):
            if k in player:
                p[k] = player[k]
        if "deck" in player and isinstance(player["deck"], list):
            p["deck"] = player["deck"]
        if "hand" in player and isinstance(player["hand"], list):
            p["hand"] = player["hand"]
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

    # Feed the turn tracker (before narration, so it captures raw state)
    turn_tracker.process_event(tool_name, params, result, state_json)

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

    # Auto-detect mistakes in narrate() that weren't reported via report_mistake()
    if tool_name in ("narrate", "mp_narrate") and text:
        mistake_text = _extract_mistake_from_narration(params.get("text", ""))
        if mistake_text:
            store.append(
                text=f"❌ [自动检测] {mistake_text.lstrip('❌').strip()}",
                event_type="mistake",
                tool_name="report_mistake",
                raw_data={"auto_detected": True, "params": params},
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
    turn_tracker.clear()
    return web.json_response(
        {"status": "ok"},
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_last_turn(request: web.Request) -> web.Response:
    """Return the last completed combat turn summary as text."""
    summary = turn_tracker.format_last_turn_summary()
    if summary is None:
        return web.json_response(
            {"status": "ok", "summary": None, "message": "No completed turn data"},
            headers={"Access-Control-Allow-Origin": "*"},
        )
    return web.json_response(
        {"status": "ok", "summary": summary, "turn": turn_tracker.get_last_turn()["turn"]},
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def handle_combat_log(request: web.Request) -> web.Response:
    """Return all completed combat turns as summaries."""
    turns = turn_tracker.get_all_turns()
    summaries = []
    for t in turns:
        s = turn_tracker.format_turn_summary(t)
        if s:
            summaries.append({"turn": t["turn"], "summary": s})
    return web.json_response(
        {
            "status": "ok",
            "in_combat": turn_tracker.in_combat,
            "current_round": turn_tracker.current_round,
            "turns": summaries,
        },
        headers={"Access-Control-Allow-Origin": "*"},
    )


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/events", handle_post_event)
    app.router.add_get("/events/stream", handle_events_stream)
    app.router.add_get("/events/history", handle_events_history)
    app.router.add_post("/api/clear", handle_clear)
    app.router.add_get("/api/last-turn", handle_last_turn)
    app.router.add_get("/api/combat-log", handle_combat_log)
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
