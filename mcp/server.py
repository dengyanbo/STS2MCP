"""MCP server bridge for Slay the Spire 2.

Connects to the STS2_MCP mod's HTTP server and exposes game actions
as MCP tools for Claude Desktop / Claude Code.
"""

import argparse
import asyncio
import atexit
import functools
import json
import logging
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP

# Suppress noisy httpx request logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

mcp = FastMCP("sts2")

_base_url: str = "http://localhost:15526"
_trust_env: bool = True
_displayer_url: str = "http://localhost:15580"
_displayer_enabled: bool = True


# ---------------------------------------------------------------------------
# Displayer integration — fire-and-forget notifications to the dashboard
# ---------------------------------------------------------------------------

async def _notify_displayer(tool_name: str, params: dict, result: str) -> None:
    """Post a tool-call event to the displayer dashboard (best-effort)."""
    if not _displayer_enabled:
        return
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            await client.post(
                f"{_displayer_url}/api/events",
                json={"tool": tool_name, "params": params, "result": result},
            )
    except Exception:
        pass  # Displayer is optional — never block gameplay


# Wrap mcp.tool so every registered tool auto-notifies the displayer.
_orig_mcp_tool = mcp.tool


def _instrumented_tool(*deco_args, **deco_kwargs):
    orig_decorator = _orig_mcp_tool(*deco_args, **deco_kwargs)

    def wrapper(func):
        @functools.wraps(func)
        async def instrumented(**kwargs):
            result = await func(**kwargs)
            asyncio.create_task(
                _notify_displayer(func.__name__, kwargs, result)
            )
            return result
        return orig_decorator(instrumented)
    return wrapper


mcp.tool = _instrumented_tool


def _sp_url() -> str:
    return f"{_base_url}/api/v1/singleplayer"


def _mp_url() -> str:
    return f"{_base_url}/api/v1/multiplayer"


async def _get(params: dict | None = None) -> str:
    async with httpx.AsyncClient(timeout=10, trust_env=_trust_env) as client:
        r = await client.get(_sp_url(), params=params)
        r.raise_for_status()
        return r.text


async def _post(body: dict) -> str:
    async with httpx.AsyncClient(timeout=10, trust_env=_trust_env) as client:
        r = await client.post(
            _sp_url(), json=body, params={"format": "markdown"}
        )
        r.raise_for_status()
        return _format_action_response(r.text)


async def _raw_post(body: dict) -> str:
    """Post without formatting — returns raw JSON for internal parsing."""
    async with httpx.AsyncClient(timeout=10, trust_env=_trust_env) as client:
        r = await client.post(_sp_url(), json=body)
        r.raise_for_status()
        return r.text


async def _mp_get(params: dict | None = None) -> str:
    async with httpx.AsyncClient(timeout=10, trust_env=_trust_env) as client:
        r = await client.get(_mp_url(), params=params)
        r.raise_for_status()
        return r.text


async def _mp_post(body: dict) -> str:
    async with httpx.AsyncClient(timeout=10, trust_env=_trust_env) as client:
        r = await client.post(
            _mp_url(), json=body, params={"format": "markdown"}
        )
        r.raise_for_status()
        return _format_action_response(r.text)


def _format_action_response(text: str) -> str:
    """Extract the embedded game_state_markdown from action responses.

    The mod now returns game state alongside every successful action.
    If a markdown-formatted state is present, append it so the caller
    sees the updated state without a separate get_game_state() call.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text

    parts: list[str] = []

    # Action result summary
    status = data.get("status", "")
    message = data.get("message", "")
    if status == "error":
        error = data.get("error", message)
        return f"Error: {error}"
    if message:
        parts.append(f"✓ {message}")

    # Append markdown state if available
    md = data.get("game_state_markdown")
    if md:
        parts.append("")
        parts.append(md)
    elif "game_state" in data:
        # Fallback: return raw JSON state (shouldn't happen normally)
        parts.append("")
        parts.append(json.dumps(data["game_state"], indent=2, ensure_ascii=False))

    return "\n".join(parts) if parts else text


def _handle_error(e: Exception) -> str:
    if isinstance(e, httpx.ConnectError):
        return "Error: Cannot connect to STS2_MCP mod. Is the game running with the mod enabled?"
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: HTTP {e.response.status_code} — {e.response.text}"
    return f"Error: {e}"


# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_game_state(format: str = "markdown") -> str:
    """Get the current Slay the Spire 2 game state.

    Returns the full game state including player stats, hand, enemies, potions, etc.
    The state_type field indicates the current screen (combat, map, event, shop,
    fake_merchant, etc.).

    Args:
        format: "markdown" for human-readable output, "json" for structured data.
    """
    try:
        return await _get({"format": format})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def use_potion(slot: int, target: str | None = None) -> str:
    """Use a potion from the player's potion slots.

    Works both during and outside of combat. Combat-only potions require an active battle.

    Args:
        slot: Potion slot index (as shown in game state).
        target: Entity ID of the target enemy (e.g. "JAW_WORM_0"). Required for enemy-targeted potions.
    """
    body: dict = {"action": "use_potion", "slot": slot}
    if target is not None:
        body["target"] = target
    try:
        return await _post(body)
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def discard_potion(slot: int) -> str:
    """Discard a potion from the player's potion slots to free up space.

    Use this when all potion slots are full and you need room for incoming potions
    (e.g. before collecting a potion reward).

    Args:
        slot: Potion slot index to discard (as shown in game state).
    """
    try:
        return await _post({"action": "discard_potion", "slot": slot})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def proceed_to_map() -> str:
    """Proceed from the current screen to the map.

    Works from: rewards screen, rest site, shop, fake merchant.
    Does NOT work for events — use event_choose_option() with the Proceed option's index.
    """
    try:
        return await _post({"action": "proceed"})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Combat (state_type: monster / elite / boss)
# ---------------------------------------------------------------------------


@mcp.tool()
async def combat_play_card(card_index: int, target: str | None = None) -> str:
    """[Combat] Play a card from the player's hand.

    Args:
        card_index: Index of the card in hand (0-based, as shown in game state).
        target: Entity ID of the target enemy (e.g. "JAW_WORM_0"). Required for single-target cards.

    Note that the index can change as cards are played - playing a card will shift the indices of remaining cards in hand.
    Refer to the latest game state for accurate indices. New cards are drawn to the right, so playing cards from right to left can help maintain more stable indices for remaining cards.
    """
    body: dict = {"action": "play_card", "card_index": card_index}
    if target is not None:
        body["target"] = target
    try:
        return await _post(body)
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def combat_end_turn() -> str:
    """[Combat] End the player's current turn.

    Automatically waits for the enemy turn to complete and returns the
    next player turn state, so you do NOT need to call get_game_state()
    afterwards.
    """
    try:
        result_text = await _post({"action": "end_turn"})
    except Exception as e:
        return _handle_error(e)

    # The mod already embeds game_state in the response via _post.
    # However, the state captured right after end_turn may still show
    # the enemy turn in progress. Poll until we see the next player
    # turn (is_play_phase=true) or a non-combat state (combat ended).
    try:
        for _ in range(20):  # up to ~5 seconds
            await asyncio.sleep(0.25)
            raw = await _get({"format": "json"})
            data = json.loads(raw)
            state_type = data.get("state_type", "")

            # Combat ended → rewards, map, event, etc.
            if state_type not in ("monster", "elite", "boss"):
                return await _get({"format": "markdown"})

            # Still in combat — check if it's the player's turn again
            battle = data.get("battle", {})
            if battle.get("is_play_phase", False):
                return await _get({"format": "markdown"})
    except Exception:
        pass  # polling failed, return what we have

    # Fallback: return whatever state we have now
    try:
        return await _get({"format": "markdown"})
    except Exception:
        return result_text


@mcp.tool()
async def combat_batch(actions: list[dict]) -> str:
    """[Combat] Execute multiple combat actions in a single call.

    Plays cards, uses potions, and optionally ends the turn — all in one
    round-trip. Each action is executed sequentially with a short delay
    to let the game process animations. Card indices are based on the
    hand state at execution time, NOT the original state — the tool
    re-reads the hand after each action to resolve correct indices by
    card name.

    Args:
        actions: Ordered list of action dicts. Each dict has:
            - type: "play_card" | "use_potion" | "end_turn"
            - card_index: (play_card) 0-based index in current hand
            - target: (play_card/use_potion) entity_id if needed
            - slot: (use_potion) potion slot index

    Example: [
        {"type": "use_potion", "slot": 0},
        {"type": "play_card", "card_index": 4, "target": "JAW_WORM_0"},
        {"type": "play_card", "card_index": 2},
        {"type": "end_turn"}
    ]
    """
    results: list[str] = []

    for i, action in enumerate(actions):
        action_type = action.get("type", "")

        try:
            if action_type == "play_card":
                body: dict = {
                    "action": "play_card",
                    "card_index": action["card_index"],
                }
                if "target" in action:
                    body["target"] = action["target"]
                raw = await _raw_post(body)
            elif action_type == "use_potion":
                body = {"action": "use_potion", "slot": action["slot"]}
                if "target" in action:
                    body["target"] = action["target"]
                raw = await _raw_post(body)
            elif action_type == "end_turn":
                raw = await _raw_post({"action": "end_turn"})
            else:
                results.append(f"[{i}] ✗ Unknown action type: {action_type}")
                continue

            data = json.loads(raw)
            status = data.get("status", "error")
            msg = data.get("message", data.get("error", ""))
            if status == "ok":
                results.append(f"[{i}] ✓ {msg}")
            else:
                results.append(f"[{i}] ✗ {msg}")
                break  # stop on first error

        except Exception as e:
            results.append(f"[{i}] ✗ {_handle_error(e)}")
            break

        # Short delay to let the game process the action
        if action_type != "end_turn" and i < len(actions) - 1:
            await asyncio.sleep(0.15)

    # If the last action was end_turn, wait for enemy turn to complete
    if actions and actions[-1].get("type") == "end_turn":
        try:
            for _ in range(20):
                await asyncio.sleep(0.25)
                raw = await _get({"format": "json"})
                data = json.loads(raw)
                st = data.get("state_type", "")
                if st not in ("monster", "elite", "boss"):
                    break
                if data.get("battle", {}).get("is_play_phase", False):
                    break
        except Exception:
            pass

    # Get final state
    try:
        state = await _get({"format": "markdown"})
    except Exception:
        state = "(Could not fetch final state)"

    return "\n".join(results) + "\n\n" + state


# ---------------------------------------------------------------------------
# In-Combat Card Selection (state_type: hand_select)
# ---------------------------------------------------------------------------


@mcp.tool()
async def combat_select_card(card_index: int) -> str:
    """[Combat Selection] Select a card from hand during an in-combat card selection prompt.

    Used when a card effect asks you to select a card to exhaust, discard, etc.
    This is different from deck_select_card which handles out-of-combat card selection overlays.

    Args:
        card_index: 0-based index of the card in the selectable hand cards (as shown in game state).
    """
    try:
        return await _post({"action": "combat_select_card", "card_index": card_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def combat_confirm_selection() -> str:
    """[Combat Selection] Confirm the in-combat card selection.

    After selecting the required number of cards from hand (exhaust, discard, etc.),
    use this to confirm the selection. Only works when the confirm button is enabled.
    """
    try:
        return await _post({"action": "combat_confirm_selection"})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Rewards (state_type: rewards / card_reward)
# ---------------------------------------------------------------------------


@mcp.tool()
async def rewards_claim(reward_index: int) -> str:
    """[Rewards] Claim a reward from the post-combat rewards screen.

    Gold, potion, and relic rewards are claimed immediately.
    Card rewards open the card selection screen (state changes to card_reward).

    Args:
        reward_index: 0-based index of the reward on the rewards screen.

    Note that claiming a reward may change the indices of remaining rewards, so refer to the latest game state for accurate indices.
    Claiming from right to left can help maintain more stable indices for remaining rewards, as rewards will always shift left to fill in gaps.
    """
    try:
        return await _post({"action": "claim_reward", "index": reward_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def rewards_pick_card(card_index: int) -> str:
    """[Rewards] Select a card from the card reward selection screen.

    Args:
        card_index: 0-based index of the card to add to the deck.
    """
    try:
        return await _post({"action": "select_card_reward", "card_index": card_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def rewards_skip_card() -> str:
    """[Rewards] Skip the card reward without selecting a card."""
    try:
        return await _post({"action": "skip_card_reward"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def rewards_claim_all() -> str:
    """[Rewards] Claim all non-card rewards (gold, relics, potions) in one call.

    Automatically claims all available rewards from right-to-left to avoid
    index shifting issues. Stops if a card reward is encountered (you must
    handle card selection separately via rewards_pick_card or rewards_skip_card).

    Returns the final state, which may be:
    - card_reward: if there's a card reward to select/skip
    - rewards: if some rewards remain
    - map: if all rewards were claimed and proceed was triggered
    """
    claimed: list[str] = []

    for _ in range(10):  # safety limit
        try:
            raw = await _get({"format": "json"})
            data = json.loads(raw)
        except Exception as e:
            return _handle_error(e)

        state_type = data.get("state_type", "")

        if state_type == "card_reward":
            # Stop — card reward needs manual decision
            claimed.append("→ Card reward awaiting selection")
            break

        if state_type != "rewards":
            break  # no longer on rewards screen

        items = data.get("rewards", {}).get("items", [])
        if not items:
            break

        # Claim from highest index to lowest (right-to-left)
        idx = items[-1].get("index", len(items) - 1)
        reward_type = items[-1].get("type", "unknown")
        try:
            result_raw = await _raw_post({"action": "claim_reward", "index": idx})
            result = json.loads(result_raw)
            if result.get("status") == "ok":
                claimed.append(f"✓ Claimed {reward_type}: {result.get('message', '')}")
            else:
                claimed.append(f"✗ {result.get('error', result.get('message', ''))}")
                break
        except Exception as e:
            claimed.append(f"✗ {_handle_error(e)}")
            break

        await asyncio.sleep(0.1)

    # Get final state
    try:
        state = await _get({"format": "markdown"})
    except Exception:
        state = "(Could not fetch final state)"

    return "\n".join(claimed) + "\n\n" + state


# ---------------------------------------------------------------------------
# Map (state_type: map)
# ---------------------------------------------------------------------------


@mcp.tool()
async def map_choose_node(node_index: int) -> str:
    """[Map] Choose a map node to travel to.

    Args:
        node_index: 0-based index of the node from the next_options list.
    """
    try:
        return await _post({"action": "choose_map_node", "index": node_index})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Rest Site (state_type: rest_site)
# ---------------------------------------------------------------------------


@mcp.tool()
async def rest_choose_option(option_index: int) -> str:
    """[Rest Site] Choose a rest site option (rest, smith, etc.).

    Args:
        option_index: 0-based index of the option from the rest site state.
    """
    try:
        return await _post({"action": "choose_rest_option", "index": option_index})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Shop (state_type: shop)
# ---------------------------------------------------------------------------


@mcp.tool()
async def shop_purchase(item_index: int) -> str:
    """[Shop / Fake Merchant] Purchase an item from the shop.

    Works for both regular shops (state_type: shop) and the fake merchant
    event (state_type: fake_merchant). The fake merchant only sells relics.

    Args:
        item_index: 0-based index of the item from the shop state.
    """
    try:
        return await _post({"action": "shop_purchase", "index": item_index})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Event (state_type: event)
# ---------------------------------------------------------------------------


@mcp.tool()
async def event_choose_option(option_index: int) -> str:
    """[Event] Choose an event option.

    Works for both regular events and ancients (after dialogue ends).
    Also used to click the Proceed option after an event resolves.

    Args:
        option_index: 0-based index of the unlocked option.
    """
    try:
        return await _post({"action": "choose_event_option", "index": option_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def event_advance_dialogue() -> str:
    """[Event] Advance ancient event dialogue.

    Click through dialogue text in ancient events. Call repeatedly until options appear.
    """
    try:
        return await _post({"action": "advance_dialogue"})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Card Selection (state_type: card_select)
# ---------------------------------------------------------------------------


@mcp.tool()
async def deck_select_card(card_index: int) -> str:
    """[Card Selection] Select or deselect a card in the card selection screen.

    Used when the game asks you to choose cards from your deck (transform, upgrade,
    remove, discard) or pick a card from offered choices (potions, effects).

    For deck selections: toggles card selection. For choose-a-card: picks immediately.

    Args:
        card_index: 0-based index of the card (as shown in game state).
    """
    try:
        return await _post({"action": "select_card", "index": card_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def deck_confirm_selection() -> str:
    """[Card Selection] Confirm the current card selection.

    After selecting the required number of cards, use this to confirm.
    If a preview is showing (e.g., transform preview), this confirms the preview.
    Not needed for choose-a-card screens where picking is immediate.
    """
    try:
        return await _post({"action": "confirm_selection"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def deck_cancel_selection() -> str:
    """[Card Selection] Cancel the current card selection.

    If a preview is showing, goes back to the selection grid.
    For choose-a-card screens, clicks the skip button (if available).
    Otherwise, closes the card selection screen (only if cancellation is allowed).
    """
    try:
        return await _post({"action": "cancel_selection"})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Bundle Selection (state_type: bundle_select)
# ---------------------------------------------------------------------------


@mcp.tool()
async def bundle_select(bundle_index: int) -> str:
    """[Bundle Selection] Open a bundle preview.

    Args:
        bundle_index: 0-based index of the bundle.
    """
    try:
        return await _post({"action": "select_bundle", "index": bundle_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def bundle_confirm_selection() -> str:
    """[Bundle Selection] Confirm the currently previewed bundle."""
    try:
        return await _post({"action": "confirm_bundle_selection"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def bundle_cancel_selection() -> str:
    """[Bundle Selection] Cancel the current bundle preview."""
    try:
        return await _post({"action": "cancel_bundle_selection"})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Relic Selection (state_type: relic_select)
# ---------------------------------------------------------------------------


@mcp.tool()
async def relic_select(relic_index: int) -> str:
    """[Relic Selection] Select a relic from the relic selection screen.

    Used when the game offers a choice of relics (e.g., boss relic rewards).

    Args:
        relic_index: 0-based index of the relic (as shown in game state).
    """
    try:
        return await _post({"action": "select_relic", "index": relic_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def relic_skip() -> str:
    """[Relic Selection] Skip the relic selection without choosing a relic."""
    try:
        return await _post({"action": "skip_relic_selection"})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Treasure (state_type: treasure)
# ---------------------------------------------------------------------------


@mcp.tool()
async def treasure_claim_relic(relic_index: int) -> str:
    """[Treasure] Claim a relic from the treasure chest.

    The chest is auto-opened when entering the treasure room.
    After claiming, use proceed_to_map() to continue.

    Args:
        relic_index: 0-based index of the relic (as shown in game state).
    """
    try:
        return await _post({"action": "claim_treasure_relic", "index": relic_index})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Crystal Sphere (state_type: crystal_sphere)
# ---------------------------------------------------------------------------


@mcp.tool()
async def crystal_sphere_set_tool(tool: str) -> str:
    """[Crystal Sphere] Switch the active divination tool.

    Args:
        tool: Either "big" or "small".
    """
    try:
        return await _post({"action": "crystal_sphere_set_tool", "tool": tool})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def crystal_sphere_click_cell(x: int, y: int) -> str:
    """[Crystal Sphere] Click a hidden cell on the Crystal Sphere grid.

    Args:
        x: Cell x-coordinate.
        y: Cell y-coordinate.
    """
    try:
        return await _post({"action": "crystal_sphere_click_cell", "x": x, "y": y})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def crystal_sphere_proceed() -> str:
    """[Crystal Sphere] Continue after the Crystal Sphere minigame finishes."""
    try:
        return await _post({"action": "crystal_sphere_proceed"})
    except Exception as e:
        return _handle_error(e)


# ===========================================================================
# MULTIPLAYER tools — all route through /api/v1/multiplayer
# ===========================================================================


@mcp.tool()
async def mp_get_game_state(format: str = "markdown") -> str:
    """[Multiplayer] Get the current multiplayer game state.

    Returns full game state for ALL players: HP, powers, relics, potions,
    plus multiplayer-specific data: map votes, event votes, treasure bids,
    end-turn ready status. Only works during a multiplayer run.

    Args:
        format: "markdown" for human-readable output, "json" for structured data.
    """
    try:
        return await _mp_get({"format": format})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_combat_play_card(card_index: int, target: str | None = None) -> str:
    """[Multiplayer Combat] Play a card from the local player's hand.

    Same as singleplayer combat_play_card but routed through the multiplayer
    endpoint for sync safety.

    Args:
        card_index: Index of the card in hand (0-based).
        target: Entity ID of the target enemy (e.g. "JAW_WORM_0"). Required for single-target cards.
    """
    body: dict = {"action": "play_card", "card_index": card_index}
    if target is not None:
        body["target"] = target
    try:
        return await _mp_post(body)
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_combat_end_turn() -> str:
    """[Multiplayer Combat] Submit end-turn vote.

    In multiplayer, ending the turn is a VOTE — the turn only ends when ALL
    players have submitted. Use mp_combat_undo_end_turn() to retract.
    """
    try:
        return await _mp_post({"action": "end_turn"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_combat_undo_end_turn() -> str:
    """[Multiplayer Combat] Retract end-turn vote.

    If you submitted end turn but want to play more cards, use this to undo.
    Only works if other players haven't all committed yet.
    """
    try:
        return await _mp_post({"action": "undo_end_turn"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_use_potion(slot: int, target: str | None = None) -> str:
    """[Multiplayer] Use a potion from the local player's potion slots.

    Args:
        slot: Potion slot index (as shown in game state).
        target: Entity ID of the target enemy. Required for enemy-targeted potions.
    """
    body: dict = {"action": "use_potion", "slot": slot}
    if target is not None:
        body["target"] = target
    try:
        return await _mp_post(body)
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_discard_potion(slot: int) -> str:
    """[Multiplayer] Discard a potion from the local player's potion slots to free up space.

    Args:
        slot: Potion slot index to discard (as shown in game state).
    """
    try:
        return await _mp_post({"action": "discard_potion", "slot": slot})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_map_vote(node_index: int) -> str:
    """[Multiplayer Map] Vote for a map node to travel to.

    In multiplayer, map selection is a vote — travel happens when all players
    agree. Re-voting for the same node sends a ping to other players.

    Args:
        node_index: 0-based index of the node from the next_options list.
    """
    try:
        return await _mp_post({"action": "choose_map_node", "index": node_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_event_choose_option(option_index: int) -> str:
    """[Multiplayer Event] Choose or vote for an event option.

    For shared events: this is a vote (resolves when all players vote).
    For individual events: immediate choice, same as singleplayer.

    Args:
        option_index: 0-based index of the unlocked option.
    """
    try:
        return await _mp_post({"action": "choose_event_option", "index": option_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_event_advance_dialogue() -> str:
    """[Multiplayer Event] Advance ancient event dialogue."""
    try:
        return await _mp_post({"action": "advance_dialogue"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_rest_choose_option(option_index: int) -> str:
    """[Multiplayer Rest Site] Choose a rest site option (rest, smith, etc.).

    Per-player choice — no voting needed.

    Args:
        option_index: 0-based index of the option.
    """
    try:
        return await _mp_post({"action": "choose_rest_option", "index": option_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_shop_purchase(item_index: int) -> str:
    """[Multiplayer Shop] Purchase an item from the shop.

    Per-player inventory — no voting needed.

    Args:
        item_index: 0-based index of the item.
    """
    try:
        return await _mp_post({"action": "shop_purchase", "index": item_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_rewards_claim(reward_index: int) -> str:
    """[Multiplayer Rewards] Claim a reward from the post-combat rewards screen.

    Args:
        reward_index: 0-based index of the reward.
    """
    try:
        return await _mp_post({"action": "claim_reward", "index": reward_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_rewards_pick_card(card_index: int) -> str:
    """[Multiplayer Rewards] Select a card from the card reward screen.

    Args:
        card_index: 0-based index of the card to add to the deck.
    """
    try:
        return await _mp_post({"action": "select_card_reward", "card_index": card_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_rewards_skip_card() -> str:
    """[Multiplayer Rewards] Skip the card reward."""
    try:
        return await _mp_post({"action": "skip_card_reward"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_proceed_to_map() -> str:
    """[Multiplayer] Proceed from the current screen to the map.

    Works from: rewards screen, rest site, shop.
    """
    try:
        return await _mp_post({"action": "proceed"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_deck_select_card(card_index: int) -> str:
    """[Multiplayer Card Selection] Select or deselect a card in the card selection screen.

    Args:
        card_index: 0-based index of the card.
    """
    try:
        return await _mp_post({"action": "select_card", "index": card_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_deck_confirm_selection() -> str:
    """[Multiplayer Card Selection] Confirm the current card selection."""
    try:
        return await _mp_post({"action": "confirm_selection"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_deck_cancel_selection() -> str:
    """[Multiplayer Card Selection] Cancel the current card selection."""
    try:
        return await _mp_post({"action": "cancel_selection"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_bundle_select(bundle_index: int) -> str:
    """[Multiplayer Bundle Selection] Open a bundle preview.

    Args:
        bundle_index: 0-based index of the bundle.
    """
    try:
        return await _mp_post({"action": "select_bundle", "index": bundle_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_bundle_confirm_selection() -> str:
    """[Multiplayer Bundle Selection] Confirm the currently previewed bundle."""
    try:
        return await _mp_post({"action": "confirm_bundle_selection"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_bundle_cancel_selection() -> str:
    """[Multiplayer Bundle Selection] Cancel the current bundle preview."""
    try:
        return await _mp_post({"action": "cancel_bundle_selection"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_combat_select_card(card_index: int) -> str:
    """[Multiplayer Combat Selection] Select a card from hand during in-combat card selection.

    Args:
        card_index: 0-based index of the card in the selectable hand cards.
    """
    try:
        return await _mp_post({"action": "combat_select_card", "card_index": card_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_combat_confirm_selection() -> str:
    """[Multiplayer Combat Selection] Confirm the in-combat card selection."""
    try:
        return await _mp_post({"action": "combat_confirm_selection"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_relic_select(relic_index: int) -> str:
    """[Multiplayer Relic Selection] Select a relic (boss relic rewards).

    Args:
        relic_index: 0-based index of the relic.
    """
    try:
        return await _mp_post({"action": "select_relic", "index": relic_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_relic_skip() -> str:
    """[Multiplayer Relic Selection] Skip the relic selection."""
    try:
        return await _mp_post({"action": "skip_relic_selection"})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_treasure_claim_relic(relic_index: int) -> str:
    """[Multiplayer Treasure] Bid on / claim a relic from the treasure chest.

    In multiplayer, this is a bid — if multiple players pick the same relic,
    a "relic fight" determines the winner. Others get consolation prizes.

    Args:
        relic_index: 0-based index of the relic.
    """
    try:
        return await _mp_post({"action": "claim_treasure_relic", "index": relic_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_crystal_sphere_set_tool(tool: str) -> str:
    """[Multiplayer Crystal Sphere] Switch the active divination tool.

    Args:
        tool: Either "big" or "small".
    """
    try:
        return await _mp_post({"action": "crystal_sphere_set_tool", "tool": tool})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_crystal_sphere_click_cell(x: int, y: int) -> str:
    """[Multiplayer Crystal Sphere] Click a hidden cell on the Crystal Sphere grid.

    Args:
        x: Cell x-coordinate.
        y: Cell y-coordinate.
    """
    try:
        return await _mp_post({"action": "crystal_sphere_click_cell", "x": x, "y": y})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def mp_crystal_sphere_proceed() -> str:
    """[Multiplayer Crystal Sphere] Continue after the Crystal Sphere minigame finishes."""
    try:
        return await _mp_post({"action": "crystal_sphere_proceed"})
    except Exception as e:
        return _handle_error(e)


def _port_in_use(port: int) -> bool:
    """Check if a TCP port is already bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def _start_displayer(displayer_url: str) -> subprocess.Popen | None:
    """Auto-launch the displayer dashboard server as a background process."""
    displayer_script = Path(__file__).resolve().parent.parent / "displayer" / "server.py"
    if not displayer_script.exists():
        print("[sts2-mcp] Displayer script not found, skipping auto-launch", file=sys.stderr)
        return None

    # Extract port from URL
    port = urlparse(displayer_url).port or 15580

    if _port_in_use(port):
        print(f"[sts2-mcp] Displayer already running on port {port}", file=sys.stderr)
        return None

    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "run", str(displayer_script), "--port", str(port)]
    else:
        cmd = [sys.executable, str(displayer_script), "--port", str(port)]

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(proc.terminate)
        print(f"[sts2-mcp] Displayer launched on port {port} (PID {proc.pid})", file=sys.stderr)
        return proc
    except Exception as e:
        print(f"[sts2-mcp] Failed to auto-launch displayer: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="STS2 MCP Server")
    parser.add_argument("--port", type=int, default=15526, help="Game HTTP server port")
    parser.add_argument("--host", type=str, default="localhost", help="Game HTTP server host")
    parser.add_argument("--no-trust-env", action="store_true", help="Ignore HTTP_PROXY/HTTPS_PROXY environment variables")
    parser.add_argument("--displayer-url", type=str, default="http://localhost:15580",
                        help="Displayer dashboard URL")
    parser.add_argument("--no-displayer", action="store_true",
                        help="Disable displayer notifications and auto-launch")
    args = parser.parse_args()

    global _base_url, _trust_env, _displayer_url, _displayer_enabled
    _base_url = f"http://{args.host}:{args.port}"
    _trust_env = not args.no_trust_env
    _displayer_url = args.displayer_url
    _displayer_enabled = not args.no_displayer

    if _displayer_enabled:
        _start_displayer(_displayer_url)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
