"""Rich narration engine (Simplified Chinese).

Translates MCP tool calls + results into detailed Chinese gameplay commentary.
Provides situation analysis, strategic reasoning, and action context.
All output is in Simplified Chinese with human-like commentary.
"""

from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------------------
# Node type / keyword translation
# ---------------------------------------------------------------------------

_NODE_TYPE_ZH: dict[str, str] = {
    "Monster": "普通怪", "monster": "普通怪",
    "Elite": "精英怪", "elite": "精英怪",
    "Boss": "首领", "boss": "首领",
    "Rest": "篝火", "rest": "篝火", "rest_site": "篝火",
    "Shop": "商店", "shop": "商店",
    "Treasure": "宝箱", "treasure": "宝箱",
    "Event": "事件", "event": "事件", "Unknown": "未知", "unknown": "未知",
    "?": "未知",
}


def _zh_node(ntype: str) -> str:
    """Translate a node/state type to Chinese."""
    return _NODE_TYPE_ZH.get(ntype, ntype)


_RARITY_ZH: dict[str, str] = {
    "Common": "普通", "common": "普通",
    "Uncommon": "罕见", "uncommon": "罕见",
    "Rare": "稀有", "rare": "稀有",
    "Special": "特殊", "special": "特殊",
    "Basic": "基础", "basic": "基础",
    "Curse": "诅咒", "curse": "诅咒",
    "Status": "状态", "status": "状态",
}


def _zh_rarity(r: str) -> str:
    return _RARITY_ZH.get(r, r)


_CARD_TYPE_ZH: dict[str, str] = {
    "Attack": "攻击", "attack": "攻击",
    "Skill": "技能", "skill": "技能",
    "Power": "能力", "power": "能力",
    "Curse": "诅咒", "curse": "诅咒",
    "Status": "状态", "status": "状态",
}


def _zh_ctype(t: str) -> str:
    return _CARD_TYPE_ZH.get(t, t)


_ITEM_TYPE_ZH: dict[str, str] = {
    "card": "卡牌", "relic": "遗物", "potion": "药水",
    "card_removal": "移除卡牌",
}


def _zh_item_type(t: str) -> str:
    return _ITEM_TYPE_ZH.get(t, t)


class NarrationEngine:
    def __init__(self):
        self._last_state: dict | None = None
        self._last_tool: str | None = None
        self._turn_actions: list[str] = []
        self._last_error: str | None = None
        self._action_count: int = 0

    def narrate(self, tool_name: str, params: dict, result: str,
                state_data: dict | None = None) -> tuple[str | None, str]:
        """Generate narration text and event type for a tool call.

        Args:
            state_data: Optional pre-parsed JSON game state. When provided
                        (e.g. from the MCP server for markdown-format
                        get_game_state calls), this is used to update the
                        cached state so subsequent action narrations can
                        resolve card/reward/relic names by index.

        Returns:
            (text, event_type) — text is None to suppress the event.
            event_type is one of: "narration", "action", "state".
        """
        result_data = self._try_parse_json(result)

        # Detect errors from the result and track them
        if result_data and result_data.get("status") == "error":
            self._last_error = result_data.get("error", result_data.get("message", "未知错误"))

        # Cache game state for context — prefer externally-provided state,
        # then fall back to parsing the result (for JSON-format responses).
        if state_data and isinstance(state_data, dict):
            self._last_state = state_data
        elif tool_name in ("get_game_state", "mp_get_game_state"):
            if result_data:
                self._last_state = result_data

        # Track turn actions for end-of-turn summary
        if tool_name in ("combat_end_turn", "mp_combat_end_turn"):
            summary = self._turn_actions.copy()
            self._turn_actions.clear()
            text = _narrate_end_turn(params, result, result_data, self._last_state, summary)
            self._last_tool = tool_name
            self._action_count = 0
            return text, "action"

        if tool_name in ("combat_play_card", "mp_combat_play_card",
                         "use_potion", "mp_use_potion"):
            action_text = _NARRATORS.get(tool_name, _narrate_generic)(
                params, result, result_data, self._last_state)
            if action_text:
                self._turn_actions.append(action_text)
            self._action_count += 1

        handler = _NARRATORS.get(tool_name, _narrate_generic)
        text = handler(params, result, result_data, self._last_state)

        # Build error correction note
        error_note = ""
        if self._last_error and result_data and result_data.get("status") == "error":
            error_note = f"\n⚠️ 操作失败: {self._last_error}"
        elif self._last_error and result_data and result_data.get("status") == "ok":
            error_note = f"\n✅ 已修正之前的错误（{self._last_error}）"
            self._last_error = None

        if error_note and text:
            text = text + error_note

        self._last_tool = tool_name

        # Classify event type
        event_type = _classify_event(tool_name)
        return text, event_type

    @staticmethod
    def _try_parse_json(text: str) -> dict | None:
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_card(card_index: int, state: dict | None) -> dict | None:
    """Get full card dict from state by index."""
    if not state:
        return None
    hand = state.get("player", {}).get("hand", [])
    for card in hand:
        if card.get("index") == card_index:
            return card
    if 0 <= card_index < len(hand):
        return hand[card_index]
    return None


def _resolve_card_name(card_index: int, state: dict | None) -> str:
    card = _resolve_card(card_index, state)
    if card:
        name = card.get("name", f"#{card_index}")
        if card.get("upgraded", False):
            name += "+"
        return name
    return f"#{card_index}"


def _resolve_enemy(entity_id: str, state: dict | None) -> dict | None:
    if not state:
        return None
    for e in state.get("battle", {}).get("enemies", []):
        if e.get("entity_id") == entity_id:
            return e
    return None


def _resolve_enemy_name(entity_id: str, state: dict | None) -> str:
    e = _resolve_enemy(entity_id, state)
    return e.get("name", entity_id) if e else entity_id


def _resolve_potion(slot: int, state: dict | None) -> dict | None:
    if not state:
        return None
    for p in state.get("player", {}).get("potions", []):
        if p.get("slot") == slot:
            return p
    return None


def _resolve_potion_name(slot: int, state: dict | None) -> str:
    p = _resolve_potion(slot, state)
    return p.get("name", f"#{slot}") if p else f"#{slot}"


def _format_intent(intent: dict) -> str:
    itype = intent.get("type", "")
    label = intent.get("label", "")
    if itype == "Attack":
        return f"攻击({label})" if label else "攻击"
    mapping = {
        "Defend": "防御", "Block": "防御",
        "Buff": "增益", "Debuff": "减益",
        "Sleep": "休眠", "Unknown": "未知", "Stun": "眩晕",
        "Escape": "逃跑", "AttackDebuff": "攻击+减益",
        "AttackDefend": "攻击+防御", "AttackBuff": "攻击+增益",
        "DefendBuff": "防御+增益", "Strategic": "战略",
    }
    return mapping.get(itype, itype)


def _hp_percentage(hp, max_hp) -> float:
    try:
        return hp / max_hp * 100
    except (TypeError, ZeroDivisionError):
        return 100.0


def _hp_status(hp, max_hp) -> str:
    pct = _hp_percentage(hp, max_hp)
    if pct >= 80:
        return "健康"
    if pct >= 50:
        return "受伤"
    if pct >= 30:
        return "危险"
    return "濒死"


def _total_incoming_damage(enemies: list[dict]) -> int:
    total = 0
    for e in enemies:
        for intent in e.get("intents", []):
            if intent.get("type") == "Attack":
                label = intent.get("label", "0")
                try:
                    # Handle "12x3" format
                    if "x" in str(label):
                        parts = str(label).split("x")
                        total += int(parts[0]) * int(parts[1])
                    else:
                        total += int(label)
                except (ValueError, IndexError):
                    pass
    return total



# ---------------------------------------------------------------------------
# State narrators (get_game_state)
# ---------------------------------------------------------------------------

def _narrate_get_state(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str | None:
    if not parsed:
        return None  # No game data — suppress

    st = parsed.get("state_type", "unknown")

    if st in ("monster", "elite", "boss"):
        return _narrate_combat_state(parsed, st)
    if st == "map":
        return _narrate_map_state(parsed)
    if st == "event":
        return _narrate_event_state(parsed)
    if st == "rest_site":
        return _narrate_rest_state(parsed)
    if st in ("shop", "fake_merchant"):
        return _narrate_shop_state(parsed)
    if st == "rewards":
        return _narrate_rewards_state(parsed)
    if st == "card_reward":
        return _narrate_card_reward_state(parsed)
    if st == "hand_select":
        return _narrate_hand_select_state(parsed)
    if st == "menu":
        return None  # Menu — no game info
    if st == "treasure":
        return _narrate_treasure_state(parsed)
    if st == "card_select":
        return _narrate_card_select_state(parsed)
    if st == "relic_select":
        return _narrate_relic_select_state(parsed)
    if st == "bundle_select":
        return _narrate_bundle_select_state(parsed)
    return None  # Unknown state — suppress


def _narrate_combat_state(data: dict, st: str) -> str:
    battle = data.get("battle", {})
    player = data.get("player", {})
    enemies = battle.get("enemies", [])
    turn = battle.get("turn", "player")
    round_num = battle.get("round", "?")

    if turn == "enemy":
        return None  # Enemy turn — no useful info to show

    label = {"monster": "普通战斗", "elite": "⚔️ 精英战", "boss": "👑 首领战"}
    lines = [f"[ {label.get(st, '战斗')} — 第{round_num}回合 ]"]

    # Player status
    hp = player.get("hp", "?")
    max_hp = player.get("max_hp", "?")
    energy = player.get("energy", "?")
    hand = player.get("hand", [])
    block = player.get("block", 0)
    status = _hp_status(hp, max_hp) if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)) else ""

    player_line = f"我方: {hp}/{max_hp} 生命（{status}）"
    if block:
        player_line += f" | {block}格挡"
    player_line += f" | {energy}能量 | {len(hand)}张手牌"
    lines.append(player_line)

    # Player powers
    powers = player.get("powers", [])
    if powers:
        power_strs = []
        for p in powers:
            name = p.get("name", "?")
            amt = p.get("amount", "")
            power_strs.append(f"{name}×{amt}" if amt else name)
        lines.append(f"状态效果: {', '.join(power_strs)}")

    # Hand cards
    if hand:
        card_names = []
        for c in hand:
            name = c.get("name", "?")
            cost = c.get("cost", "?")
            if c.get("upgraded", False):
                name += "+"
            card_names.append(f"{name}({cost}费)")
        lines.append(f"手牌: {', '.join(card_names)}")

    # Enemy status
    lines.append("")
    total_dmg = _total_incoming_damage(enemies)
    for e in enemies:
        name = e.get("name", e.get("entity_id", "???"))
        ehp = e.get("hp", "?")
        emax = e.get("max_hp", "?")
        eblock = e.get("block", 0)
        intents = e.get("intents", [])

        enemy_line = f"敌方 [{name}]: {ehp}/{emax} 生命"
        if eblock:
            enemy_line += f" | {eblock}格挡"

        if intents:
            intent_strs = [_format_intent(i) for i in intents]
            enemy_line += f" | 意图: {'+'.join(intent_strs)}"

        # Enemy powers
        epowers = e.get("powers", [])
        if epowers:
            epower_strs = [f"{p.get('name', '?')}×{p.get('amount', '')}" if p.get('amount') else p.get('name', '?') for p in epowers]
            enemy_line += f" | {', '.join(epower_strs)}"

        lines.append(enemy_line)

    # Threat assessment with personality
    if total_dmg > 0:
        lines.append("")
        if block >= total_dmg:
            lines.append(f"📊 局势分析: 敌方预计造成{total_dmg}点伤害，我方已有{block}格挡，可以安心进攻。")
        else:
            net = total_dmg - block
            if isinstance(hp, (int, float)) and net >= hp:
                lines.append(f"🚨 危险！敌方预计造成{total_dmg}点伤害，我方只有{block}格挡，不防御就会死！")
            elif isinstance(hp, (int, float)) and net >= hp * 0.5:
                lines.append(f"⚠️ 形势严峻。敌方预计造成{total_dmg}点伤害，需要额外{net}点格挡才能完全抵挡。")
            else:
                lines.append(f"📊 局势分析: 敌方预计造成{total_dmg}点伤害，还需要{net}点格挡来防御。")
    elif total_dmg == 0 and enemies:
        # Enemies not attacking
        any_buff = any(
            any(i.get("type") in ("Buff", "Defend") for i in e.get("intents", []))
            for e in enemies
        )
        if any_buff:
            lines.append("")
            lines.append("📊 局势分析: 敌方本回合不会攻击，正在蓄力，这是全力输出的好机会！")
        else:
            lines.append("")
            lines.append("📊 局势分析: 敌方本回合没有攻击意图，放手进攻吧。")

    return "\n".join(lines)


def _narrate_map_state(data: dict) -> str:
    map_data = data.get("map", {})
    options = map_data.get("next_options", [])
    run = data.get("run", {})
    player = data.get("player", {})
    floor = run.get("floor", "?")
    hp = player.get("hp", "?")
    max_hp = player.get("max_hp", "?")
    gold = player.get("gold", 0)

    lines = [f"🗺️ [ 地图 — 第{floor}层 ]"]
    lines.append(f"状态: {hp}/{max_hp} 生命 | {gold}金币")

    if options:
        lines.append("可选路径:")
        for opt in options:
            ntype = _zh_node(opt.get("type", "???"))
            leads = opt.get("leads_to", [])
            path_str = f"  • {ntype}"
            if leads:
                next_types = [_zh_node(l.get("type", "?")) for l in leads]
                path_str += f"（通往: {', '.join(next_types)}）"
            lines.append(path_str)

    # Strategic hint with personality
    if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)):
        pct = _hp_percentage(hp, max_hp)
        if pct < 30:
            lines.append("")
            lines.append("💀 生命值极低，必须找到篝火恢复，否则下一场战斗可能撑不住。")
        elif pct < 50:
            lines.append("")
            lines.append("⚠️ 生命值偏低，优先选择篝火或商店补给，避开精英怪。")
        elif pct > 80 and any(o.get("type") in ("elite", "Elite") for o in options):
            lines.append("")
            lines.append("💪 状态不错，可以考虑挑战精英怪获取遗物！")

    return "\n".join(lines)


def _narrate_event_state(data: dict) -> str:
    event = data.get("event", {})
    name = event.get("event_name", "未知事件")
    in_dialogue = event.get("in_dialogue", False)

    if in_dialogue:
        return f"📜 遇到事件「{name}」，正在阅读对话..."

    lines = [f"📜 [ 事件: {name} ]"]
    options = event.get("options", [])
    if options:
        lines.append("可选项:")
        for o in options:
            title = o.get("title", "???")
            locked = o.get("is_locked", False)
            desc = o.get("description", "")
            prefix = "🔒 " if locked else ""
            opt_line = f"  {prefix}[{o.get('index', '?')}] {title}"
            if desc:
                opt_line += f" — {desc}"
            lines.append(opt_line)
    return "\n".join(lines)


def _narrate_rest_state(data: dict) -> str:
    rest = data.get("rest_site", {})
    player = data.get("player", {})
    options = rest.get("options", [])
    hp = player.get("hp", "?")
    max_hp = player.get("max_hp", "?")

    lines = [f"🔥 [ 篝火 ] 生命: {hp}/{max_hp}"]
    if options:
        opt_names = []
        for o in options:
            name = o.get("name", "???")
            enabled = o.get("is_enabled", True)
            opt_names.append(f"{name}{'（不可用）' if not enabled else ''}")
        lines.append(f"选项: {', '.join(opt_names)}")

    # Strategic hint with personality
    if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)):
        pct = _hp_percentage(hp, max_hp)
        if pct < 40:
            lines.append("🩹 伤痕累累，必须休息恢复一下。")
        elif pct < 60:
            lines.append("🤔 生命值偏低，休息恢复更安全；但如果有关键牌可以升级的话...")
        else:
            lines.append("💪 生命值充足，可以考虑升级关键卡牌来强化牌组。")

    return "\n".join(lines)


def _resolve_shop_item(item: dict) -> tuple[str, str, str]:
    """Extract name, description, and category label from a shop item.

    Shop items use category-specific keys: card_name/relic_name/potion_name.
    """
    cat = item.get("category", "")
    if cat == "card":
        name = item.get("card_name", item.get("name", "???"))
        desc = item.get("card_description", item.get("description", ""))
        label = "卡牌"
    elif cat == "relic":
        name = item.get("relic_name", item.get("name", "???"))
        desc = item.get("relic_description", item.get("description", ""))
        label = "遗物"
    elif cat == "potion":
        name = item.get("potion_name", item.get("name", "???"))
        desc = item.get("potion_description", item.get("description", ""))
        label = "药水"
    elif cat == "card_removal":
        name = "移除卡牌"
        desc = ""
        label = "服务"
    else:
        name = item.get("name", "???")
        desc = item.get("description", "")
        label = _zh_item_type(item.get("type", cat))
    return name, desc, label


def _narrate_shop_state(data: dict) -> str:
    player = data.get("player", {})
    gold = player.get("gold", "?")
    shop = data.get("shop", {})
    items = shop.get("items", [])

    lines = [f"🛒 [ 商店 ] 持有金币: {gold}"]
    if items:
        for item in items:
            name, desc, label = _resolve_shop_item(item)
            price = item.get("price", item.get("cost", "?"))
            stocked = item.get("is_stocked", True)
            if not stocked:
                continue  # Skip sold-out items
            afford = item.get("can_afford", True)
            suffix = "" if afford else " [买不起]"
            lines.append(f"  • {name}（{price}金）[{label}]{suffix}")
    removal = shop.get("card_removal", {})
    if removal:
        cost = removal.get("cost", removal.get("price", "?"))
        available = removal.get("available", True)
        lines.append(f"  • 移除卡牌（{cost}金）{'[可用]' if available else '[已售罄]'}")

    return "\n".join(lines)


def _narrate_rewards_state(data: dict) -> str | None:
    rewards = data.get("rewards", {})
    items = rewards.get("items", [])
    if not items:
        return None  # No reward data — suppress

    lines = ["🏆 [ 战斗奖励 ]"]
    for item in items:
        desc = item.get("description", item.get("type", "???"))
        lines.append(f"  • {desc}")
    return "\n".join(lines)


def _narrate_card_reward_state(data: dict) -> str | None:
    cr = data.get("card_reward", {})
    cards = cr.get("cards", [])
    if not cards:
        return None  # No card data — suppress

    lines = ["🃏 [ 卡牌奖励 ] 需要选择一张卡牌:"]
    for c in cards:
        name = c.get("name", "???")
        cost = c.get("cost", "?")
        rarity = _zh_rarity(c.get("rarity", ""))
        ctype = _zh_ctype(c.get("type", ""))
        desc = c.get("description", "")
        upgraded = "+" if c.get("upgraded", False) else ""

        card_line = f"  [{c.get('index', '?')}] {name}{upgraded}（{cost}费）[{rarity}/{ctype}]"
        if desc:
            card_line += f"\n      {desc}"
        lines.append(card_line)

    return "\n".join(lines)


def _narrate_hand_select_state(data: dict) -> str:
    hs = data.get("hand_select", {})
    prompt = hs.get("prompt", "选择卡牌")
    return f"✋ 需要从手牌中选择: {prompt}"


def _narrate_card_select_state(data: dict) -> str:
    cs = data.get("card_select", {})
    screen_type = cs.get("screen_type", "")
    cards = cs.get("cards", [])

    lines = [f"🃏 [ 卡牌选择: {screen_type or '从牌组中选择'} ]"]
    if cards:
        for c in cards:
            name = c.get("name", "???")
            upgraded = "+" if c.get("upgraded", False) else ""
            lines.append(f"  • {name}{upgraded}")
    return "\n".join(lines)


def _narrate_relic_select_state(data: dict) -> str | None:
    rs = data.get("relic_select", {})
    relics = rs.get("relics", [])
    if not relics:
        return None  # No relic data — suppress

    lines = ["🏺 [ 遗物选择 ]"]
    for r in relics:
        name = r.get("name", "???")
        desc = r.get("description", "")
        relic_line = f"  • {name}"
        if desc:
            relic_line += f": {desc}"
        lines.append(relic_line)
    return "\n".join(lines)


def _narrate_treasure_state(data: dict) -> str | None:
    """Narrate treasure chest contents."""
    treasure = data.get("treasure", {})
    relics = treasure.get("relics", [])
    if not relics:
        return None  # No data — suppress
    lines = ["🎁 [ 宝箱 ]"]
    for r in relics:
        name = r.get("name", "???")
        desc = r.get("description", "")
        relic_line = f"  • {name}"
        if desc:
            relic_line += f": {desc}"
        lines.append(relic_line)
    return "\n".join(lines)


def _narrate_bundle_select_state(data: dict) -> str | None:
    """Narrate bundle selection with actual bundle info."""
    bs = data.get("bundle_select", data.get("bundles", {}))
    bundles = bs.get("bundles", []) if isinstance(bs, dict) else []
    if not bundles:
        return None  # No data — suppress
    lines = ["📦 [ 卡组包选择 ]"]
    for i, b in enumerate(bundles):
        name = b.get("name", f"卡组包{i}")
        cards = b.get("cards", [])
        card_names = [c.get("name", "?") for c in cards]
        line = f"  [{i}] {name}"
        if card_names:
            line += f": {', '.join(card_names)}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Action narrators
# ---------------------------------------------------------------------------

def _narrate_play_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("card_index", "?")
    target = params.get("target")
    card = _resolve_card(idx, state) if isinstance(idx, int) else None
    card_name = _resolve_card_name(idx, state) if isinstance(idx, int) else f"第{idx}张"

    parts = [f"🎴 出牌: {card_name}"]

    # Card details
    if card:
        cost = card.get("cost", "?")
        desc = card.get("description", "")
        parts[0] += f"（{cost}费）"
        if desc:
            parts.append(f"  效果: {desc}")

    # Target info
    if target:
        enemy = _resolve_enemy(target, state)
        enemy_name = enemy.get("name", target) if enemy else target
        parts[0] += f" → {enemy_name}"
        if enemy:
            ehp = enemy.get("hp", "?")
            emax = enemy.get("max_hp", "?")
            parts[0] += f"（{ehp}/{emax} 生命）"

    # Check for error in result
    if parsed and parsed.get("status") == "error":
        error = parsed.get("error", parsed.get("message", "未知错误"))
        parts.append(f"❌ 出牌失败: {error}")

    return "\n".join(parts)


def _narrate_end_turn(
    params: dict, result: str, parsed: dict | None, state: dict | None,
    turn_actions: list[str] | None = None,
) -> str:
    lines = ["⏩ ——— 回合结束 ———"]
    if turn_actions:
        lines.append(f"本回合执行了 {len(turn_actions)} 个操作")
    return "\n".join(lines)


def _narrate_combat_batch(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    actions = params.get("actions", [])
    lines = [f"⚡ [ 连续操作: {len(actions)}个动作 ]"]

    for i, action in enumerate(actions):
        atype = action.get("type", "?")
        if atype == "play_card":
            card_name = _resolve_card_name(action.get("card_index", "?"), state) \
                if isinstance(action.get("card_index"), int) else f"第{action.get('card_index', '?')}张"
            target = action.get("target")
            line = f"  {i+1}. 出牌: {card_name}"
            if target:
                line += f" → {_resolve_enemy_name(target, state)}"
            lines.append(line)
        elif atype == "use_potion":
            name = _resolve_potion_name(action.get("slot", 0), state)
            line = f"  {i+1}. 使用药水: {name}"
            if action.get("target"):
                line += f" → {_resolve_enemy_name(action['target'], state)}"
            lines.append(line)
        elif atype == "end_turn":
            lines.append(f"  {i+1}. 结束回合")
        else:
            lines.append(f"  {i+1}. {atype}")

    return "\n".join(lines)


def _narrate_use_potion(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    slot = params.get("slot", "?")
    target = params.get("target")
    potion = _resolve_potion(slot, state) if isinstance(slot, int) else None
    name = potion.get("name", f"第{slot}瓶") if potion else f"第{slot}瓶"

    lines = [f"🧪 使用药水: {name}"]
    if target:
        enemy_name = _resolve_enemy_name(target, state)
        lines[0] += f" → {enemy_name}"

    if potion:
        desc = potion.get("description", "")
        if desc:
            lines.append(f"  效果: {desc}")

    if parsed and parsed.get("status") == "error":
        lines.append(f"❌ 失败: {parsed.get('error', '未知错误')}")

    return "\n".join(lines)


def _narrate_discard_potion(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    slot = params.get("slot", "?")
    name = _resolve_potion_name(slot, state) if isinstance(slot, int) else f"第{slot}瓶"
    return f"🗑️ 丢弃药水: {name}（腾出药水槽位）"


def _narrate_proceed(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str | None:
    return None  # Mechanical navigation — suppress


def _narrate_map_choice(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("node_index", params.get("index", "?"))
    if state:
        options = state.get("map", {}).get("next_options", [])
        player = state.get("player", {})
        hp = player.get("hp", "?")
        max_hp = player.get("max_hp", "?")
        run = state.get("run", {})
        floor = run.get("floor", "?")

        chosen = None
        others = []
        for opt in options:
            if opt.get("index") == idx:
                chosen = opt
            else:
                others.append(opt)

        if chosen:
            ntype = _zh_node(chosen.get("type", "???"))
            leads = chosen.get("leads_to", [])
            lines = [f"🗺️ 选择路径: 前往「{ntype}」（第{floor}层）"]
            if leads:
                next_types = [_zh_node(l.get("type", "?")) for l in leads]
                lines[0] += f" → {', '.join(next_types)}"
            lines.append(f"  当前状态: {hp}/{max_hp} 生命")
            if others:
                other_types = [_zh_node(o.get("type", "?")) for o in others]
                lines.append(f"  其他路径: {', '.join(other_types)}")
            return "\n".join(line for line in lines if line)
    return f"🗺️ 选择第{idx}条路径"


def _narrate_rest_choice(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("option_index", params.get("index", "?"))
    if state:
        rest = state.get("rest_site", {})
        player = state.get("player", {})
        options = rest.get("options", [])
        hp = player.get("hp", "?")
        max_hp = player.get("max_hp", "?")

        chosen_name = None
        other_names = []
        for opt in options:
            name = opt.get("name", "???")
            if opt.get("index") == idx:
                chosen_name = name
            elif opt.get("is_enabled", True):
                other_names.append(name)

        if chosen_name:
            lines = [f"🔥 篝火选择: {chosen_name}（生命: {hp}/{max_hp}）"]
            if other_names:
                lines.append(f"  其他选项: {', '.join(other_names)}")
            return "\n".join(line for line in lines if line)
    return "🔥 篝火选择..."


def _narrate_shop_buy(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("item_index", params.get("index", "?"))
    if state:
        items = state.get("shop", {}).get("items", [])
        player = state.get("player", {})
        gold = player.get("gold", "?")

        if isinstance(idx, int):
            for item in items:
                if item.get("index") == idx:
                    name, desc, label = _resolve_shop_item(item)
                    price = item.get("price", item.get("cost", "?"))
                    lines = [f"🛒 购买: {name}（{price}金）[{label}]"]
                    if desc:
                        lines.append(f"  效果: {desc}")
                    lines.append(f"  剩余金币: {gold}")
                    return "\n".join(line for line in lines if line)
    return f"🛒 购买第{idx}件商品"


def _narrate_event_option(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("option_index", params.get("index", "?"))
    if state:
        event = state.get("event", {})
        event_name = event.get("event_name", "未知事件")
        options = event.get("options", [])
        chosen = None
        others = []
        for opt in options:
            if opt.get("index") == idx:
                chosen = opt
            elif not opt.get("is_locked", False) and not opt.get("is_proceed", False):
                others.append(opt)

        if chosen:
            if chosen.get("is_proceed", False):
                return "🚶 继续前进..."
            title = chosen.get("title", "???")
            desc = chosen.get("description", "")
            lines = [f"📜 [ 事件「{event_name}」]"]
            lines.append(f"选择: 「{title}」")
            if desc:
                lines.append(f"  效果: {desc}")
            if others:
                other_strs = []
                for o in others:
                    o_title = o.get("title", "?")
                    o_desc = o.get("description", "")
                    other_strs.append(f"「{o_title}」" + (f"（{o_desc}）" if o_desc else ""))
                lines.append(f"  放弃选项: {'; '.join(other_strs)}")
            return "\n".join(line for line in lines if line)
    return f"📜 事件选择第{idx}项"


def _narrate_advance_dialogue(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return None  # Suppress dialogue advance noise


def _narrate_claim_reward(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("reward_index", params.get("index", "?"))
    if state:
        items = state.get("rewards", {}).get("items", [])
        chosen = None
        remaining = []
        for item in items:
            if item.get("index") == idx:
                chosen = item
            else:
                remaining.append(item)

        if chosen:
            desc = chosen.get("description", chosen.get("type", "???"))
            lines = [f"🎁 领取奖励: {desc}"]
            if remaining:
                rem_names = [i.get("description", i.get("type", "?")) for i in remaining]
                lines.append(f"  剩余奖励: {', '.join(rem_names)}")
            return "\n".join(line for line in lines if line)
    return f"🎁 领取第{idx}个奖励"


def _narrate_claim_all(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "🎁 一次性领取所有非卡牌奖励"


def _narrate_pick_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("card_index", "?")
    if state:
        cards = state.get("card_reward", {}).get("cards", [])
        chosen = None
        others = []
        for card in cards:
            if card.get("index") == idx:
                chosen = card
            else:
                others.append(card)

        if chosen:
            name = chosen.get("name", "???")
            rarity = _zh_rarity(chosen.get("rarity", ""))
            cost = chosen.get("cost", "?")
            desc = chosen.get("description", "")
            upgraded = "+" if chosen.get("upgraded", False) else ""

            lines = [f"🃏 选择卡牌: {name}{upgraded}（{cost}费）[{rarity}]"]
            if desc:
                lines.append(f"  效果: {desc}")
            if others:
                other_strs = []
                for o in others:
                    oname = o.get("name", "?")
                    ocost = o.get("cost", "?")
                    if o.get("upgraded", False):
                        oname += "+"
                    other_strs.append(f"{oname}（{ocost}费）")
                lines.append(f"  放弃选项: {', '.join(other_strs)}")
            return "\n".join(line for line in lines if line)
    return f"🃏 选择第{idx}张卡牌"


def _narrate_skip_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    if state:
        cards = state.get("card_reward", {}).get("cards", [])
        if cards:
            names = []
            for c in cards:
                name = c.get("name", "?")
                cost = c.get("cost", "?")
                if c.get("upgraded", False):
                    name += "+"
                names.append(f"{name}（{cost}费）")
            lines = ["🚫 跳过卡牌奖励"]
            lines.append(f"  可选卡牌: {', '.join(names)}")
            return "\n".join(line for line in lines if line)
    return "🚫 跳过卡牌奖励"


def _narrate_select_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str | None:
    return None  # Mechanical — suppress


def _narrate_confirm(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str | None:
    return None  # Mechanical — suppress


def _narrate_cancel(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str | None:
    return None  # Mechanical — suppress


def _narrate_relic_select(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("relic_index", params.get("index", "?"))
    if state:
        relics = state.get("relic_select", {}).get("relics", [])
        chosen = None
        others = []
        if isinstance(idx, int):
            for i, r in enumerate(relics):
                if i == idx:
                    chosen = r
                else:
                    others.append(r)

        if chosen:
            name = chosen.get("name", "???")
            desc = chosen.get("description", "")
            lines = [f"🏺 选择遗物: {name}"]
            if desc:
                lines.append(f"  效果: {desc}")
            if others:
                other_strs = [o.get("name", "?") for o in others]
                lines.append(f"  放弃选项: {', '.join(other_strs)}")
            return "\n".join(line for line in lines if line)
    return f"🏺 选择第{idx}个遗物"


def _narrate_relic_skip(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    if state:
        relics = state.get("relic_select", {}).get("relics", [])
        if relics:
            names = [r.get("name", "?") for r in relics]
            lines = ["🚫 跳过遗物选择"]
            lines.append(f"  可选遗物: {', '.join(names)}")
            return "\n".join(line for line in lines if line)
    return "🚫 跳过遗物选择"


def _narrate_treasure(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("relic_index", params.get("index", "?"))
    if state:
        relics = state.get("treasure", {}).get("relics", [])
        if isinstance(idx, int) and 0 <= idx < len(relics):
            name = relics[idx].get("name", "???")
            desc = relics[idx].get("description", "")
            lines = [f"🎁 从宝箱获取遗物: {name}"]
            if desc:
                lines.append(f"  效果: {desc}")
            return "\n".join(line for line in lines if line)
    return "🎁 从宝箱中获取遗物"


def _narrate_crystal_sphere(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str | None:
    return None  # Mechanical — suppress


def _narrate_combat_select_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("card_index", "?")
    card_name = _resolve_card_name(idx, state) if isinstance(idx, int) else f"第{idx}张"
    return f"✋ 选中: {card_name}"


def _narrate_generic(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str | None:
    return None  # Unknown tool — suppress filler


def _narrate_ai_narration(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    """Pass through the AI's own narration text directly."""
    return params.get("text", "...")


# ---------------------------------------------------------------------------
# Event type classification
# ---------------------------------------------------------------------------

_EVENT_TYPE_MAP: dict[str, str] = {
    "narrate": "narration",
    "get_game_state": "state",
    "combat_play_card": "action",
    "combat_batch": "action",
    "combat_end_turn": "action",
    "use_potion": "action",
    "discard_potion": "action",
    "combat_select_card": "action",
    "combat_confirm_selection": "action",
    "map_choose_node": "action",
    "event_choose_option": "action",
    "event_advance_dialogue": "action",
    "rest_choose_option": "action",
    "shop_purchase": "action",
    "rewards_claim": "action",
    "rewards_claim_all": "action",
    "rewards_pick_card": "action",
    "rewards_skip_card": "action",
    "deck_select_card": "action",
    "deck_confirm_selection": "action",
    "deck_cancel_selection": "action",
    "bundle_select": "action",
    "bundle_confirm_selection": "action",
    "bundle_cancel_selection": "action",
    "relic_select": "action",
    "relic_skip": "action",
    "treasure_claim_relic": "action",
    "proceed_to_map": "action",
    "crystal_sphere_set_tool": "action",
    "crystal_sphere_click_cell": "action",
    "crystal_sphere_proceed": "action",
}
# Auto-populate mp_ variants
_EVENT_TYPE_MAP.update({
    f"mp_{k}": v for k, v in _EVENT_TYPE_MAP.items()
    if k != "narrate"
})


def _classify_event(tool_name: str) -> str:
    """Classify tool into event type: narration, action, or state."""
    return _EVENT_TYPE_MAP.get(tool_name, "action")


# ---------------------------------------------------------------------------
# Tool name -> handler map
# ---------------------------------------------------------------------------

# Base (single-player) tool → handler mapping.
# mp_ variants are auto-generated below.
_BASE_NARRATORS: dict[str, Any] = {
    # AI narration (pass-through)
    "narrate": _narrate_ai_narration,
    # State queries
    "get_game_state": _narrate_get_state,
    # Combat
    "combat_play_card": _narrate_play_card,
    "combat_batch": _narrate_combat_batch,
    "combat_end_turn": _narrate_end_turn,
    "combat_select_card": _narrate_combat_select_card,
    "combat_confirm_selection": _narrate_confirm,
    # Potions
    "use_potion": _narrate_use_potion,
    "discard_potion": _narrate_discard_potion,
    # Navigation
    "proceed_to_map": _narrate_proceed,
    "map_choose_node": _narrate_map_choice,
    # Rest site
    "rest_choose_option": _narrate_rest_choice,
    # Shop
    "shop_purchase": _narrate_shop_buy,
    # Events
    "event_choose_option": _narrate_event_option,
    "event_advance_dialogue": _narrate_advance_dialogue,
    # Rewards
    "rewards_claim": _narrate_claim_reward,
    "rewards_claim_all": _narrate_claim_all,
    "rewards_pick_card": _narrate_pick_card,
    "rewards_skip_card": _narrate_skip_card,
    # Card selection overlay
    "deck_select_card": _narrate_select_card,
    "deck_confirm_selection": _narrate_confirm,
    "deck_cancel_selection": _narrate_cancel,
    # Bundle
    "bundle_select": lambda *a: None,
    "bundle_confirm_selection": lambda *a: "📦 确认选择卡组包！",
    "bundle_cancel_selection": lambda *a: None,
    # Relic selection
    "relic_select": _narrate_relic_select,
    "relic_skip": _narrate_relic_skip,
    # Treasure
    "treasure_claim_relic": _narrate_treasure,
    # Crystal sphere
    "crystal_sphere_set_tool": _narrate_crystal_sphere,
    "crystal_sphere_click_cell": _narrate_crystal_sphere,
    "crystal_sphere_proceed": lambda *a: None,
}

# Build final map: base + auto-generated mp_ variants + special mp-only tools
_NARRATORS: dict[str, Any] = dict(_BASE_NARRATORS)
_NARRATORS.update({
    f"mp_{k}": v for k, v in _BASE_NARRATORS.items()
    if k != "narrate"  # narrate has no mp_ variant
})
# mp-only tools (no single-player equivalent)
_NARRATORS["mp_map_vote"] = _narrate_map_choice
_NARRATORS["mp_combat_undo_end_turn"] = lambda *a: None
