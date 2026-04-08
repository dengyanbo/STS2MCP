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

from game_logger import GameLogger

# Suppress noisy httpx request logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

mcp = FastMCP("sts2")

_base_url: str = "http://localhost:15526"
_trust_env: bool = True
_displayer_url: str = "http://localhost:15580"
_displayer_enabled: bool = True

# Game logger — initialized in main() with the correct log directory
_game_logger: GameLogger | None = None


# ---------------------------------------------------------------------------
# Displayer integration — fire-and-forget notifications to the dashboard
# ---------------------------------------------------------------------------

async def _notify_displayer(
    tool_name: str, params: dict, result: str,
    state_json: str | None = None,
) -> None:
    """Post a tool-call event to the displayer dashboard (best-effort).

    Args:
        state_json: Optional JSON-format game state string for the displayer
                    to cache.  Sent when get_game_state uses markdown format
                    so the narration engine still has structured data.
    """
    if not _displayer_enabled:
        return
    try:
        payload: dict = {"tool": tool_name, "params": params, "result": result}
        if state_json is not None:
            payload["state"] = state_json
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            await client.post(f"{_displayer_url}/api/events", json=payload)
    except Exception:
        pass  # Displayer is optional — never block gameplay


# Wrap mcp.tool so every registered tool auto-notifies the displayer
# and logs to the game logger.
_orig_mcp_tool = mcp.tool

# Tools that don't change game state — skip the extra JSON state fetch
_LOGGER_READ_ONLY = frozenset({"narrate", "get_game_state", "mp_get_game_state"})

# Stash for the last raw action response dict.
# Used by _instrumented_tool to extract game_state from the action response
# itself, eliminating a redundant (and potentially stale) extra HTTP call.
# Safe because MCP tool calls are serial (no parallelism).
_action_game_state: dict | None = None


def _instrumented_tool(*deco_args, **deco_kwargs):
    orig_decorator = _orig_mcp_tool(*deco_args, **deco_kwargs)

    def wrapper(func):
        @functools.wraps(func)
        async def instrumented(**kwargs):
            global _action_game_state

            # Extract reason (display-only) before calling the game function
            reason = kwargs.pop("reason", None)

            # Reset stash before calling the tool function.
            # _post/_mp_post will populate it from the action response.
            _action_game_state = None

            result = await func(**kwargs)

            tool_name = func.__name__

            # --- Extract state for displayer & logger --------------------
            # Priority: embedded state from action response (set by _post)
            # > extra fetch (only for get_game_state with markdown format).
            # This eliminates the redundant extra HTTP call that previously
            # raced with the game's state update.
            displayer_state: str | None = None
            logger_state_obj: dict | None = None

            try:
                if tool_name in ("get_game_state", "mp_get_game_state"):
                    if kwargs.get("format") == "json":
                        displayer_state = result  # already JSON string
                        logger_state_obj = json.loads(result)
                    else:
                        # Fetch JSON for displayer state cache
                        fetcher = _mp_get if tool_name == "mp_get_game_state" else _get
                        raw = await fetcher({"format": "json"})
                        displayer_state = raw
                        logger_state_obj = json.loads(raw)
                elif tool_name not in _LOGGER_READ_ONLY:
                    # Use the embedded game_state from the action response
                    # (stashed by _post/_mp_post). No extra HTTP call needed.
                    logger_state_obj = _action_game_state
                    # Also send to displayer for status bar updates + narration
                    if _action_game_state is not None:
                        displayer_state = json.dumps(
                            _action_game_state, ensure_ascii=False
                        )
            except Exception:
                pass

            # --- Displayer notification (fire-and-forget) ----------------
            params_for_displayer = dict(kwargs)
            if reason:
                params_for_displayer["reason"] = reason
            asyncio.create_task(
                _notify_displayer(
                    tool_name, params_for_displayer, result, displayer_state
                )
            )

            # --- Game logger ---------------------------------------------
            if _game_logger is not None:
                try:
                    _game_logger.log_tool_call(
                        tool_name, dict(kwargs), result, logger_state_obj
                    )
                except Exception:
                    pass  # Logger must never break gameplay

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
    global _action_game_state
    async with httpx.AsyncClient(timeout=10, trust_env=_trust_env) as client:
        r = await client.post(
            _sp_url(), json=body, params={"format": "markdown"}
        )
        r.raise_for_status()
        # Stash the embedded game_state for the instrumented wrapper
        try:
            raw = json.loads(r.text)
            _action_game_state = raw.get("game_state")
        except (json.JSONDecodeError, TypeError):
            pass
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
    global _action_game_state
    async with httpx.AsyncClient(timeout=10, trust_env=_trust_env) as client:
        r = await client.post(
            _mp_url(), json=body, params={"format": "markdown"}
        )
        r.raise_for_status()
        # Stash the embedded game_state for the instrumented wrapper
        try:
            raw = json.loads(r.text)
            _action_game_state = raw.get("game_state")
        except (json.JSONDecodeError, TypeError):
            pass
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
async def narrate(text: str) -> str:
    """Send your complete Chain-of-Thought analysis to the live thinking panel.

    Call this BEFORE every significant decision. This is your PRIMARY channel
    for sharing detailed reasoning with viewers. The text appears in a
    dedicated thinking sidebar — not the action feed — so be thorough.

    Write in natural Chinese with the following structure:

    📍 局势总结 — What is the current situation? Key numbers (HP, energy,
       enemy HP/intent, gold, floor, deck size, etc.)
    🔍 选项列举 — What are ALL available options? List them explicitly.
    ⚖️ 逐项评估 — For EACH option, analyze pros/cons with numerical
       reasoning. E.g. "痛击: 1费→8伤+3易伤, 下回合所有攻击×1.5"
    ✅ 最终决策 — Which option wins and WHY? Summarize the key factor.
    ⚠️ 风险与备选 — What could go wrong? What's plan B?

    Not every section is needed every time — adapt to context:
    - Combat turns: focus on damage calc, kill math, energy plan
    - Map choices: focus on HP thresholds, path lookahead, economy
    - Card rewards: focus on deck synergy, archetype fit, what's missing
    - Events: focus on risk/reward tradeoff with numbers

    Args:
        text: Your full analysis in natural Chinese. Use newlines to
              separate sections. Aim for 5-15 sentences for important
              decisions (combat turns, boss fights, card picks).
              Shorter (3-5 sentences) for simpler choices.

    Example (combat turn):
        "📍 回合2，3能量。小啃兽 28/43HP，意图攻击12。手牌：痛击(1)、防御(1)、防御(1)、打击(1)、燃烧(1)。无格挡。\\n"
        "🔍 方案A: 痛击→防御→防御 = 8伤+3易伤+10挡，净受伤2\\n"
        "方案B: 燃烧→痛击→防御 = 3力量+8伤+5挡，净受伤7但后续回合伤害大增\\n"
        "⚖️ 方案B虽然本回合多受5伤(82→75HP)，但燃烧是能力牌，越早打收益越高。3力量让后续每次攻击+3伤害。\\n"
        "✅ 选方案B！燃烧第一个打，再痛击(易伤放大后续)，最后防御减伤。\\n"
        "⚠️ 如果下回合敌人继续攻击且手牌差，75HP仍有余量承受。"

    Example (map choice):
        "📍 第6层，HP 71/80(89%)，金币87，0药水。前方4条路线。\\n"
        "🔍 路线0: 怪→怪→精英→篝火→怪 | 路线1: 怪→未知→怪→商店→怪 | 路线2: 怪→怪→怪→篝火→怪 | 路线3: 怪→篝火→怪→怪→怪\\n"
        "⚖️ 路线0有精英，HP 89%完全可以打。精英掉遗物是全局加速关键。精英后有篝火可以恢复或升级。\\n"
        "路线1有商店但只有87金，不够移除+买卡(75+50=125)。\\n"
        "✅ 走路线0！精英+篝火是最佳组合。\\n"
        "⚠️ 如果精英前怪战损血严重(HP<50%)，精英后篝火必须休息不能升级。"
    """
    # This tool doesn't call the game — it only sends text to the displayer.
    # The _instrumented_tool wrapper handles the displayer notification.
    return "OK"


@mcp.tool()
async def report_mistake(text: str, turn: int | None = None) -> str:
    """Report a gameplay mistake identified during post-turn analysis.

    Call this at the start of each combat turn (Step 0) to flag errors
    from previous turns. The mistake will appear in the live dashboard's
    dedicated mistakes panel.

    Args:
        text: Description of the mistake in natural Chinese. Include what
              went wrong, what the correct play was, and estimated impact.
              Example: "上回合应该先打痛击施加易伤再打重刀，损失约6点伤害"
        turn: The turn number when the mistake occurred (optional).
    """
    return "OK"


@mcp.tool()
async def get_last_turn_summary() -> str:
    """Get a structured summary of the last completed combat turn.

    Returns detailed data about the previous turn including:
    - Game state at turn start (hand, enemies, intents, HP, energy)
    - All actions taken (cards played with names, potions used, targets)
    - Game state at turn end (after enemy actions)
    - HP/damage changes

    Use this at the start of each combat turn to feed a sub-agent for
    mistake analysis. The sub-agent can identify suboptimal plays like
    wrong card order, wasted energy, incorrect targeting, etc.

    Returns "No turn data" if no completed turn is available.
    """
    if not _displayer_enabled:
        return "Displayer not enabled — turn tracking unavailable"
    try:
        async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
            r = await client.get(f"{_displayer_url}/api/last-turn")
            r.raise_for_status()
            data = r.json()
            summary = data.get("summary")
            if summary:
                return summary
            return "No turn data"
    except Exception:
        return "Failed to fetch turn data from displayer"


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
    global _action_game_state
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
                _action_game_state = data
                return await _get({"format": "markdown"})

            # Still in combat — check if it's the player's turn again
            battle = data.get("battle", {})
            if battle.get("is_play_phase", False):
                _action_game_state = data
                return await _get({"format": "markdown"})
    except Exception:
        pass  # polling failed, return what we have

    # Fallback: return whatever state we have now
    try:
        return await _get({"format": "markdown"})
    except Exception:
        return result_text


def _extract_hand(state: dict) -> list[dict]:
    """Extract hand card list from a game state dict."""
    return state.get("battle", {}).get("hand", [])


def _find_card_in_hand(
    hand: list[dict], card_id: str, is_upgraded: bool,
) -> int | None:
    """Return the index field of the first matching card in *hand*."""
    for card in hand:
        if card.get("id") == card_id and card.get("is_upgraded") == is_upgraded:
            return card.get("index")
    return None


@mcp.tool()
async def combat_batch(actions: list[dict], reason: str | None = None) -> str:
    """[Combat] Execute multiple combat actions in a single call.

    Plays cards, uses potions, and optionally ends the turn — all in one
    round-trip. Each action is executed sequentially with a short delay
    to let the game process animations. Card indices are based on the
    hand state **at the time combat_batch is called** — the tool
    snapshots the hand, identifies each card by (id, upgraded), and
    re-resolves the correct index in the live hand before every play.
    This prevents wrong-card bugs caused by index shifting after draws
    or earlier plays.

    ⚠️ IMPORTANT SAFETY RULES:
    - When selecting multiple cards (hand_select), select from HIGHEST
      index to LOWEST to avoid index shifting.
    - Do NOT batch actions that involve randomness (draw effects,
      random potions, etc.). Execute them individually, call
      get_game_state() to see the result, then decide the next step.
      E.g. 燃烧契约 draws new cards — don't pre-plan plays after it.
    - If an action fails to register, fall back to individual tool
      calls (combat_play_card / use_potion) instead of retrying batch.

    Args:
        actions: Ordered list of action dicts. Each dict has:
            - type: "play_card" | "use_potion" | "end_turn"
            - card_index: (play_card) 0-based index in current hand
            - target: (play_card/use_potion) entity_id if needed
            - slot: (use_potion) potion slot index

        - reason: (Required) Your turn strategy: what you plan to do this turn, key calculations (damage/block math), and why. 2-3 sentences. Example: "痛击施加易伤后，打击伤害从6→9。剩1能量防御减少5点伤害，净受伤7点可接受。"

    Example: [
        {"type": "use_potion", "slot": 0},
        {"type": "play_card", "card_index": 4, "target": "JAW_WORM_0"},
        {"type": "play_card", "card_index": 2},
        {"type": "end_turn"}
    ]
    """
    results: list[str] = []

    # ------------------------------------------------------------------
    # 1. Snapshot the initial hand so we can identify cards by identity
    #    rather than by fragile positional index.
    # ------------------------------------------------------------------
    initial_hand: list[dict] = []
    try:
        raw_state = await _get({"format": "json"})
        initial_state = json.loads(raw_state)
        initial_hand = _extract_hand(initial_state)
    except Exception:
        pass  # fallback: direct index mode (original behaviour)

    # Pre-resolve: for each play_card action, record the card identity
    # (id + is_upgraded) that the caller intended, looked up by index in
    # the initial hand snapshot.
    _intended: list[dict | None] = []
    for action in actions:
        if action.get("type") == "play_card":
            idx = action.get("card_index", -1)
            if initial_hand and 0 <= idx < len(initial_hand):
                c = initial_hand[idx]
                _intended.append({
                    "id": c.get("id", ""),
                    "name": c.get("name", ""),
                    "is_upgraded": c.get("is_upgraded", False),
                    "original_index": idx,
                })
            else:
                # Index out of range or no snapshot — will use raw index
                _intended.append(None)
        else:
            _intended.append(None)

    # Live hand state — updated after each successful action from the
    # game_state embedded in the action response.
    current_hand: list[dict] = list(initial_hand)

    for i, action in enumerate(actions):
        action_type = action.get("type", "")

        try:
            if action_type == "play_card":
                intended = _intended[i]
                raw_index = action["card_index"]

                if intended is not None and current_hand:
                    resolved = _find_card_in_hand(
                        current_hand, intended["id"], intended["is_upgraded"],
                    )
                    if resolved is not None:
                        use_index = resolved
                    else:
                        # Card no longer in hand (maybe already played/discarded)
                        results.append(
                            f"[{i}] ✗ Card '{intended['name']}' (id={intended['id']}) "
                            f"not found in current hand"
                        )
                        break
                else:
                    # No snapshot available — fall back to raw index
                    use_index = raw_index

                body: dict = {
                    "action": "play_card",
                    "card_index": use_index,
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
                # Update current hand from the embedded game_state so
                # the next play_card can resolve against fresh indices.
                gs = data.get("game_state")
                if isinstance(gs, dict):
                    current_hand = _extract_hand(gs)
            else:
                results.append(f"[{i}] ✗ {msg}")
                break  # stop on first error

        except Exception as e:
            results.append(f"[{i}] ✗ {_handle_error(e)}")
            break

        # Wait for game animation to complete between actions.
        # 300ms handles most card animations; complex chains (AOE,
        # multi-draw, exhaust triggers) may need the re-resolve fallback.
        if action_type != "end_turn" and i < len(actions) - 1:
            await asyncio.sleep(0.3)

    # If the last action was end_turn, wait for enemy turn to complete
    if actions and actions[-1].get("type") == "end_turn":
        try:
            for _ in range(20):
                await asyncio.sleep(0.25)
                raw = await _get({"format": "json"})
                data = json.loads(raw)
                st = data.get("state_type", "")
                if st not in ("monster", "elite", "boss"):
                    _action_game_state = data
                    break
                if data.get("battle", {}).get("is_play_phase", False):
                    _action_game_state = data
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
async def rewards_claim(reward_index: int, reason: str | None = None) -> str:
    """[Rewards] Claim a reward from the post-combat rewards screen.

    Gold, potion, and relic rewards are claimed immediately.
    Card rewards open the card selection screen (state changes to card_reward).

    Args:
        reward_index: 0-based index of the reward on the rewards screen.
        reason: Why you're claiming this specific reward and its value to your run. 1-2 sentences.

    Note that claiming a reward may change the indices of remaining rewards, so refer to the latest game state for accurate indices.
    Claiming from right to left can help maintain more stable indices for remaining rewards, as rewards will always shift left to fill in gaps.
    """
    try:
        return await _post({"action": "claim_reward", "index": reward_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def rewards_pick_card(card_index: int, reason: str | None = None) -> str:
    """[Rewards] Select a card from the card reward selection screen.

    Args:
        card_index: 0-based index of the card to add to the deck.
        reason: Why this card over the alternatives — deck synergy, what problem it solves, and what you skipped. 2-3 sentences.
    """
    try:
        return await _post({"action": "select_card_reward", "card_index": card_index})
    except Exception as e:
        return _handle_error(e)


@mcp.tool()
async def rewards_skip_card(reason: str | None = None) -> str:
    """[Rewards] Skip the card reward without selecting a card.

    Args:
        reason: Why none of the offered cards fit — deck size, archetype mismatch, etc. 1-2 sentences.
    """
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
async def map_choose_node(node_index: int, reason: str | None = None) -> str:
    """[Map] Choose a map node to travel to.

    Args:
        node_index: 0-based index of the node from the next_options list.
        reason: Path analysis: why this route over alternatives, HP/economy considerations, and what you expect ahead. 2-3 sentences.
    """
    global _action_game_state
    try:
        result_text = await _post({"action": "choose_map_node", "index": node_index})
    except Exception as e:
        return _handle_error(e)

    # Poll until the game transitions away from the map screen.
    # The mod processes the node choice, but the new screen (combat, event,
    # shop, etc.) may take time to load.  Similar to combat_end_turn polling.
    try:
        for _ in range(15):  # up to ~3s
            await asyncio.sleep(0.2)
            raw = await _get({"format": "json"})
            data = json.loads(raw)
            if data.get("state_type") != "map":
                # Update stash with the post-transition state
                _action_game_state = data
                return await _get({"format": "markdown"})
    except Exception:
        pass  # polling failed — return what we have

    return result_text


# ---------------------------------------------------------------------------
# Rest Site (state_type: rest_site)
# ---------------------------------------------------------------------------


@mcp.tool()
async def rest_choose_option(option_index: int, reason: str | None = None) -> str:
    """[Rest Site] Choose a rest site option (rest, smith, etc.).

    Args:
        option_index: 0-based index of the option from the rest site state.
        reason: Why rest vs smith vs other options — HP status, upgrade targets, upcoming path considerations. 1-2 sentences.
    """
    try:
        return await _post({"action": "choose_rest_option", "index": option_index})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Shop (state_type: shop)
# ---------------------------------------------------------------------------


@mcp.tool()
async def shop_purchase(item_index: int, reason: str | None = None) -> str:
    """[Shop / Fake Merchant] Purchase an item from the shop.

    Works for both regular shops (state_type: shop) and the fake merchant
    event (state_type: fake_merchant). The fake merchant only sells relics.

    Args:
        item_index: 0-based index of the item from the shop state.
        reason: Why this purchase — value assessment, deck/build synergy, and gold budget. 1-2 sentences.
    """
    try:
        return await _post({"action": "shop_purchase", "index": item_index})
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Event (state_type: event)
# ---------------------------------------------------------------------------


@mcp.tool()
async def event_choose_option(option_index: int, reason: str | None = None) -> str:
    """[Event] Choose an event option.

    Works for both regular events and ancients (after dialogue ends).
    Also used to click the Proceed option after an event resolves.

    Args:
        option_index: 0-based index of the unlocked option.
        reason: Risk/reward analysis of the chosen option vs alternatives. What you gain, what you risk. 1-2 sentences.
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
async def relic_select(relic_index: int, reason: str | None = None) -> str:
    """[Relic Selection] Select a relic from the relic selection screen.

    Used when the game offers a choice of relics (e.g., boss relic rewards).

    Args:
        relic_index: 0-based index of the relic (as shown in game state).
        reason: Why this relic over the others — build synergy, immediate value, and what you passed on. 2-3 sentences.
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
async def treasure_claim_relic(relic_index: int, reason: str | None = None) -> str:
    """[Treasure] Claim a relic from the treasure chest.

    The chest is auto-opened when entering the treasure room.
    After claiming, use proceed_to_map() to continue.

    Args:
        relic_index: 0-based index of the relic (as shown in game state).
        reason: Relic value assessment for your current build. 1 sentence.
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
    parser.add_argument("--log-dir", type=str, default=None,
                        help="Directory for detailed game logs (default: <project>/logs)")
    parser.add_argument("--no-logging", action="store_true",
                        help="Disable detailed game logging")
    args = parser.parse_args()

    global _base_url, _trust_env, _displayer_url, _displayer_enabled, _game_logger
    _base_url = f"http://{args.host}:{args.port}"
    _trust_env = not args.no_trust_env
    _displayer_url = args.displayer_url
    _displayer_enabled = not args.no_displayer

    if _displayer_enabled:
        _start_displayer(_displayer_url)

    # Initialize game logger
    if not args.no_logging:
        log_dir = Path(args.log_dir) if args.log_dir else (
            Path(__file__).resolve().parent.parent / "logs"
        )
        _game_logger = GameLogger(log_dir)
        atexit.register(_game_logger.force_end_run, "server_shutdown")
        print(f"[sts2-mcp] Game logging enabled → {log_dir}", file=sys.stderr)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
