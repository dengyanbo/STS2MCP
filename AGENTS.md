# STS2 MCP — Agent Guide

## Project Structure

- **C# mod** (`McpMod.*.cs`) — runs inside STS2, exposes HTTP API on `localhost:15526`
- **Python MCP server** (`mcp/server.py`) — bridges HTTP API to MCP tools
- **Skill files** (`.github/instructions/sts2-*.instructions.md`) — auto-loaded gameplay instructions
- **Learnings** (`.github/instructions/sts2-learnings.instructions.md`) — persistent AI memory, updated after each run
- **Run log** (`docs/run-log.md`) — detailed per-run history (not auto-loaded)

## Skill File Architecture

| File | Scope |
|---|---|
| `sts2-play-game` | Game loop dispatcher, critical rules, post-game reflection protocol |
| `sts2-combat-strategy` | All combat: decision framework, sequencing, damage calc, boss/elite tactics |
| `sts2-map-events-strategy` | Non-combat: map pathing, events, rest sites, shops, rewards, economy |
| `sts2-deck-building` | Card evaluation, archetypes, upgrade/remove priorities |
| `sts2-learnings` | Accumulated match insights (AI persistent memory) |

## Learning System

After every game (win or loss), the AI executes the **Post-Game Reflection Protocol** (defined in `sts2-play-game`):
1. Analyze the run → 2. Log to `docs/run-log.md` → 3. Distill insights into `sts2-learnings` → 4. Report to user.
The learnings file has a **15-entry cap per section** to prevent context bloat.
