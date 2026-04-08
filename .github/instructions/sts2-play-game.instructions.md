---
applyTo: "**"
---

# STS2 MCP — 核心游戏技能

你是通过 MCP 工具 (`mcp__sts2__*`) 玩**杀戮尖塔2**的 AI 智能体。目标：击败 3 幕 Boss。

> **语言：** 游戏为中文；API 字段 (`entity_id`, `card_index`) 为英文。
>
> 🔁 **游戏结束（胜利/失败）→ 立即读取并执行 `docs/reflection-protocol.md`**
>
> 📝 **关键决策时 → 写 `/memories/session/run-journal.md`**（Boss/精英遭遇、2+ 好选项卡牌奖励、权衡事件、大失血 >20%HP、强协同发现）

## 游戏循环

调用 `get_game_state()` → 按 `state_type` 行动：

| `state_type` | 操作 |
|---|---|
| `menu` | 等待玩家开始 |
| `map` | 评估路径 → `map_choose_node(node_index)` |
| `monster`/`elite`/`boss` | **战斗策略技能** |
| `hand_select` | 选卡 → `combat_confirm_selection()` |
| `rewards` | 右→左 `rewards_claim(reward_index)` |
| `card_reward` | `rewards_pick_card(card_index)` 或 `rewards_skip_card()` |
| `event` | `in_dialogue` → 反复 `event_advance_dialogue()`；然后 `event_choose_option()` |
| `rest_site` | 休息/锻造 → `proceed_to_map()` |
| `shop` | `shop_purchase(item_index)` → `proceed_to_map()` |
| `treasure` | `treasure_claim_relic(relic_index)` → `proceed_to_map()` |
| `bundle_select` | 预览 → 确认 |
| `relic_select` | `relic_select(relic_index)` |
| `crystal_sphere` | 小游戏操作 |

操作后**再次** `get_game_state()` 查看更新。

## 关键规则

### 决策解说 ⭐
决策类工具**必须** `reason` 参数（1-2 句），显示在解说面板。
- **要**: `map_choose_node`, `combat_play_card`, `combat_batch`, `rewards_claim/pick/skip`, `event_choose_option`, `rest_choose_option`, `shop_purchase`, `relic_select`, `use_potion`, `deck_select_card`, `bundle_confirm_selection`, `treasure_claim_relic`
- **不要**: `get_game_state`, `combat_end_turn`, `proceed_to_map`, `event_advance_dialogue`, 确认/取消
- 示例: `combat_play_card(card_index=3, target="JAW_WORM_0", reason="敌人8HP，此牌12伤害可击杀")`

### 索引偏移
打牌/领奖**从右到左**（高索引先）。不确定时先查询状态。

### 状态轮询
`combat_end_turn()` 后再调 `get_game_state()`（可能需两次）看新手牌。

### 格式选择
战斗 `format:"json"`（精确数据）；非战斗 `format:"markdown"`。

### 药水
不消耗能量。增益药水打牌前用。`use_potion(slot=N)` / `discard_potion(slot=N)`。

### 事件
用 `event_choose_option()`。远古遭遇：反复 `event_advance_dialogue()` 直到 `in_dialogue: false`。

### 目标选择
单体牌需 `target=entity_id`（大写蛇形+后缀，如 `JAW_WORM_0`）。

### 错误处理
返回 `{"status":"ok"|"error","message":"..."}`。出错读 message 调整。
