# STS2 MCP — Performance & Gameplay Improvement TODO

## ✅ Implemented

### 1.1 Return game state from every action response
Every action handler now includes the full game state in its response.
Eliminates ~50% of `get_game_state()` calls.

### 1.2 Auto-advance enemy turns in `combat_end_turn`
Python MCP server polls after end_turn until next player turn or combat end.
Eliminates 1-2 polling calls per turn.

### 1.3 Include legal actions in combat state
`legal_actions` array in battle state lists all playable cards (with valid targets),
usable potions, and end_turn. Eliminates failed action calls.

### 1.4 Combat damage/block calculator
`combat_analysis` section with incoming damage estimation, unblocked damage,
HP after attack, and enemies_attacking flag. Reduces AI miscalculations.

### 2.1 Batch card play tool (`combat_batch`)
New MCP tool that executes multiple combat actions (play_card, use_potion,
end_turn) in a single call. Includes auto-advance after end_turn.

### 2.3 Context-relevant hints in state
`hints` array in combat state with situational advice:
- "No enemies attacking — go all-out offense"
- "LETHAL DANGER" warnings
- Low-HP enemy kill opportunities
- Enemy buffing alerts

### 2.4 Batch reward claiming (`rewards_claim_all`)
New MCP tool that claims all non-card rewards (gold, relics, potions)
in one call. Stops at card rewards for manual selection.

---

## 📋 Remaining (Not Yet Implemented)

### 2.2 Character-specific strategy instructions
Add per-character instruction files with top cards, synergies, archetypes.
**Files**: `.github/instructions/sts2-<character>.instructions.md`
**Impact**: Major win-rate improvement for deckbuilding.

### 3.1 Enemy move pattern tracker
Track enemy move cycles and predict next 2-3 intents.
**Files**: `McpMod.StateBuilder.cs`, `McpMod.Helpers.cs`
**Impact**: Enables multi-turn planning.

### 3.2 Map path scorer
Evaluate full paths to boss and rank them by score.
**Files**: `McpMod.StateBuilder.cs`
**Impact**: Better map pathing decisions.

### 3.3 Draw pile visibility / deck stats
Already partially implemented (draw/discard/exhaust pile contents are in state).
Could add probability analysis for next draw.

### 3.4 Trim and consolidate instruction files
Consolidate ~4,000+ tokens across 5 instruction files to ~1,500 tokens.
Remove advice now handled by mod-side hints and combat analysis.

### 3.5 SSE streaming for state updates (Advanced)
Replace poll-based model with push-based Server-Sent Events.
**Impact**: Eliminates all polling. High effort.
