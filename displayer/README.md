# STS2 AI Thinking Dashboard

A browser-based dashboard that shows a live, human-readable narration of the AI's gameplay decisions in Slay the Spire 2.

## How It Works

```
VS Code Chat ←stdio→ MCP Server (mcp/server.py)
                         ↓ HTTP POST after each tool call
                    Displayer Server (displayer/server.py)
                         ↓ SSE stream
                    Browser Dashboard (localhost:15580)
```

1. The MCP server intercepts every tool call (passively — no AI behavior changes)
2. It sends the tool name, params, and result to the displayer server
3. The displayer narrates the action in human-readable text using templates
4. The dashboard renders narration with a ChatGPT-like typing animation
5. History is persisted in SQLite — survives page refresh

## Quick Start

### 1. Start the Dashboard Server

```bash
uv run python displayer/server.py
```

The dashboard will be available at **http://localhost:15580**.

### 2. Start the MCP Server (as usual)

The MCP server automatically sends events to `http://localhost:15580` by default.
No extra flags needed.

### 3. Open the Dashboard

Open http://localhost:15580 in your browser. Events will stream in as the AI plays.

## Configuration

### Dashboard Server

```bash
uv run python displayer/server.py --port 15580 --host 0.0.0.0
```

### MCP Server Integration

```bash
# Custom displayer URL
uv run --directory mcp python server.py --displayer-url http://localhost:15580

# Disable displayer notifications
uv run --directory mcp python server.py --no-displayer
```

## Features

- 🎮 **Live narration** — Human-readable thinking text for every AI action
- ⚡ **Typing animation** — ChatGPT-like character-by-character reveal
- 📜 **Persistent history** — SQLite storage, survives page refresh
- 🔌 **Auto-reconnect** — SSE reconnects automatically on disconnect
- 🎨 **Dark theme** — Game-appropriate UI
- 🔇 **Passive** — No changes to AI behavior; zero impact on gameplay

## Files

| File | Purpose |
|---|---|
| `server.py` | aiohttp HTTP + SSE server |
| `event_store.py` | Thread-safe event storage with SQLite |
| `narration.py` | Template-based tool → human text translator |
| `static/dashboard.html` | Browser UI (single file, vanilla JS) |
| `data/events.db` | SQLite database (auto-created, gitignored) |
