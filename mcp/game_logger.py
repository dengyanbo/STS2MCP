"""Detailed gameplay logger for STS2 MCP.

Logs every tool call, game state transition, combat turn, and decision
to JSONL files organized by game run. Designed for post-game analysis
and cross-run learning.

Log structure:
  logs/
    run_YYYY-MM-DD_HH-MM-SS/
      events.jsonl      — Every event, one JSON per line (machine-readable)
      combat_log.md     — Turn-by-turn combat breakdown (human-readable)
      summary.md        — Run summary generated at run end
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("sts2.game_logger")

# Tools that don't change game state — skip extra JSON fetch for these
_READ_ONLY_TOOLS = frozenset({"narrate", "get_game_state", "mp_get_game_state"})

# Tools that represent combat actions
_COMBAT_ACTION_TOOLS = frozenset({
    "combat_play_card", "combat_end_turn", "combat_batch",
    "combat_select_card", "combat_confirm_selection",
    "use_potion", "discard_potion",
    "mp_combat_play_card", "mp_combat_end_turn",
    "mp_combat_select_card", "mp_combat_confirm_selection",
    "mp_use_potion", "mp_discard_potion",
})


class GameLogger:
    """Logs every MCP tool call to JSONL files organized by game run."""

    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._run_dir: Path | None = None
        self._events_file = None
        self._seq = 0
        self._run_active = False

        # State tracking
        self._last_state_type: str | None = None
        self._last_floor: int | None = None
        self._run_character: str = "Unknown"
        self._run_start_time: float | None = None

        # Combat tracking for human-readable combat_log.md
        self._combat_entries: list[dict] = []
        self._combat_count = 0
        self._combat_turn_logged: int = -1
        self._combat_state_type: str = ""
        self._combat_floor: int | None = None

        # Run statistics
        self._stats = _new_stats()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_tool_call(
        self,
        tool_name: str,
        params: dict,
        result: str,
        state_json: dict | None = None,
    ) -> None:
        """Log a single tool call with optional post-action game state.

        Called by the instrumented MCP tool wrapper after every tool
        execution.  ``state_json`` should be the JSON game state fetched
        *after* the action for action tools, or the parsed result for
        ``get_game_state(format="json")``.
        """
        try:
            self._log_tool_call_inner(tool_name, params, result, state_json)
        except Exception:
            logger.exception("GameLogger error (non-fatal)")

    def force_end_run(self, reason: str = "forced") -> None:
        """Manually end the current run (e.g. on server shutdown)."""
        if self._run_active:
            self._end_run(reason)

    # ------------------------------------------------------------------
    # Internal — run lifecycle
    # ------------------------------------------------------------------

    def _start_run(self, character: str = "Unknown") -> None:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._run_dir = self.log_dir / f"run_{ts}"
        self._run_dir.mkdir(parents=True, exist_ok=True)

        if self._events_file:
            self._events_file.close()
        self._events_file = open(
            self._run_dir / "events.jsonl", "a", encoding="utf-8"
        )
        self._seq = 0
        self._combat_count = 0
        self._combat_entries.clear()
        self._combat_turn_logged = -1
        self._run_character = character
        self._run_start_time = time.time()
        self._run_active = True
        self._stats = _new_stats()

        self._write_event({"event": "run_start", "character": character})
        logger.info("Run started: %s (%s)", self._run_dir.name, character)

    def _end_run(self, reason: str = "unknown") -> None:
        if not self._run_active:
            return

        # Flush any pending combat
        self._finalize_combat()

        elapsed = time.time() - (self._run_start_time or time.time())
        self._write_event({
            "event": "run_end",
            "reason": reason,
            "floor": self._last_floor,
            "elapsed_seconds": round(elapsed, 1),
            "stats": self._stats,
        })

        # Generate summary
        self._write_summary(reason)

        if self._events_file:
            self._events_file.close()
            self._events_file = None
        self._run_active = False
        self._run_dir = None
        logger.info("Run ended: %s (floor %s)", reason, self._last_floor)

    # ------------------------------------------------------------------
    # Internal — event writing
    # ------------------------------------------------------------------

    def _write_event(self, data: dict) -> None:
        if not self._events_file:
            return
        self._seq += 1
        data["seq"] = self._seq
        data["ts"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        line = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self._events_file.write(line + "\n")
        self._events_file.flush()

    # ------------------------------------------------------------------
    # Internal — main log logic
    # ------------------------------------------------------------------

    def _log_tool_call_inner(
        self, tool_name: str, params: dict, result: str, state_json: dict | None
    ) -> None:
        # ---- State transition detection ----
        if state_json:
            self._detect_transitions(state_json)

        # Auto-start run if we see game activity without one
        if not self._run_active:
            if state_json and state_json.get("state_type") not in ("menu", None, ""):
                char = _extract_character(state_json)
                self._start_run(char)
            else:
                return  # Nothing to log pre-game

        # ---- Build event entry ----
        entry: dict = {
            "event": "narration" if tool_name == "narrate" else "tool_call",
            "tool": tool_name,
            "params": _safe_params(params),
        }

        # Result status
        if result.startswith("Error:"):
            entry["status"] = "error"
            entry["error"] = result[:500]
        elif "✗" in result[:10]:
            entry["status"] = "error"
            entry["error"] = result[:500]
        else:
            entry["status"] = "ok"

        # Narration text
        if tool_name == "narrate":
            entry["narration_text"] = params.get("text", "")

        # Context snapshot from state
        if state_json:
            entry["ctx"] = self._extract_context(state_json)

        # Result text (truncated for storage efficiency)
        if tool_name in ("get_game_state", "mp_get_game_state"):
            if params.get("format") == "json" and state_json:
                entry["state_snapshot"] = state_json
            else:
                entry["result_text"] = result[:3000]
        else:
            # For actions, store first line (status) + truncated text
            lines = result.split("\n", 1)
            entry["result_summary"] = lines[0][:200]
            if len(result) > 200:
                entry["result_text"] = result[:1500]

        self._write_event(entry)

        # ---- Update stats ----
        self._update_stats(tool_name, params, entry.get("status", "ok"), state_json)

        # ---- Combat tracking ----
        if state_json:
            st = state_json.get("state_type", "")
            if st in ("monster", "elite", "boss"):
                if tool_name in _COMBAT_ACTION_TOOLS or tool_name == "get_game_state":
                    self._track_combat(tool_name, params, state_json)
            elif self._combat_entries:
                self._finalize_combat()

    # ------------------------------------------------------------------
    # Internal — state transition detection
    # ------------------------------------------------------------------

    def _detect_transitions(self, state: dict) -> None:
        st = state.get("state_type", "")
        floor = state.get("floor") or state.get("current_floor")

        # Menu → game = new run
        if self._last_state_type == "menu" and st not in ("menu", "", None):
            if self._run_active:
                self._end_run("new_run_detected")
            self._start_run(_extract_character(state))

        # Game → menu = run ended
        if st == "menu" and self._run_active:
            self._end_run("returned_to_menu")

        # State type change within a run → log transition
        if (
            self._run_active
            and self._last_state_type
            and st != self._last_state_type
            and st not in ("", None)
        ):
            self._write_event({
                "event": "state_transition",
                "from": self._last_state_type,
                "to": st,
                "floor": floor,
            })

        self._last_state_type = st
        if floor is not None:
            self._last_floor = floor

    # ------------------------------------------------------------------
    # Internal — context extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_context(state: dict) -> dict:
        ctx: dict = {"state_type": state.get("state_type", "")}

        floor = state.get("floor") or state.get("current_floor")
        if floor is not None:
            ctx["floor"] = floor

        player = state.get("player", {})
        if player:
            for k in ("hp", "max_hp", "gold"):
                v = player.get(k)
                if v is not None:
                    ctx[k] = v

        battle = state.get("battle", {})
        if battle:
            for k in ("turn", "energy", "block"):
                v = battle.get(k)
                if v is not None:
                    ctx[k] = v
            hand = battle.get("hand", [])
            ctx["hand_size"] = len(hand)
            ctx["hand"] = [c.get("name", "?") for c in hand]

            enemies = battle.get("enemies", [])
            ctx["enemies"] = [
                {
                    "name": e.get("name", "?"),
                    "id": e.get("entity_id", ""),
                    "hp": e.get("hp"),
                    "max_hp": e.get("max_hp"),
                    "block": e.get("block", 0),
                    "intent": e.get("intent", {}).get("action", "?"),
                    "intent_value": e.get("intent", {}).get("value"),
                }
                for e in enemies
            ]

        # Rewards
        rewards = state.get("rewards", {})
        if rewards:
            items = rewards.get("items", [])
            ctx["reward_count"] = len(items)

        # Map
        next_opts = state.get("next_options", [])
        if next_opts:
            ctx["map_options"] = len(next_opts)

        return ctx

    # ------------------------------------------------------------------
    # Internal — combat tracking → combat_log.md
    # ------------------------------------------------------------------

    def _track_combat(
        self, tool_name: str, params: dict, state: dict
    ) -> None:
        battle = state.get("battle", {})
        turn = battle.get("turn", 0)
        st = state.get("state_type", "")

        if not self._combat_entries:
            self._combat_floor = self._last_floor
            self._combat_state_type = st

        self._combat_entries.append({
            "tool": tool_name,
            "params": params,
            "turn": turn,
            "energy": battle.get("energy"),
            "block": battle.get("block", 0),
            "hand": [c.get("name", "?") for c in battle.get("hand", [])],
            "enemies": [
                {
                    "name": e.get("name", "?"),
                    "id": e.get("entity_id", ""),
                    "hp": e.get("hp"),
                    "max_hp": e.get("max_hp"),
                    "block": e.get("block", 0),
                    "intent": e.get("intent", {}).get("action", "?"),
                    "intent_value": e.get("intent", {}).get("value"),
                    "powers": [
                        {"name": p.get("name", "?"), "amount": p.get("amount", 0)}
                        for p in e.get("powers", [])
                    ],
                }
                for e in battle.get("enemies", [])
            ],
            "player_hp": state.get("player", {}).get("hp"),
            "player_max_hp": state.get("player", {}).get("max_hp"),
            "player_powers": [
                {"name": p.get("name", "?"), "amount": p.get("amount", 0)}
                for p in battle.get("powers", [])
            ],
        })

    def _finalize_combat(self) -> None:
        if not self._combat_entries or not self._run_dir:
            return

        self._combat_count += 1
        combat_file = self._run_dir / "combat_log.md"

        kind = self._combat_state_type or "monster"
        kind_label = {"monster": "普通战斗", "elite": "精英战斗", "boss": "Boss战"}.get(
            kind, kind
        )

        with open(combat_file, "a", encoding="utf-8") as f:
            f.write(
                f"\n---\n\n## 战斗 #{self._combat_count}"
                f" — {kind_label} (层 {self._combat_floor})\n\n"
            )

            # Write enemy roster from first entry
            first = self._combat_entries[0]
            for e in first.get("enemies", []):
                f.write(
                    f"- **{e['name']}** ({e['id']}): "
                    f"{e['hp']}/{e['max_hp']}HP\n"
                )
            f.write(
                f"- 玩家: {first.get('player_hp')}/{first.get('player_max_hp')}HP\n\n"
            )

            current_turn = -1
            for entry in self._combat_entries:
                turn = entry.get("turn", 0)
                tool = entry["tool"]

                # Skip pure state reads for combat log (they don't add info)
                if tool in ("get_game_state", "mp_get_game_state"):
                    continue

                if turn != current_turn:
                    current_turn = turn
                    f.write(f"\n### 回合 {turn}\n\n")
                    hand = entry.get("hand", [])
                    energy = entry.get("energy")
                    block = entry.get("block", 0)
                    f.write(f"- 手牌 ({len(hand)}): {', '.join(hand)}\n")
                    f.write(f"- 能量: {energy} | 格挡: {block}\n")
                    for e in entry.get("enemies", []):
                        powers_str = ""
                        if e.get("powers"):
                            pw = [
                                f"{p['name']}×{p['amount']}"
                                for p in e["powers"]
                                if p["amount"] != 0
                            ]
                            if pw:
                                powers_str = f" [{', '.join(pw)}]"
                        intent_str = e["intent"]
                        if e.get("intent_value"):
                            intent_str += f" {e['intent_value']}"
                        blk = f", {e['block']}挡" if e.get("block") else ""
                        f.write(
                            f"- {e['name']}: {e['hp']}HP{blk}"
                            f" → {intent_str}{powers_str}\n"
                        )
                    f.write(f"- 玩家: {entry.get('player_hp')}HP\n")

                    # Player powers
                    ppow = entry.get("player_powers", [])
                    if ppow:
                        pw_str = ", ".join(
                            f"{p['name']}×{p['amount']}"
                            for p in ppow
                            if p["amount"] != 0
                        )
                        if pw_str:
                            f.write(f"- 玩家增益: {pw_str}\n")
                    f.write("\n")

                # Write action
                params = entry["params"]
                if tool == "combat_play_card":
                    idx = params.get("card_index")
                    target = params.get("target", "")
                    # Try to resolve card name from hand
                    hand = entry.get("hand", [])
                    card_name = hand[idx] if idx is not None and idx < len(hand) else f"#{idx}"
                    target_str = f" → {target}" if target else ""
                    f.write(f"  ▸ 打出 **{card_name}**{target_str}\n")

                elif tool == "combat_end_turn":
                    f.write("  ▸ **结束回合**\n")

                elif tool == "combat_batch":
                    actions = params.get("actions", [])
                    for a in actions:
                        atype = a.get("type", "?")
                        if atype == "play_card":
                            ci = a.get("card_index")
                            t = a.get("target", "")
                            hand = entry.get("hand", [])
                            cn = hand[ci] if ci is not None and ci < len(hand) else f"#{ci}"
                            ts = f" → {t}" if t else ""
                            f.write(f"  ▸ 打出 **{cn}**{ts}\n")
                        elif atype == "use_potion":
                            s = a.get("slot")
                            t = a.get("target", "")
                            ts = f" → {t}" if t else ""
                            f.write(f"  ▸ 使用药水 槽#{s}{ts}\n")
                        elif atype == "end_turn":
                            f.write("  ▸ **结束回合**\n")

                elif tool in ("use_potion", "mp_use_potion"):
                    slot = params.get("slot")
                    target = params.get("target", "")
                    target_str = f" → {target}" if target else ""
                    f.write(f"  ▸ 使用药水 槽#{slot}{target_str}\n")

                elif tool in ("combat_select_card", "mp_combat_select_card"):
                    idx = params.get("card_index")
                    f.write(f"  ▸ 选择卡牌 #{idx}\n")

                elif tool in ("combat_confirm_selection", "mp_combat_confirm_selection"):
                    f.write("  ▸ 确认选择\n")

            # Write combat result
            last = self._combat_entries[-1]
            f.write("\n**战斗结束**\n")
            f.write(f"- 玩家HP: {last.get('player_hp')}/{last.get('player_max_hp')}\n")
            for e in last.get("enemies", []):
                f.write(f"- {e['name']}: {e['hp']}HP\n")
            f.write("\n")

        # Also log combat summary to JSONL
        first = self._combat_entries[0]
        last = self._combat_entries[-1]
        self._write_event({
            "event": "combat_end",
            "combat_number": self._combat_count,
            "combat_type": self._combat_state_type,
            "floor": self._combat_floor,
            "turns": last.get("turn", 0),
            "actions": len([
                e for e in self._combat_entries
                if e["tool"] not in ("get_game_state", "mp_get_game_state")
            ]),
            "hp_start": first.get("player_hp"),
            "hp_end": last.get("player_hp"),
            "enemies_start": [
                {"name": e["name"], "hp": e["hp"]} for e in first.get("enemies", [])
            ],
            "enemies_end": [
                {"name": e["name"], "hp": e["hp"]} for e in last.get("enemies", [])
            ],
        })

        # Update stats
        self._stats["combats_fought"] += 1
        hp_start = first.get("player_hp") or 0
        hp_end = last.get("player_hp") or 0
        self._stats["total_damage_taken"] += max(0, hp_start - hp_end)
        self._stats["total_turns"] += last.get("turn", 0)

        self._combat_entries.clear()
        self._combat_turn_logged = -1

    # ------------------------------------------------------------------
    # Internal — run summary
    # ------------------------------------------------------------------

    def _write_summary(self, end_reason: str) -> None:
        if not self._run_dir:
            return

        elapsed = time.time() - (self._run_start_time or time.time())
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)

        summary_file = self._run_dir / "summary.md"
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(f"# Run Summary\n\n")
            f.write(f"- **角色**: {self._run_character}\n")
            f.write(f"- **结果**: {end_reason}\n")
            f.write(f"- **到达层数**: {self._last_floor}\n")
            f.write(f"- **用时**: {mins}分{secs}秒\n")
            f.write(f"- **事件总数**: {self._seq}\n\n")

            f.write("## 统计\n\n")
            f.write(f"| 指标 | 值 |\n|---|---|\n")
            f.write(f"| 战斗次数 | {self._stats['combats_fought']} |\n")
            f.write(f"| 总回合数 | {self._stats['total_turns']} |\n")
            f.write(f"| 总受伤量 | {self._stats['total_damage_taken']} |\n")
            f.write(f"| 打牌次数 | {self._stats['cards_played']} |\n")
            f.write(f"| 药水使用 | {self._stats['potions_used']} |\n")
            f.write(f"| 获得卡牌 | {self._stats['cards_picked']} |\n")
            f.write(f"| 跳过卡牌 | {self._stats['cards_skipped']} |\n")
            f.write(f"| 商店购买 | {self._stats['shop_purchases']} |\n")
            f.write(f"| 事件选择 | {self._stats['events_chosen']} |\n")
            f.write(f"| 解说次数 | {self._stats['narrations']} |\n")
            f.write(f"| 错误操作 | {self._stats['errors']} |\n")

    # ------------------------------------------------------------------
    # Internal — stats tracking
    # ------------------------------------------------------------------

    def _update_stats(
        self, tool_name: str, params: dict, status: str, state: dict | None
    ) -> None:
        if status == "error":
            self._stats["errors"] += 1

        if tool_name in ("combat_play_card", "mp_combat_play_card"):
            self._stats["cards_played"] += 1
        elif tool_name == "combat_batch":
            for a in params.get("actions", []):
                if a.get("type") == "play_card":
                    self._stats["cards_played"] += 1
                elif a.get("type") == "use_potion":
                    self._stats["potions_used"] += 1
        elif tool_name in ("use_potion", "mp_use_potion"):
            self._stats["potions_used"] += 1
        elif tool_name in ("rewards_pick_card", "mp_rewards_pick_card"):
            self._stats["cards_picked"] += 1
        elif tool_name in ("rewards_skip_card", "mp_rewards_skip_card"):
            self._stats["cards_skipped"] += 1
        elif tool_name in ("shop_purchase", "mp_shop_purchase"):
            self._stats["shop_purchases"] += 1
        elif tool_name in ("event_choose_option", "mp_event_choose_option"):
            self._stats["events_chosen"] += 1
        elif tool_name == "narrate":
            self._stats["narrations"] += 1


# ======================================================================
# Helpers
# ======================================================================


def _new_stats() -> dict:
    return {
        "combats_fought": 0,
        "total_turns": 0,
        "total_damage_taken": 0,
        "cards_played": 0,
        "potions_used": 0,
        "cards_picked": 0,
        "cards_skipped": 0,
        "shop_purchases": 0,
        "events_chosen": 0,
        "narrations": 0,
        "errors": 0,
    }


def _extract_character(state: dict) -> str:
    """Try to extract character name from game state."""
    # Direct field
    char = state.get("character") or state.get("player_class")
    if char:
        return char
    # From player sub-object
    player = state.get("player", {})
    char = player.get("character") or player.get("class") or player.get("name")
    if char:
        return char
    return "Unknown"


def _safe_params(params: dict) -> dict:
    """Return a copy of params safe for JSON serialization."""
    safe = {}
    for k, v in params.items():
        if isinstance(v, (str, int, float, bool, type(None))):
            safe[k] = v
        elif isinstance(v, (list, dict)):
            try:
                json.dumps(v)
                safe[k] = v
            except (TypeError, ValueError):
                safe[k] = str(v)
        else:
            safe[k] = str(v)
    return safe
