"""Rich narration engine (Simplified Chinese).

Translates MCP tool calls + results into detailed Chinese gameplay commentary.
Provides situation analysis, strategic reasoning, and action context.
"""

from __future__ import annotations

import json
from typing import Any


class NarrationEngine:
    def __init__(self):
        self._last_state: dict | None = None
        self._last_tool: str | None = None
        self._turn_actions: list[str] = []  # track actions within a turn

    def narrate(self, tool_name: str, params: dict, result: str) -> str | None:
        """Generate rich Chinese narration for a tool call."""
        result_data = self._try_parse_json(result)

        # Cache game state for context
        if tool_name in ("get_game_state", "mp_get_game_state"):
            if result_data:
                self._last_state = result_data
            elif not result_data and result:
                # Markdown format — try to extract from action response
                pass

        # Track turn actions for end-of-turn summary
        if tool_name in ("combat_end_turn", "mp_combat_end_turn"):
            summary = self._turn_actions.copy()
            self._turn_actions.clear()
            return _narrate_end_turn(params, result, result_data, self._last_state, summary)

        if tool_name in ("combat_play_card", "mp_combat_play_card",
                         "use_potion", "mp_use_potion"):
            action_text = _NARRATORS.get(tool_name, _narrate_generic)(
                params, result, result_data, self._last_state)
            self._turn_actions.append(action_text)

        handler = _NARRATORS.get(tool_name, _narrate_generic)
        text = handler(params, result, result_data, self._last_state)

        self._last_tool = tool_name
        return text

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
        "Defend": "防御", "Buff": "增益", "Debuff": "减益",
        "Sleep": "休眠", "Unknown": "未知", "Stun": "眩晕",
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
) -> str:
    if not parsed:
        return "正在观察当前局势..."

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
        return "在主菜单，等待开始新的一局..."
    if st == "treasure":
        return "发现宝箱！看看里面有什么好东西。"
    if st == "card_select":
        return _narrate_card_select_state(parsed)
    if st == "relic_select":
        return _narrate_relic_select_state(parsed)
    if st == "bundle_select":
        return "需要选择一个卡组包。"
    return f"正在查看游戏画面 ({st})..."


def _narrate_combat_state(data: dict, st: str) -> str:
    battle = data.get("battle", {})
    player = data.get("player", {})
    enemies = battle.get("enemies", [])
    turn = battle.get("turn", "player")
    round_num = battle.get("round", "?")

    if turn == "enemy":
        return "等待敌方回合结束..."

    label = {"monster": "普通战斗", "elite": "精英战", "boss": "BOSS战"}
    lines = [f"[ {label.get(st, '战斗')} - 第{round_num}回合 ]"]

    # Player status
    hp = player.get("hp", "?")
    max_hp = player.get("max_hp", "?")
    energy = player.get("energy", "?")
    hand = player.get("hand", [])
    block = player.get("block", 0)
    status = _hp_status(hp, max_hp) if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)) else ""

    player_line = f"我方: {hp}/{max_hp} HP ({status})"
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
            power_strs.append(f"{name}{'x'+str(amt) if amt else ''}")
        lines.append(f"增益: {', '.join(power_strs)}")

    # Hand cards
    if hand:
        card_names = []
        for c in hand:
            name = c.get("name", "?")
            cost = c.get("cost", "?")
            if c.get("upgraded", False):
                name += "+"
            card_names.append(f"{name}({cost})")
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

        enemy_line = f"敌方 [{name}]: {ehp}/{emax} HP"
        if eblock:
            enemy_line += f" | {eblock}格挡"

        if intents:
            intent_strs = [_format_intent(i) for i in intents]
            enemy_line += f" | 意图: {'+'.join(intent_strs)}"

        # Enemy powers
        epowers = e.get("powers", [])
        if epowers:
            epower_strs = [f"{p.get('name', '?')}{'x'+str(p.get('amount', '')) if p.get('amount') else ''}" for p in epowers]
            enemy_line += f" | {', '.join(epower_strs)}"

        lines.append(enemy_line)

    # Threat assessment
    if total_dmg > 0:
        lines.append("")
        if block >= total_dmg:
            lines.append(f"威胁评估: 预计受到{total_dmg}点伤害，已有{block}格挡足以抵挡")
        else:
            net = total_dmg - block
            lines.append(f"威胁评估: 预计受到{total_dmg}点伤害，需要至少{net}点格挡")
            if isinstance(hp, (int, float)) and net >= hp:
                lines.append("!! 警告: 不格挡将会死亡 !!")

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

    lines = [f"[ 地图 - 第{floor}层 ]"]
    lines.append(f"状态: {hp}/{max_hp} HP | {gold}金币")

    if options:
        lines.append("可选路径:")
        for opt in options:
            ntype = opt.get("type", "???")
            leads = opt.get("leads_to", [])
            path_str = f"  - {ntype}"
            if leads:
                next_types = [l.get("type", "?") for l in leads]
                path_str += f" (通往: {', '.join(next_types)})"
            lines.append(path_str)

    # Strategic hint
    if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)):
        pct = _hp_percentage(hp, max_hp)
        if pct < 50:
            lines.append("")
            lines.append("策略提示: HP较低，优先选择篝火恢复")
        elif pct > 80 and any(o.get("type") in ("elite", "Elite") for o in options):
            lines.append("")
            lines.append("策略提示: HP充足，可以考虑挑战精英获取遗物")

    return "\n".join(lines)


def _narrate_event_state(data: dict) -> str:
    event = data.get("event", {})
    name = event.get("event_name", "未知事件")
    in_dialogue = event.get("in_dialogue", False)

    if in_dialogue:
        return f"遇到事件「{name}」，正在阅读对话..."

    lines = [f"[ 事件: {name} ]"]
    options = event.get("options", [])
    if options:
        lines.append("可选项:")
        for o in options:
            title = o.get("title", "???")
            locked = o.get("is_locked", False)
            desc = o.get("description", "")
            prefix = "[锁定] " if locked else ""
            opt_line = f"  {prefix}[{o.get('index', '?')}] {title}"
            if desc:
                opt_line += f" - {desc}"
            lines.append(opt_line)
    return "\n".join(lines)


def _narrate_rest_state(data: dict) -> str:
    rest = data.get("rest_site", {})
    player = data.get("player", {})
    options = rest.get("options", [])
    hp = player.get("hp", "?")
    max_hp = player.get("max_hp", "?")

    lines = [f"[ 篝火 ] HP: {hp}/{max_hp}"]
    if options:
        opt_names = []
        for o in options:
            name = o.get("name", "???")
            enabled = o.get("is_enabled", True)
            opt_names.append(f"{name}{'(不可用)' if not enabled else ''}")
        lines.append(f"选项: {', '.join(opt_names)}")

    # Strategic hint
    if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)):
        pct = _hp_percentage(hp, max_hp)
        if pct < 60:
            lines.append("建议: HP较低，优先休息恢复")
        else:
            lines.append("建议: HP充足，可以考虑升级卡牌")

    return "\n".join(lines)


def _narrate_shop_state(data: dict) -> str:
    player = data.get("player", {})
    gold = player.get("gold", "?")
    shop = data.get("shop", {})
    items = shop.get("items", [])

    lines = [f"[ 商店 ] 金币: {gold}"]
    if items:
        for item in items:
            name = item.get("name", "???")
            price = item.get("price", item.get("cost", "?"))
            itype = item.get("type", "")
            lines.append(f"  - {name} ({price}金) [{itype}]")
    removal = shop.get("card_removal", {})
    if removal:
        cost = removal.get("cost", removal.get("price", "?"))
        available = removal.get("available", True)
        lines.append(f"  - 移除卡牌 ({cost}金) {'[可用]' if available else '[不可用]'}")

    return "\n".join(lines)


def _narrate_rewards_state(data: dict) -> str:
    rewards = data.get("rewards", {})
    items = rewards.get("items", [])
    if not items:
        return "查看战斗奖励..."

    lines = ["[ 战斗奖励 ]"]
    for item in items:
        desc = item.get("description", item.get("type", "???"))
        lines.append(f"  - {desc}")
    return "\n".join(lines)


def _narrate_card_reward_state(data: dict) -> str:
    cr = data.get("card_reward", {})
    cards = cr.get("cards", [])
    if not cards:
        return "选择卡牌奖励..."

    lines = ["[ 卡牌奖励 ] 需要选择一张卡牌:"]
    for c in cards:
        name = c.get("name", "???")
        cost = c.get("cost", "?")
        rarity = c.get("rarity", "")
        ctype = c.get("type", "")
        desc = c.get("description", "")
        upgraded = "+" if c.get("upgraded", False) else ""

        card_line = f"  [{c.get('index', '?')}] {name}{upgraded} ({cost}费) [{rarity}/{ctype}]"
        if desc:
            card_line += f"\n      {desc}"
        lines.append(card_line)

    return "\n".join(lines)


def _narrate_hand_select_state(data: dict) -> str:
    hs = data.get("hand_select", {})
    prompt = hs.get("prompt", "选择卡牌")
    return f"需要从手牌中选择: {prompt}"


def _narrate_card_select_state(data: dict) -> str:
    cs = data.get("card_select", {})
    screen_type = cs.get("screen_type", "")
    cards = cs.get("cards", [])

    lines = [f"[ 卡牌选择: {screen_type or '从牌组中选择'} ]"]
    if cards:
        for c in cards:
            name = c.get("name", "???")
            upgraded = "+" if c.get("upgraded", False) else ""
            lines.append(f"  - {name}{upgraded}")
    return "\n".join(lines)


def _narrate_relic_select_state(data: dict) -> str:
    rs = data.get("relic_select", {})
    relics = rs.get("relics", [])
    if not relics:
        return "选择遗物..."

    lines = ["[ 遗物选择 ]"]
    for r in relics:
        name = r.get("name", "???")
        desc = r.get("description", "")
        relic_line = f"  - {name}"
        if desc:
            relic_line += f": {desc}"
        lines.append(relic_line)
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
    card_name = _resolve_card_name(idx, state) if isinstance(idx, int) else f"#{idx}"

    parts = [f"出牌: {card_name}"]

    # Card details
    if card:
        cost = card.get("cost", "?")
        parts[0] += f" ({cost}费)"

    # Target info
    if target:
        enemy = _resolve_enemy(target, state)
        enemy_name = enemy.get("name", target) if enemy else target
        parts[0] += f" -> {enemy_name}"
        if enemy:
            ehp = enemy.get("hp", "?")
            emax = enemy.get("max_hp", "?")
            parts[0] += f" [{ehp}/{emax} HP]"

    # Check for error in result
    if parsed and parsed.get("status") == "error":
        error = parsed.get("error", parsed.get("message", "未知错误"))
        parts.append(f"!! 出牌失败: {error}")

    return "\n".join(parts)


def _narrate_end_turn(
    params: dict, result: str, parsed: dict | None, state: dict | None,
    turn_actions: list[str] | None = None,
) -> str:
    lines = ["--- 回合结束 ---"]
    if turn_actions:
        lines.append(f"本回合执行了 {len(turn_actions)} 个操作")
    return "\n".join(lines)


def _narrate_combat_batch(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    actions = params.get("actions", [])
    lines = [f"[ 批量操作: {len(actions)}个动作 ]"]

    for i, action in enumerate(actions):
        atype = action.get("type", "?")
        if atype == "play_card":
            card_name = _resolve_card_name(action.get("card_index", "?"), state) \
                if isinstance(action.get("card_index"), int) else f"#{action.get('card_index', '?')}"
            target = action.get("target")
            line = f"  {i+1}. 出牌: {card_name}"
            if target:
                line += f" -> {_resolve_enemy_name(target, state)}"
            lines.append(line)
        elif atype == "use_potion":
            name = _resolve_potion_name(action.get("slot", 0), state)
            line = f"  {i+1}. 使用药水: {name}"
            if action.get("target"):
                line += f" -> {_resolve_enemy_name(action['target'], state)}"
            lines.append(line)
        elif atype == "end_turn":
            lines.append(f"  {i+1}. 结束回合")
        else:
            lines.append(f"  {i+1}. {atype}")

    # Parse batch result for success/failure
    if result:
        result_lines = result.strip().split("\n")
        for rl in result_lines:
            if rl.strip().startswith("[") and "]" in rl:
                lines.append(f"  {rl.strip()}")

    return "\n".join(lines)


def _narrate_use_potion(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    slot = params.get("slot", "?")
    target = params.get("target")
    potion = _resolve_potion(slot, state) if isinstance(slot, int) else None
    name = potion.get("name", f"#{slot}") if potion else f"#{slot}"

    line = f"使用药水: {name}"
    if target:
        enemy_name = _resolve_enemy_name(target, state)
        line += f" -> {enemy_name}"

    if parsed and parsed.get("status") == "error":
        line += f"\n!! 失败: {parsed.get('error', '未知错误')}"

    return line


def _narrate_discard_potion(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    slot = params.get("slot", "?")
    name = _resolve_potion_name(slot, state) if isinstance(slot, int) else f"#{slot}"
    return f"丢弃药水: {name} (腾出药水槽位)"


def _narrate_proceed(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "前往地图..."


def _narrate_map_choice(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("node_index", params.get("index", "?"))
    if state:
        options = state.get("map", {}).get("next_options", [])
        for opt in options:
            if opt.get("index") == idx:
                ntype = opt.get("type", "???")
                leads = opt.get("leads_to", [])
                line = f"选择路径: 前往{ntype}"
                if leads:
                    next_types = [l.get("type", "?") for l in leads]
                    line += f" (下一步: {', '.join(next_types)})"
                return line
    return f"选择路径 #{idx}"


def _narrate_rest_choice(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("option_index", params.get("index", "?"))
    if state:
        options = state.get("rest_site", {}).get("options", [])
        for opt in options:
            if opt.get("index") == idx:
                name = opt.get("name", "???")
                return f"篝火选择: {name}"
    return "篝火选择..."


def _narrate_shop_buy(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("item_index", params.get("index", "?"))
    if state:
        items = state.get("shop", {}).get("items", [])
        if isinstance(idx, int):
            for item in items:
                if item.get("index") == idx:
                    name = item.get("name", "???")
                    price = item.get("price", item.get("cost", "?"))
                    return f"购买: {name} ({price}金)"
    return f"购买商品 #{idx}"


def _narrate_event_option(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("option_index", params.get("index", "?"))
    if state:
        options = state.get("event", {}).get("options", [])
        for opt in options:
            if opt.get("index") == idx:
                title = opt.get("title", "???")
                if opt.get("is_proceed", False):
                    return "继续..."
                return f"事件选择: 「{title}」"
    return f"事件选择 #{idx}"


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
        for item in items:
            if item.get("index") == idx:
                desc = item.get("description", item.get("type", "???"))
                return f"领取奖励: {desc}"
    return f"领取奖励 #{idx}"


def _narrate_claim_all(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "领取所有非卡牌奖励"


def _narrate_pick_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("card_index", "?")
    if state:
        cards = state.get("card_reward", {}).get("cards", [])
        for card in cards:
            if card.get("index") == idx:
                name = card.get("name", "???")
                rarity = card.get("rarity", "")
                cost = card.get("cost", "?")
                desc = card.get("description", "")
                upgraded = "+" if card.get("upgraded", False) else ""
                line = f"选择卡牌: {name}{upgraded} ({cost}费) [{rarity}]"
                if desc:
                    line += f"\n  效果: {desc}"
                return line
    return f"选择卡牌 #{idx}"


def _narrate_skip_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "跳过卡牌奖励，保持牌组精简"


def _narrate_select_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "选择卡牌..."


def _narrate_confirm(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "确认选择"


def _narrate_cancel(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "取消选择"


def _narrate_relic_select(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("relic_index", params.get("index", "?"))
    if state:
        relics = state.get("relic_select", {}).get("relics", [])
        if isinstance(idx, int) and 0 <= idx < len(relics):
            name = relics[idx].get("name", "???")
            desc = relics[idx].get("description", "")
            line = f"选择遗物: {name}"
            if desc:
                line += f"\n  效果: {desc}"
            return line
    return f"选择遗物 #{idx}"


def _narrate_relic_skip(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "跳过遗物选择"


def _narrate_treasure(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "从宝箱中获取遗物"


def _narrate_crystal_sphere(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "操作水晶球小游戏..."


def _narrate_combat_select_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("card_index", "?")
    card_name = _resolve_card_name(idx, state) if isinstance(idx, int) else f"#{idx}"
    return f"选中: {card_name}"


def _narrate_generic(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "思考中..."


# ---------------------------------------------------------------------------
# Tool name -> handler map
# ---------------------------------------------------------------------------

_NARRATORS: dict[str, Any] = {
    # State queries
    "get_game_state": _narrate_get_state,
    "mp_get_game_state": _narrate_get_state,
    # Combat
    "combat_play_card": _narrate_play_card,
    "mp_combat_play_card": _narrate_play_card,
    "combat_batch": _narrate_combat_batch,
    "combat_end_turn": _narrate_end_turn,
    "mp_combat_end_turn": _narrate_end_turn,
    "mp_combat_undo_end_turn": lambda *a: "撤回结束回合投票",
    "combat_select_card": _narrate_combat_select_card,
    "mp_combat_select_card": _narrate_combat_select_card,
    "combat_confirm_selection": _narrate_confirm,
    "mp_combat_confirm_selection": _narrate_confirm,
    # Potions
    "use_potion": _narrate_use_potion,
    "mp_use_potion": _narrate_use_potion,
    "discard_potion": _narrate_discard_potion,
    "mp_discard_potion": _narrate_discard_potion,
    # Navigation
    "proceed_to_map": _narrate_proceed,
    "mp_proceed_to_map": _narrate_proceed,
    "map_choose_node": _narrate_map_choice,
    "mp_map_vote": _narrate_map_choice,
    # Rest site
    "rest_choose_option": _narrate_rest_choice,
    "mp_rest_choose_option": _narrate_rest_choice,
    # Shop
    "shop_purchase": _narrate_shop_buy,
    "mp_shop_purchase": _narrate_shop_buy,
    # Events
    "event_choose_option": _narrate_event_option,
    "mp_event_choose_option": _narrate_event_option,
    "event_advance_dialogue": _narrate_advance_dialogue,
    "mp_event_advance_dialogue": _narrate_advance_dialogue,
    # Rewards
    "rewards_claim": _narrate_claim_reward,
    "mp_rewards_claim": _narrate_claim_reward,
    "rewards_claim_all": _narrate_claim_all,
    "mp_rewards_claim_all": _narrate_claim_all,
    "rewards_pick_card": _narrate_pick_card,
    "mp_rewards_pick_card": _narrate_pick_card,
    "rewards_skip_card": _narrate_skip_card,
    "mp_rewards_skip_card": _narrate_skip_card,
    # Card selection overlay
    "deck_select_card": _narrate_select_card,
    "mp_deck_select_card": _narrate_select_card,
    "deck_confirm_selection": _narrate_confirm,
    "mp_deck_confirm_selection": _narrate_confirm,
    "deck_cancel_selection": _narrate_cancel,
    "mp_deck_cancel_selection": _narrate_cancel,
    # Bundle
    "bundle_select": lambda *a: "查看卡组包...",
    "mp_bundle_select": lambda *a: "查看卡组包...",
    "bundle_confirm_selection": lambda *a: "选择此卡组包！",
    "mp_bundle_confirm_selection": lambda *a: "选择此卡组包！",
    "bundle_cancel_selection": lambda *a: "查看其他卡组包...",
    "mp_bundle_cancel_selection": lambda *a: "查看其他卡组包...",
    # Relic selection
    "relic_select": _narrate_relic_select,
    "mp_relic_select": _narrate_relic_select,
    "relic_skip": _narrate_relic_skip,
    "mp_relic_skip": _narrate_relic_skip,
    # Treasure
    "treasure_claim_relic": _narrate_treasure,
    "mp_treasure_claim_relic": _narrate_treasure,
    # Crystal sphere
    "crystal_sphere_set_tool": _narrate_crystal_sphere,
    "mp_crystal_sphere_set_tool": _narrate_crystal_sphere,
    "crystal_sphere_click_cell": _narrate_crystal_sphere,
    "mp_crystal_sphere_click_cell": _narrate_crystal_sphere,
    "crystal_sphere_proceed": lambda *a: "水晶球完成",
    "mp_crystal_sphere_proceed": lambda *a: "水晶球完成",
}
