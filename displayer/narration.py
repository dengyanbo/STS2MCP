"""Template-based narration engine.

Translates MCP tool calls + results into human-readable gameplay thinking text.
Caches the last game state to resolve card/enemy names from indices.
"""

from __future__ import annotations

import json
from typing import Any


class NarrationEngine:
    def __init__(self):
        self._last_state: dict | None = None
        self._last_tool: str | None = None

    def narrate(self, tool_name: str, params: dict, result: str) -> str | None:
        """Generate a human-readable narration for a tool call.

        Returns None if the event should be suppressed.
        """
        result_data = self._try_parse_json(result)

        if tool_name in ("get_game_state", "mp_get_game_state") and result_data:
            self._last_state = result_data

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
# State narrators
# ---------------------------------------------------------------------------

def _narrate_get_state(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    if not parsed:
        return "Checking the current situation..."

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
        return "At the main menu. Waiting for a new run..."
    if st == "treasure":
        return "Found a treasure chest! Let me see what's inside."
    if st == "card_select":
        return _narrate_card_select_state(parsed)
    if st == "relic_select":
        return _narrate_relic_select_state(parsed)
    if st == "bundle_select":
        return "Time to choose a card bundle."

    return f"Looking at the game screen ({st})..."


def _narrate_combat_state(data: dict, st: str) -> str:
    battle = data.get("battle", {})
    player = data.get("player", {})
    enemies = battle.get("enemies", [])
    turn = battle.get("turn", "player")
    round_num = battle.get("round", "?")

    if turn == "enemy":
        return "Waiting for enemies to finish their turn..."

    label = {"monster": "combat", "elite": "elite fight", "boss": "BOSS fight"}
    parts = [f"⚔️ Round {round_num} of {label.get(st, 'combat')}."]

    if enemies:
        enemy_parts = []
        for e in enemies:
            name = e.get("name", e.get("entity_id", "???"))
            hp = e.get("hp", "?")
            max_hp = e.get("max_hp", "?")
            intents = e.get("intents", [])
            intent_str = ""
            if intents:
                first = intents[0]
                itype = first.get("type", "")
                ilabel = first.get("label", "")
                if itype == "Attack" and ilabel:
                    intent_str = f", attacking for {ilabel}"
                elif itype:
                    intent_str = f", {itype.lower()}"
            enemy_parts.append(f"{name} ({hp}/{max_hp} HP{intent_str})")
        parts.append("Facing: " + "; ".join(enemy_parts) + ".")

    hp = player.get("hp", "?")
    max_hp = player.get("max_hp", "?")
    energy = player.get("energy", "?")
    hand = player.get("hand", [])
    block = player.get("block", 0)

    player_str = f"I have {hp}/{max_hp} HP"
    if block:
        player_str += f", {block} block"
    player_str += f", {energy} energy, and {len(hand)} cards in hand."
    parts.append(player_str)

    return " ".join(parts)


def _narrate_map_state(data: dict) -> str:
    map_data = data.get("map", {})
    options = map_data.get("next_options", [])
    if not options:
        return "🗺️ Looking at the map..."

    node_types = [opt.get("type", "???") for opt in options]
    run = data.get("run", {})
    floor = run.get("floor", "?")
    return f"🗺️ Floor {floor}. I can go to: {', '.join(node_types)}."


def _narrate_event_state(data: dict) -> str:
    event = data.get("event", {})
    name = event.get("event_name", "an event")
    in_dialogue = event.get("in_dialogue", False)
    if in_dialogue:
        return f"📜 Encountered {name}. Reading the dialogue..."

    options = event.get("options", [])
    if options:
        opt_texts = [
            o.get("title", f"Option {o.get('index', '?')}")
            for o in options
            if not o.get("is_locked", False)
        ]
        return f"📜 {name} — Options: {', '.join(opt_texts)}."
    return f"📜 Encountered {name}."


def _narrate_rest_state(data: dict) -> str:
    rest = data.get("rest_site", {})
    options = rest.get("options", [])
    if options:
        opt_names = [o.get("name", "???") for o in options if o.get("is_enabled", True)]
        return f"🔥 At a campfire. I can: {', '.join(opt_names)}."
    return "🔥 At a campfire."


def _narrate_shop_state(data: dict) -> str:
    player = data.get("player", {})
    gold = player.get("gold", "?")
    return f"🏪 Browsing the shop with {gold} gold."


def _narrate_rewards_state(data: dict) -> str:
    rewards = data.get("rewards", {})
    items = rewards.get("items", [])
    if items:
        descs = [i.get("description", i.get("type", "???")) for i in items]
        return f"🎁 Rewards available: {'; '.join(descs)}."
    return "🎁 Checking rewards."


def _narrate_card_reward_state(data: dict) -> str:
    cr = data.get("card_reward", {})
    cards = cr.get("cards", [])
    if cards:
        names = [c.get("name", "???") for c in cards]
        return f"🃏 Card reward — choosing from: {', '.join(names)}."
    return "🃏 Choosing a card reward."


def _narrate_hand_select_state(data: dict) -> str:
    hs = data.get("hand_select", {})
    prompt = hs.get("prompt", "Select a card")
    return f"✋ {prompt}"


def _narrate_card_select_state(data: dict) -> str:
    cs = data.get("card_select", {})
    screen_type = cs.get("screen_type", "")
    if screen_type:
        return f"🃏 Card selection: {screen_type}."
    return "🃏 Selecting cards from my deck."


def _narrate_relic_select_state(data: dict) -> str:
    rs = data.get("relic_select", {})
    relics = rs.get("relics", [])
    if relics:
        names = [r.get("name", "???") for r in relics]
        return f"💎 Choosing a relic from: {', '.join(names)}."
    return "💎 Choosing a relic."


# ---------------------------------------------------------------------------
# Action narrators
# ---------------------------------------------------------------------------

def _resolve_card_name(card_index: int, state: dict | None) -> str:
    if not state:
        return f"card #{card_index}"
    hand = state.get("player", {}).get("hand", [])
    for card in hand:
        if card.get("index") == card_index:
            return card.get("name", f"card #{card_index}")
    if 0 <= card_index < len(hand):
        return hand[card_index].get("name", f"card #{card_index}")
    return f"card #{card_index}"


def _resolve_enemy_name(entity_id: str, state: dict | None) -> str:
    if not state:
        return entity_id
    enemies = state.get("battle", {}).get("enemies", [])
    for e in enemies:
        if e.get("entity_id") == entity_id:
            return e.get("name", entity_id)
    return entity_id


def _resolve_potion_name(slot: int, state: dict | None) -> str:
    if not state:
        return f"potion (slot {slot})"
    potions = state.get("player", {}).get("potions", [])
    for p in potions:
        if p.get("slot") == slot:
            return p.get("name", f"potion (slot {slot})")
    return f"potion (slot {slot})"


def _narrate_play_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("card_index", "?")
    target = params.get("target")
    card_name = _resolve_card_name(idx, state) if isinstance(idx, int) else f"card #{idx}"

    if target:
        enemy_name = _resolve_enemy_name(target, state)
        return f"Playing {card_name} on {enemy_name}."
    return f"Playing {card_name}."


def _narrate_end_turn(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "Ending my turn."


def _narrate_use_potion(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    slot = params.get("slot", "?")
    target = params.get("target")
    name = _resolve_potion_name(slot, state) if isinstance(slot, int) else "a potion"

    if target:
        enemy_name = _resolve_enemy_name(target, state)
        return f"🧪 Using {name} on {enemy_name}."
    return f"🧪 Using {name}."


def _narrate_discard_potion(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    slot = params.get("slot", "?")
    name = _resolve_potion_name(slot, state) if isinstance(slot, int) else "a potion"
    return f"Discarding {name} to make room."


def _narrate_proceed(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "Moving on."


def _narrate_map_choice(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("index", "?")
    if state:
        options = state.get("map", {}).get("next_options", [])
        for opt in options:
            if opt.get("index") == idx:
                ntype = opt.get("type", "???")
                return f"🗺️ Heading to the {ntype}."
    return f"🗺️ Choosing map path #{idx}."


def _narrate_rest_choice(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("index", "?")
    if state:
        options = state.get("rest_site", {}).get("options", [])
        for opt in options:
            if opt.get("index") == idx:
                name = opt.get("name", "???")
                return f"🔥 Choosing to {name.lower()}."
    return "🔥 Making a campfire choice."


def _narrate_shop_buy(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "🏪 Purchasing an item from the shop."


def _narrate_event_option(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("index", "?")
    if state:
        options = state.get("event", {}).get("options", [])
        for opt in options:
            if opt.get("index") == idx:
                title = opt.get("title", "???")
                if opt.get("is_proceed", False):
                    return "Proceeding..."
                return f"📜 Choosing: \"{title}\"."
    return f"📜 Making event choice #{idx}."


def _narrate_advance_dialogue(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "📜 Reading on..."


def _narrate_claim_reward(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("index", "?")
    if state:
        items = state.get("rewards", {}).get("items", [])
        for item in items:
            if item.get("index") == idx:
                desc = item.get("description", item.get("type", "???"))
                return f"🎁 Claiming: {desc}"
    return f"🎁 Claiming reward #{idx}."


def _narrate_pick_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("card_index", "?")
    if state:
        cards = state.get("card_reward", {}).get("cards", [])
        for card in cards:
            if card.get("index") == idx:
                name = card.get("name", "???")
                return f"🃏 Adding {name} to my deck!"
    return f"🃏 Picking card #{idx}."


def _narrate_skip_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "🃏 Skipping the card reward. Keeping my deck lean."


def _narrate_select_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "Selecting a card."


def _narrate_confirm(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "Confirming selection."


def _narrate_cancel(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "Cancelling."


def _narrate_relic_select(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("index", "?")
    if state:
        relics = state.get("relic_select", {}).get("relics", [])
        if isinstance(idx, int) and 0 <= idx < len(relics):
            name = relics[idx].get("name", "???")
            return f"💎 Choosing the {name} relic."
    return f"💎 Choosing relic #{idx}."


def _narrate_relic_skip(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "💎 Skipping the relic selection."


def _narrate_treasure(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "💎 Taking a relic from the treasure chest."


def _narrate_crystal_sphere(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "🔮 Playing the Crystal Sphere minigame."


def _narrate_combat_select_card(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    idx = params.get("card_index", "?")
    card_name = _resolve_card_name(idx, state) if isinstance(idx, int) else f"card #{idx}"
    return f"Selecting {card_name}."


def _narrate_generic(
    params: dict, result: str, parsed: dict | None, state: dict | None
) -> str:
    return "Thinking..."


# Map MCP tool function names → narration handlers
_NARRATORS: dict[str, Any] = {
    # State queries
    "get_game_state": _narrate_get_state,
    "mp_get_game_state": _narrate_get_state,
    # Combat
    "combat_play_card": _narrate_play_card,
    "mp_combat_play_card": _narrate_play_card,
    "combat_end_turn": _narrate_end_turn,
    "mp_combat_end_turn": _narrate_end_turn,
    "mp_combat_undo_end_turn": lambda *a: "Retracting my end-turn vote.",
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
    "bundle_select": lambda *a: "Looking at a card bundle.",
    "mp_bundle_select": lambda *a: "Looking at a card bundle.",
    "bundle_confirm_selection": lambda *a: "Choosing this card bundle!",
    "mp_bundle_confirm_selection": lambda *a: "Choosing this card bundle!",
    "bundle_cancel_selection": lambda *a: "Looking at other bundles.",
    "mp_bundle_cancel_selection": lambda *a: "Looking at other bundles.",
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
    "crystal_sphere_proceed": lambda *a: "🔮 Done with the Crystal Sphere.",
    "mp_crystal_sphere_proceed": lambda *a: "🔮 Done with the Crystal Sphere.",
}
