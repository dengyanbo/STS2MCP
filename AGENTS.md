# STS2 MCP — Agent Guide

## Project Structure

| Component | Path | Purpose |
|---|---|---|
| C# Mod | `McpMod.*.cs` | In-game HTTP API on `localhost:15526` |
| MCP Server | `mcp/server.py` | Bridges HTTP API → 70+ MCP tools |
| Displayer | `displayer/` | Live narration dashboard on `localhost:15580` |
| Skill Files | `.github/instructions/sts2-*.instructions.md` | Auto-loaded gameplay strategy |
| Learnings | `.github/instructions/sts2-learnings.instructions.md` | Hot memory: confirmed patterns + working hypotheses |
| Reflection | `docs/reflection-protocol.md` | Post-game protocol (warm, read at game end) |
| Bestiary | `docs/bestiary.md` | Warm memory: enemy profiles and counters |
| Card Atlas | `docs/card-atlas.md` | Warm memory: per-character card evaluations |
| Relic Atlas | `docs/relic-atlas.md` | Warm memory: relic ratings and synergies |
| Run Log | `docs/run-log.md` | Cold memory: detailed per-run history |
| Run Stats | `docs/run-stats.md` | Cold memory: cross-run statistics dashboard |
| API Docs | `docs/raw-full.md`, `docs/raw-simplified.md` | HTTP API reference |

## Skill File Architecture

| File | Scope |
|---|---|
| `sts2-play-game` | Game loop dispatcher, critical rules, 3-tier memory architecture, post-game reflection protocol |
| `sts2-combat-strategy` | All combat: decision framework, sequencing, damage calc, boss/elite tactics |
| `sts2-map-events-strategy` | Non-combat: map pathing, events, rest sites, shops, rewards, economy |
| `sts2-deck-building` | Card evaluation, archetypes, upgrade/remove priorities |
| `sts2-learnings` | Hot memory: confirmed patterns, working hypotheses, enemy quick-ref, synergy index |

## 3-Tier Memory Architecture

| Tier | Files | Loading | Update Trigger |
|---|---|---|---|
| **Hot** (auto-loaded) | `sts2-learnings.instructions.md` | Every turn | Post-game reflection |
| **Warm** (on-demand) | `docs/bestiary.md`, `docs/card-atlas.md`, `docs/relic-atlas.md` | `grep_search` when facing enemy / card reward / relic choice | Post-game reflection |
| **Cold** (write-only) | `docs/run-log.md`, `docs/run-stats.md`, EventStore SQLite | Post-game only | Post-game reflection |

### Multi-Character Support
- Learnings entries tagged `[通用]` (universal) or `[角色名]` (character-specific)
- `card-atlas.md` organized by character sections (card pools differ)
- `bestiary.md` organized by enemy with per-character counter strategies
- `relic-atlas.md` entries tagged `[通用]` or `[角色名]`
- `run-stats.md` has overall + per-character stat tables

## Learning System

After every game (win or loss), the AI reads and executes `docs/reflection-protocol.md`:
1. Analyze the run → 1.5. Read in-run micro-journal → 2. Log to `docs/run-log.md` with decision audit → 3. Distill insights into `sts2-learnings` with confidence tracking → 3.5. Update warm memory knowledge bases (bestiary, card-atlas, relic-atlas, run-stats) → 4. Report to user → 5. Strategy review (every 5 runs).

The reflection protocol is NOT auto-loaded — it's read on-demand at game end to save context budget during gameplay.

### Confidence System
- **Working Hypothesis** `[N/M confirmed]`: 1-2 runs support. Soft guideline.
- **Confirmed Pattern**: 3+ runs support. Hard rule.
- **Strategy Promotion**: 5+ runs confirmed + high impact → written into strategy files (combat/deck/map) and removed from learnings.
- **Contradiction**: 2+ runs contradict → remove or revise hypothesis.

### In-Run Micro-Journal
During gameplay, the AI maintains `/memories/session/run-journal.md` with brief decision logs at critical moments (boss/elite encounters, card rewards with 2+ good options, trade-off events, unexpected HP loss).

The learnings file has a **15-entry cap per section** to prevent context bloat.

## Key Mod Features (for AI agents)

- **State in action responses** — Every action returns the updated game state, eliminating redundant `get_game_state()` calls
- **Auto-advance enemy turns** — `combat_end_turn` polls until the next player turn or combat end
- **Legal actions** — `legal_actions` array in combat state lists all playable cards with valid targets
- **Combat analysis** — `combat_analysis` section with damage estimation, unblocked damage, and HP projections
- **Contextual hints** — `hints` array with situational advice (lethal warnings, kill opportunities, etc.)
- **Batch operations** — `combat_batch` for multiple actions per call; `rewards_claim_all` for non-card rewards
- **Decision narration** — All tools accept an optional `reason` parameter. Provide a brief explanation (1-2 sentences) of your strategic reasoning; it is displayed in the live narration dashboard for viewers. Example: `map_choose_node(node_index=1, reason="HP充足，挑战精英获取遗物")`
