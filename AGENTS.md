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
- **Card effect tags** — Each hand card includes an `effects` array (e.g. `applies_vulnerable`, `draws_cards`, `gains_strength`, `is_power`, `is_x_cost`) for structured decision-making
- **Suggested play order** — `suggested_play_order` in combat state ranks playable cards by strategic priority with energy simulation; follow this for optimal sequencing (debuffs before attacks, draw cards first, X-cost last)
- **Batch auto-pause** — `combat_batch` auto-detects cards with `draws_cards`/`has_randomness` effects and pauses after them, returning updated state + remaining actions for re-planning
- **Batch order validation** — `combat_batch` pre-validates card order and emits `⚠️ SUBOPTIMAL ORDER` warnings when debuff cards are placed after attacks
- **Live narration tool** — Call `narrate(text="...")` BEFORE every significant decision to share your detailed strategic thinking with viewers. Write 2-5 sentences in natural Chinese analyzing the situation, your options, and why you're choosing a particular path. This is the primary way viewers understand your gameplay.
- **Auto mistake detection** — When a new combat round starts (round 2+), the MCP server auto-injects a `[TURN_REVIEW_PENDING]` block into tool responses containing the previous turn's summary. The main agent fire-and-forgets a task sub-agent to analyze it; the sub-agent posts any mistakes directly to the displayer via HTTP. The main agent never blocks on mistake analysis.
- **Strategic combat review** — When combat ends (state transitions from combat to rewards/map/event), the MCP server auto-injects a `[COMBAT_REVIEW_PENDING]` block with a full-combat strategic summary (HP timeline, per-turn actions, damage patterns). A fire-and-forget sub-agent analyzes cross-turn strategic mistakes: HP efficiency, kill priority, Power card timing, debuff maintenance, energy waste, defensive over/under-commitment, potion timing, and missed kill windows.
