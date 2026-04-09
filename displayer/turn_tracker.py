"""Combat turn tracker for automatic mistake analysis.

Collects per-turn combat data (state, actions, results) and produces
structured summaries that can be sent to an LLM for mistake detection.
"""

from __future__ import annotations

import json
from typing import Any


_COMBAT_TYPES = frozenset(("monster", "elite", "boss"))
_ACTION_TOOLS = frozenset((
    "combat_play_card", "combat_batch", "use_potion",
    "discard_potion", "combat_select_card", "combat_confirm_selection",
    "mp_combat_play_card", "mp_use_potion",
    "mp_combat_select_card", "mp_combat_confirm_selection",
))


class CombatTurnTracker:
    """Tracks combat turns and produces summaries for mistake analysis."""

    def __init__(self, max_turns: int = 20):
        self._in_combat = False
        self._current_round = 0
        self._turn_start_state: dict | None = None
        self._turn_actions: list[dict] = []
        self._turn_narration: str | None = None
        self._completed_turns: list[dict] = []
        self._max_turns = max_turns

    # ------------------------------------------------------------------
    # Event processing
    # ------------------------------------------------------------------

    def process_event(
        self,
        tool_name: str,
        params: dict,
        result: str,
        state: dict | None,
    ) -> None:
        """Process a tool call event, tracking combat turns."""

        # Try to extract state from result if not provided
        if state is None:
            state = self._try_extract_state(result)

        state_type = state.get("state_type") if state else None

        # -- Combat start / turn boundary detection --
        if state_type in _COMBAT_TYPES:
            if not self._in_combat:
                self._in_combat = True
                self._current_round = 0
                self._completed_turns = []
                self._turn_actions = []
                self._turn_narration = None

            battle = state.get("battle", {})
            round_num = battle.get("round", 0)

            if round_num > self._current_round:
                # Finalize previous turn
                if self._current_round > 0 and self._turn_start_state:
                    self._completed_turns.append({
                        "turn": self._current_round,
                        "start_state": self._turn_start_state,
                        "actions": self._turn_actions.copy(),
                        "narration": self._turn_narration,
                        "result_state": state,
                    })
                    if len(self._completed_turns) > self._max_turns:
                        self._completed_turns.pop(0)

                self._current_round = round_num
                self._turn_start_state = state
                self._turn_actions = []
                self._turn_narration = None

        elif state_type and state_type not in _COMBAT_TYPES:
            # Left combat — finalize last turn
            if self._in_combat and self._current_round > 0 and self._turn_start_state:
                self._completed_turns.append({
                    "turn": self._current_round,
                    "start_state": self._turn_start_state,
                    "actions": self._turn_actions.copy(),
                    "narration": self._turn_narration,
                    "result_state": state,
                })
            self._in_combat = False
            self._current_round = 0

        # -- Record combat actions --
        if self._in_combat and tool_name in _ACTION_TOOLS:
            self._turn_actions.append({
                "tool": tool_name,
                "params": params,
                "message": self._extract_message(result),
            })

        # -- Record AI narration for this turn --
        if self._in_combat and tool_name in ("narrate", "mp_narrate"):
            self._turn_narration = params.get("text", "")

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def get_last_turn(self) -> dict | None:
        return self._completed_turns[-1] if self._completed_turns else None

    def get_all_turns(self) -> list[dict]:
        return self._completed_turns.copy()

    @property
    def in_combat(self) -> bool:
        return self._in_combat

    @property
    def current_round(self) -> int:
        return self._current_round

    def clear(self) -> None:
        self._in_combat = False
        self._current_round = 0
        self._turn_start_state = None
        self._turn_actions = []
        self._turn_narration = None
        self._completed_turns = []

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_last_turn_summary(self) -> str | None:
        return self.format_turn_summary(self.get_last_turn())

    def format_combat_summary(self) -> str | None:
        """Format a full-combat strategic summary across ALL completed turns.

        Used for post-combat strategic analysis — identifies cross-turn patterns
        like wasted resources, missed kill windows, suboptimal target focus, etc.
        """
        turns = self._completed_turns
        if not turns:
            return None

        lines = ["# 战斗全局复盘数据"]

        # ── Overall stats ──────────────────────────────────────────────
        first_state = turns[0]["start_state"]
        last_state = turns[-1]["result_state"]
        fp = first_state.get("player", {})
        lp = last_state.get("player", {})

        hp_start = fp.get("hp", 0)
        hp_end = lp.get("hp", 0)
        max_hp = fp.get("max_hp", 0)
        hp_lost = hp_start - hp_end
        total_rounds = len(turns)

        lines.append(f"\n## 总览")
        lines.append(f"- 总回合数: {total_rounds}")
        lines.append(f"- HP变化: {hp_start}/{max_hp} → {hp_end}/{max_hp} (损失{hp_lost})")
        lines.append(f"- 战损率: {hp_lost / max_hp * 100:.0f}%" if max_hp > 0 else "- 战损率: N/A")

        # Starting enemies
        first_enemies = first_state.get("battle", {}).get("enemies", [])
        if first_enemies:
            total_ehp = sum(e.get("hp", 0) for e in first_enemies)
            lines.append(f"- 初始敌人总HP: {total_ehp}")
            for e in first_enemies:
                lines.append(f"  - {e.get('name', '?')}: {e.get('hp', '?')}HP")

        # Potions used
        potions_used: list[str] = []
        # Energy wasted (turns where energy > 0 at end of player actions)
        energy_wasted_turns: list[int] = []

        for t in turns:
            for action in t.get("actions", []):
                tool = action.get("tool", "")
                if tool in ("use_potion", "mp_use_potion"):
                    slot = action["params"].get("slot", "?")
                    potions_used.append(f"T{t['turn']}:药水[{slot}]")
                elif tool == "combat_batch":
                    for ba in action["params"].get("actions", []):
                        if ba.get("type") == "use_potion":
                            potions_used.append(f"T{t['turn']}:药水[{ba.get('slot', '?')}]")

        if potions_used:
            lines.append(f"- 药水使用: {', '.join(potions_used)}")
        else:
            lines.append("- 药水使用: 无")

        # ── Per-turn timeline ──────────────────────────────────────────
        lines.append(f"\n## 每回合时间线")
        for t in turns:
            turn_num = t["turn"]
            start = t["start_state"]
            result = t["result_state"]
            sp = start.get("player", {})
            rp = result.get("player", {})

            hp_s = sp.get("hp", 0)
            hp_e = rp.get("hp", 0)
            energy = sp.get("energy", 0)
            hp_diff = hp_e - hp_s

            # Summarize actions concisely
            action_strs: list[str] = []
            hand = list(sp.get("hand", []))
            for action in t.get("actions", []):
                action_strs.append(self._fmt_action(action, hand))

            # Enemy intent summary
            enemies = start.get("battle", {}).get("enemies", [])
            intents: list[str] = []
            for e in enemies:
                intents.append(f"{e.get('name', '?')}:{self._fmt_enemy_intent_short(e)}")

            # Enemy HP changes
            ehp_changes: list[str] = []
            start_enemies = {
                e.get("entity_id", ""): e
                for e in start.get("battle", {}).get("enemies", [])
            }
            for e in result.get("battle", {}).get("enemies", []):
                eid = e.get("entity_id", "")
                se = start_enemies.get(eid)
                if se:
                    ehs, ehe = se.get("hp", 0), e.get("hp", 0)
                    if ehs != ehe:
                        ehp_changes.append(f"{e.get('name', '?')}:{ehs}→{ehe}")

            hp_str = f"HP{hp_diff:+d}" if hp_diff != 0 else "HP不变"
            lines.append(
                f"\n### T{turn_num} ({energy}能量, {hp_str})"
            )
            lines.append(f"- 敌意: {', '.join(intents) if intents else '无'}")
            lines.append(f"- 操作: {' → '.join(action_strs) if action_strs else '无操作'}")
            if ehp_changes:
                lines.append(f"- 敌HP: {', '.join(ehp_changes)}")
            if t.get("narration"):
                # Truncate narration to first 100 chars for summary
                narr = t["narration"][:120]
                if len(t["narration"]) > 120:
                    narr += "..."
                lines.append(f"- AI思考: {narr}")

        # ── Strategic analysis data ────────────────────────────────────
        lines.append(f"\n## 分析辅助数据")

        # Damage taken per turn
        dmg_list: list[str] = []
        for t in turns:
            sp = t["start_state"].get("player", {})
            rp = t["result_state"].get("player", {})
            dmg = sp.get("hp", 0) - rp.get("hp", 0)
            if dmg > 0:
                dmg_list.append(f"T{t['turn']}:-{dmg}")
        if dmg_list:
            lines.append(f"- 受伤回合: {', '.join(dmg_list)} (总计-{hp_lost})")
        else:
            lines.append("- 受伤回合: 无（零战损）")

        # Turns where enemies were buffing/sleeping but player blocked
        lines.append(f"- 请检查: 敌人不攻击的回合是否打了格挡牌（浪费能量）")
        lines.append(f"- 请检查: 减益牌(易伤/虚弱)是否在攻击牌之前打出")
        lines.append(f"- 请检查: 能力牌(Power)是否尽早打出")
        lines.append(f"- 请检查: 是否有更优的集火策略（先杀高威胁/低HP目标）")
        lines.append(f"- 请检查: 药水使用时机（是否过早/过晚/浪费在普通怪上）")

        return "\n".join(lines)

    @staticmethod
    def _fmt_enemy_intent_short(e: dict) -> str:
        """Format enemy intent in short form for timeline."""
        intents = e.get("intents", [])
        if not intents:
            intent = e.get("intent", {})
            if isinstance(intent, dict):
                itype = intent.get("type", "?")
                dmg = intent.get("damage")
                hits = intent.get("hits", 1)
                if dmg:
                    return f"攻击{dmg}×{hits}" if hits and hits > 1 else f"攻击{dmg}"
                return itype
            return "?"

        parts = []
        for intent in intents:
            itype = intent.get("type", "?")
            label = intent.get("label", "")
            if itype == "Attack" and label:
                parts.append(f"攻击{label}")
            else:
                parts.append(itype)
        return "+".join(parts)

    def format_turn_summary(self, turn_data: dict | None) -> str | None:
        if turn_data is None:
            return None

        turn_num = turn_data["turn"]
        start = turn_data["start_state"]
        actions = turn_data["actions"]
        result = turn_data["result_state"]
        narration = turn_data.get("narration")

        lines = [f"## 回合 {turn_num} 数据"]

        # AI's thinking
        if narration:
            lines.append("\n### AI 的思考")
            lines.append(narration)

        # Start state
        lines.append("\n### 回合开始状态")
        lines.extend(self._fmt_state(start))

        # Actions
        lines.append("\n### 操作序列")
        hand = list(start.get("player", {}).get("hand", []))
        if not actions:
            lines.append("（无操作）")
        for i, action in enumerate(actions, 1):
            lines.append(f"{i}. {self._fmt_action(action, hand)}")

        # Result state
        lines.append("\n### 回合结束状态（敌方行动后）")
        lines.extend(self._fmt_state(result))

        # Changes
        lines.append("\n### 变化统计")
        lines.extend(self._fmt_changes(start, result))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _try_extract_state(result: str) -> dict | None:
        try:
            parsed = json.loads(result) if isinstance(result, str) else None
            if isinstance(parsed, dict):
                if "state_type" in parsed:
                    return parsed
                gs = parsed.get("game_state")
                if isinstance(gs, dict) and "state_type" in gs:
                    return gs
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    @staticmethod
    def _extract_message(result: str) -> str | None:
        try:
            parsed = json.loads(result) if isinstance(result, str) else None
            if isinstance(parsed, dict):
                return parsed.get("message")
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    def _fmt_state(self, state: dict) -> list[str]:
        lines: list[str] = []
        player = state.get("player", {})

        hp = player.get("hp", "?")
        max_hp = player.get("max_hp", "?")
        block = player.get("block", 0)
        energy = player.get("energy", "?")
        lines.append(f"- 玩家: {hp}/{max_hp} HP, {block}格挡, {energy}能量")

        # Hand
        hand = player.get("hand", [])
        if hand:
            cards = []
            for c in hand:
                name = c.get("name", "?")
                cost = c.get("cost", "?")
                up = "+" if c.get("is_upgraded") else ""
                playable = "" if c.get("can_play", True) else " [不可打出]"
                cards.append(f"{name}{up}({cost}费){playable}")
            lines.append(f"- 手牌({len(hand)}张): {', '.join(cards)}")

        # Status effects
        status = player.get("status", [])
        if status:
            ss = [
                f"{s.get('name', '?')}×{s.get('amount', 0)}"
                for s in status if s.get("amount", 0) != 0
            ]
            if ss:
                lines.append(f"- 状态: {', '.join(ss)}")

        # Potions
        potions = player.get("potions", [])
        if potions:
            ps = [f"[{p.get('slot', '?')}]{p.get('name', '?')}" for p in potions]
            lines.append(f"- 药水: {', '.join(ps)}")

        # Enemies
        enemies = state.get("battle", {}).get("enemies", [])
        if enemies:
            lines.append("- 敌人:")
            for e in enemies:
                lines.append(self._fmt_enemy(e))

        return lines

    def _fmt_enemy(self, e: dict) -> str:
        name = e.get("name", "?")
        eid = e.get("entity_id", "?")
        hp = e.get("hp", "?")
        max_hp = e.get("max_hp", "?")
        block = e.get("block", 0)

        intent = e.get("intent", {})
        if isinstance(intent, dict):
            itype = intent.get("type", "?")
            dmg = intent.get("damage")
            hits = intent.get("hits", 1)
            if dmg:
                intent_str = f"{dmg}×{hits}" if hits and hits > 1 else str(dmg)
                intent_str = f"攻击{intent_str}"
            else:
                intent_str = itype
        else:
            intent_str = str(intent)

        estatus = e.get("status", [])
        status_str = ""
        if estatus:
            ss = [
                f"{s.get('name', '?')}×{s.get('amount', 0)}"
                for s in estatus if s.get("amount", 0) != 0
            ]
            if ss:
                status_str = f" [{', '.join(ss)}]"

        return f"  - {name}({eid}): {hp}/{max_hp}HP, {block}挡, 意图:{intent_str}{status_str}"

    def _fmt_action(self, action: dict, hand: list[dict]) -> str:
        tool = action["tool"]
        params = action.get("params", {})
        msg = action.get("message")

        if tool in ("combat_play_card", "mp_combat_play_card"):
            idx = params.get("card_index", -1)
            target = params.get("target", "")
            card_name = self._resolve_card(idx, hand)
            # Remove from virtual hand for next resolution
            if isinstance(idx, int) and 0 <= idx < len(hand):
                hand.pop(idx)
            target_str = f" → {target}" if target else ""
            return f"打牌: {card_name}{target_str}"

        elif tool == "combat_batch":
            # Batch uses snapshot — resolve all from original hand
            batch_hand = list(hand)
            parts = []
            for ba in params.get("actions", []):
                btype = ba.get("type", "?")
                if btype == "play_card":
                    idx = ba.get("card_index", -1)
                    card_name = self._resolve_card(idx, batch_hand)
                    target = ba.get("target", "")
                    t_str = f"→{target}" if target else ""
                    parts.append(f"{card_name}{t_str}")
                elif btype == "use_potion":
                    slot = ba.get("slot", "?")
                    target = ba.get("target", "")
                    t_str = f"→{target}" if target else ""
                    parts.append(f"药水[{slot}]{t_str}")
                elif btype == "end_turn":
                    parts.append("结束回合")
            return "批量操作: " + " → ".join(parts)

        elif tool in ("use_potion", "mp_use_potion"):
            slot = params.get("slot", "?")
            target = params.get("target", "")
            target_str = f" → {target}" if target else ""
            return f"使用药水[槽位{slot}]{target_str}"

        # Fallback: use message from result if available
        if msg:
            return msg
        return f"{tool}({json.dumps(params, ensure_ascii=False)})"

    @staticmethod
    def _resolve_card(idx: int, hand: list[dict]) -> str:
        if isinstance(idx, int) and 0 <= idx < len(hand):
            c = hand[idx]
            name = c.get("name", "?")
            cost = c.get("cost", "?")
            up = "+" if c.get("is_upgraded") else ""
            return f"{name}{up}({cost}费)"
        return f"?[idx={idx}]"

    def _fmt_changes(self, start: dict, result: dict) -> list[str]:
        lines: list[str] = []
        sp = start.get("player", {})
        rp = result.get("player", {})

        hp_s, hp_e = sp.get("hp", 0), rp.get("hp", 0)
        if hp_s != hp_e:
            diff = hp_e - hp_s
            sign = "+" if diff > 0 else ""
            lines.append(f"- 玩家HP: {hp_s} → {hp_e} ({sign}{diff})")

        # Enemy HP changes
        start_enemies = {
            e.get("entity_id", ""): e
            for e in start.get("battle", {}).get("enemies", [])
        }
        for e in result.get("battle", {}).get("enemies", []):
            eid = e.get("entity_id", "")
            se = start_enemies.get(eid)
            if se:
                ehs, ehe = se.get("hp", 0), e.get("hp", 0)
                if ehs != ehe:
                    lines.append(
                        f"- {e.get('name', '?')}: {ehs} → {ehe} ({ehe - ehs})"
                    )

        if not lines:
            lines.append("- 无显著变化")
        return lines
