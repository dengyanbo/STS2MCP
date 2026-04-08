# STS2 MCP — Agent Guide

## Project Structure

| Component | Path | Purpose |
|---|---|---|
| C# Mod | `McpMod.*.cs` | In-game HTTP API on `localhost:15526` |
| MCP Server | `mcp/server.py` | Bridges HTTP API → 70+ MCP tools |
| Displayer | `displayer/` | Live narration dashboard on `localhost:15580` |
| Skill Files | `.github/instructions/sts2-*.instructions.md` | Auto-loaded gameplay strategy |
| Learnings | `.github/instructions/sts2-learnings.instructions.md` | Persistent AI memory, updated after each run |
| Run Log | `docs/run-log.md` | Detailed per-run history (not auto-loaded) |
| API Docs | `docs/raw-full.md`, `docs/raw-simplified.md` | HTTP API reference |

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

## Key Mod Features (for AI agents)

- **State in action responses** — Every action returns the updated game state, eliminating redundant `get_game_state()` calls
- **Auto-advance enemy turns** — `combat_end_turn` polls until the next player turn or combat end
- **Legal actions** — `legal_actions` array in combat state lists all playable cards with valid targets
- **Combat analysis** — `combat_analysis` section with damage estimation, unblocked damage, and HP projections
- **Contextual hints** — `hints` array with situational advice (lethal warnings, kill opportunities, etc.)
- **Batch operations** — `combat_batch` for multiple actions per call; `rewards_claim_all` for non-card rewards
- **Decision narration** — All tools accept an optional `reason` parameter. Provide a brief explanation (1-2 sentences) of your strategic reasoning; it is displayed in the live narration dashboard for viewers. Example: `map_choose_node(node_index=1, reason="HP充足，挑战精英获取遗物")`
- **Live narration tool** — Call `narrate(text="...")` BEFORE every significant decision to share your detailed strategic thinking with viewers. Write 2-5 sentences in natural Chinese analyzing the situation, your options, and why you're choosing a particular path. This is the primary way viewers understand your gameplay.
