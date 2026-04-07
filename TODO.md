# STS2 MCP — Performance & Gameplay Improvement TODO

Improvements to reduce MCP round-trips, improve AI decision quality, and increase win rate.
Organized by priority tier. Each item is independently implementable.

---

## Tier 1: High Impact — Do First

### 1.1 Return game state from every action response
**Why**: Eliminates ~50% of `get_game_state()` calls. Currently every action returns `{ status: "ok" }`, forcing the AI to call `get_game_state()` after each play.
**Files**:
- `McpMod.Actions.cs` — Every action handler (combat_play_card, combat_end_turn, use_potion, event_choose_option, etc.) should include the full game state in its response: `{ status: "ok", state: <state_object> }`
- `McpMod.StateBuilder.cs` — Reuse `BuildState()` at the end of each action handler
- `mcp/server.py` — Update tool return descriptions to document that state is included in response

**Impact**: ~4-5 fewer MCP calls per combat turn.

### 1.2 Auto-advance enemy turns in `combat_end_turn`
**Why**: After ending turn, the AI currently must call `get_game_state()` 1-2 times just to poll past the enemy turn. The mod should wait for the enemy turn to resolve and return the next player turn state directly.
**Files**:
- `McpMod.Actions.cs` — In the `end_turn` handler, add a poll/wait loop (e.g., wait up to 5s for `is_play_phase` to become true again) before returning the state
- Consider a configurable timeout with fallback to returning intermediate state

**Impact**: ~1-2 fewer calls per turn. Eliminates the most confusing polling pattern.

### 1.3 Include legal actions in state response
**Why**: The AI wastes calls on illegal moves (wrong target, insufficient energy, wrong state_type), then must retry. Including legal actions eliminates guesswork entirely.
**Files**:
- `McpMod.StateBuilder.cs` — Add a `legal_actions` array to the state object for each state_type:
  - `monster/elite/boss`: List playable cards (with valid targets), usable potions, end_turn
  - `map`: List choosable node indices
  - `rewards`: List claimable reward indices
  - `event`: List available option indices
  - `rest_site`, `shop`, `treasure`, `card_reward`: Same pattern
- `McpMod.Formatting.cs` — Include legal actions in markdown format too

**Impact**: Eliminates failed action calls. Reduces AI reasoning tokens per decision.

### 1.4 Combat damage/block calculator in state
**Why**: The AI does mental math (badly) to calculate damage output, kill thresholds, and block needs. The mod has access to exact game engine values.
**Files**:
- `McpMod.StateBuilder.cs` — Add a `combat_analysis` section to combat states containing:
  - `total_available_damage`: Sum of playable attack card damage per enemy (accounting for Strength, Vulnerable)
  - `total_available_block`: Sum of playable block (accounting for Dexterity, Frail)
  - `can_kill`: Per-enemy boolean — can this enemy be killed this turn?
  - `incoming_damage`: Total damage from all attacking enemies (accounting for Weak, player Block)
  - `net_hp_change`: Estimated HP change if all enemy attacks connect with current block
- `McpMod.Helpers.cs` — Add damage/block calculation helper functions that mirror game engine formulas

**Impact**: Dramatically reduces misplays (wrong target, unnecessary blocking, missed lethal).

---

## Tier 2: Strong Impact — Do Next

### 2.1 Batch card play tool
**Why**: A 5-card combat turn currently needs 5+ MCP calls. A batch tool collapses it to 1.
**Files**:
- `McpMod.Actions.cs` — New endpoint `/batch_play` that accepts an ordered array of actions: `[{type: "potion", slot: 0}, {type: "card", index: 4, target: "X_0"}, {type: "card", index: 2}, {type: "end_turn"}]`. Execute sequentially, re-resolving card indices after each play. Return final state + per-action results.
- `mcp/server.py` — New `combat_batch` tool wrapping the endpoint
- **Edge case**: If any action fails mid-batch, stop and return partial results + error + current state

**Impact**: ~3-4 fewer calls per turn. Biggest speed win for combat.

### 2.2 Character-specific strategy instructions
**Why**: Current instructions are character-agnostic. Each STS2 character has completely different optimal archetypes, key cards, and build paths. This is the single biggest win-rate improvement for deckbuilding.
**Files**:
- `.github/instructions/` — Add per-character instruction files (e.g., `sts2-ironclad.instructions.md`, `sts2-silent.instructions.md`) with:
  - Top 10 cards to pick (by archetype)
  - Cards to always skip
  - Key synergies and combos
  - Boss-specific tips for that character
  - Preferred archetypes ranked
- Alternatively, add to `GUIDE.md` and update dynamically during play (already partially supported by playsts2 skill)

**Impact**: Major win-rate improvement for card selection and deckbuilding decisions.

### 2.3 Embed context-relevant hints in state response
**Why**: The AI re-processes ~4,000+ tokens of static instructions every call. Moving situational advice into the state response reduces prompt bloat and provides better-timed guidance.
**Files**:
- `McpMod.StateBuilder.cs` — Add a `hints: string[]` field to state responses with context-relevant tips:
  - Combat: "All enemies buffing — prioritize damage over block", "You have lethal on JAW_WORM_0"
  - Map: "HP is low (35%), prefer rest sites", "100+ gold — shop is worthwhile"
  - Rewards: "Deck has 20+ cards — consider skipping", "No AOE in deck — prioritize AOE cards"
- Keep hints to 3-5 per state, each under 20 words
- `McpMod.Helpers.cs` — Hint generation logic (rule-based, simple conditionals)

**Impact**: Reduces LLM reasoning time. Provides timely, specific advice vs. generic static instructions.

### 2.4 Batch reward claiming
**Why**: Reward screens require 3-5 sequential calls (claim gold, claim relic, claim potion, pick/skip card, proceed). Most are no-brainers.
**Files**:
- `McpMod.Actions.cs` — New `/claim_all_rewards` endpoint that auto-claims gold, relics, and potions (if slots available), then returns the state (which may be card_reward if a card reward existed)
- `mcp/server.py` — New `rewards_claim_all` tool

**Impact**: ~2-3 fewer calls per reward screen.

---

## Tier 3: Polish — Nice to Have

### 3.1 Enemy move pattern tracker
**Why**: Many enemies follow fixed move cycles. Predicting future intents enables multi-turn planning.
**Files**:
- `McpMod.StateBuilder.cs` — Add `predicted_intents: string[]` (next 2-3 moves) per enemy in combat state, based on known enemy AI patterns
- `McpMod.Helpers.cs` — Enemy pattern database (hardcoded cycle data per enemy type)
- **Note**: Requires reverse-engineering or datamining enemy move patterns. May be fragile across game updates.

### 3.2 Map path scorer
**Why**: AI picks nodes one-at-a-time. A path scorer evaluates full routes to boss.
**Files**:
- `McpMod.StateBuilder.cs` — Add `recommended_paths` to map state with scored routes:
  - Score = f(elite_count, rest_count, current_hp, gold, deck_size)
  - Return top 2-3 paths with node sequences and scores
- Requires map graph traversal logic in the mod

### 3.3 Draw pile visibility / deck stats
**Why**: Knowing draw pile composition changes decisions (play draw cards? end turn early?).
**Files**:
- `McpMod.StateBuilder.cs` — Add to combat state:
  - `draw_pile_count`: int
  - `draw_pile_composition`: card name/type summary (not full list to avoid noise)
  - `discard_pile_count`: int
  - `exhaust_pile_count`: int

### 3.4 Trim and consolidate instruction files
**Why**: ~4,000+ tokens of instructions across 5 files. Much is redundant or overlapping.
**Files**:
- `.github/instructions/sts2-*.instructions.md` — Consolidate into 1-2 files, ~1,500 tokens total
- Remove advice that's now handled by mod-side hints (2.3) or combat analysis (1.4)
- Keep only: API reference, critical rules (index shifting, state polling), high-level strategy principles

### 3.5 SSE streaming for state updates (Advanced)
**Why**: Replace poll-based model with push-based. Mod sends state changes in real-time.
**Files**:
- `McpMod.cs` — Add SSE endpoint alongside existing HTTP endpoints
- `mcp/server.py` — Subscribe to SSE stream, surface state changes as MCP notifications
- **Note**: High effort. Only worthwhile if all other optimizations are done and polling is still the bottleneck.

---

## Implementation Notes

- **Build**: `.\build.ps1 -GameDir "<STS2 install path>"`
- **Test**: Launch STS2, start a run, verify MCP tools work via `uv run --directory mcp python server.py`
- **Tier 1 items are independent** — can be implemented in parallel
- **Tier 2 items depend on Tier 1** — especially 2.3 (hints) benefits from 1.4 (combat analysis) being done first
- **Tier 3 items are independent** and can be done in any order

## Verification Checklist
- [ ] After 1.1: Every action tool response includes full game state JSON
- [ ] After 1.2: `combat_end_turn` returns next player turn state (no extra polling needed)
- [ ] After 1.3: State includes `legal_actions` array; AI never makes an illegal action call
- [ ] After 1.4: Combat state includes `combat_analysis` with accurate damage/block/kill data
- [ ] After 2.1: Can play an entire combat turn with a single `combat_batch` call
- [ ] After 2.4: Can claim all non-card rewards with a single `rewards_claim_all` call
- [ ] Overall: A typical combat turn uses ≤3 MCP calls (was ~10)
- [ ] Overall: Win rate improves (track over 5+ runs before/after)