---
applyTo: "**"
---

# STS2 MCP — API 与工具参考 (API & Tool Reference)

杀戮尖塔2所有可用 MCP 工具的快速参考。

> **语言提示：** 游戏安装为简体中文。API 返回的 `name`、`description` 等显示字段将为中文。但 `id`、`entity_id`、`action`、`state_type` 等结构字段始终为英文。

## 状态查询 (State Query)

| 工具 | 参数 | 说明 |
|---|---|---|
| `get_game_state(format?)` | `format`: `"json"` 或 `"markdown"` | 获取当前游戏状态。战斗中用 JSON，其他场景用 markdown。 |

## 战斗工具 (Combat Tools)

| 工具 | 参数 | 说明 |
|---|---|---|
| `combat_play_card(card_index, target?)` | `card_index`: int, `target`: entity_id 字符串 | 打出手牌。`AnyEnemy` 类型牌需要 target。 |
| `combat_end_turn()` | — | 结束回合。之后调用 `get_game_state()` 两次查看新手牌。 |
| `combat_select_card(card_index)` | `card_index`: int | 在消耗/弃牌/升级提示中选择卡牌。 |
| `combat_confirm_selection()` | — | 确认战斗内卡牌选择。 |

## 药水工具 (战斗内外均可用)

| 工具 | 参数 | 说明 |
|---|---|---|
| `use_potion(slot, target?)` | `slot`: int, `target`: entity_id | 使用药水。对敌药水需要 target。 |
| `discard_potion(slot)` | `slot`: int | 丢弃药水以腾出槽位。 |

## 导航工具 (Navigation)

| 工具 | 参数 | 说明 |
|---|---|---|
| `map_choose_node(node_index)` | `node_index`: int | 从 `next_options` 选择节点前进。 |
| `proceed_to_map()` | — | 从奖励/篝火/商店/宝箱返回地图。**不适用于事件。** |

## 奖励工具 (Rewards)

| 工具 | 参数 | 说明 |
|---|---|---|
| `rewards_claim(reward_index)` | `reward_index`: int | 领取奖励。卡牌奖励会打开子界面。 |
| `rewards_pick_card(card_index)` | `card_index`: int | 从卡牌奖励中选择一张。 |
| `rewards_skip_card()` | — | 跳过卡牌奖励。 |

## 事件工具 (Events)

| 工具 | 参数 | 说明 |
|---|---|---|
| `event_choose_option(option_index)` | `option_index`: int | 选择事件选项（包括"继续"）。 |
| `event_advance_dialogue()` | — | 推进远古对话。重复直到 `in_dialogue: false`。 |

## 篝火工具 (Rest Site)

| 工具 | 参数 | 说明 |
|---|---|---|
| `rest_choose_option(option_index)` | `option_index`: int | 选择休息/锻造/举重等。 |

## 商店工具 (Shop)

| 工具 | 参数 | 说明 |
|---|---|---|
| `shop_purchase(item_index)` | `item_index`: int | 按索引购买商店物品。 |

## 宝箱工具 (Treasure)

| 工具 | 参数 | 说明 |
|---|---|---|
| `treasure_claim_relic(relic_index)` | `relic_index`: int | 从宝箱领取遗物。 |

## 卡牌选择覆盖层工具 (Card Select Overlay)

| 工具 | 参数 | 说明 |
|---|---|---|
| `deck_select_card(card_index)` | `card_index`: int | 在选择覆盖层中切换/选择卡牌。 |
| `deck_confirm_selection()` | — | 确认选择。 |
| `deck_cancel_selection()` | — | 取消/跳过选择。 |

## 卡组包选择工具 (Bundle Select)

| 工具 | 参数 | 说明 |
|---|---|---|
| `bundle_select(bundle_index)` | `bundle_index`: int | 预览卡组包。 |
| `bundle_confirm_selection()` | — | 确认选中的卡组包。 |
| `bundle_cancel_selection()` | — | 取消卡组包预览。 |

## 遗物选择工具 (Relic Select)

| 工具 | 参数 | 说明 |
|---|---|---|
| `relic_select(relic_index)` | `relic_index`: int | 选择 Boss 遗物（立即生效）。 |
| `relic_skip()` | — | 跳过遗物选择。 |

## 水晶球工具 (Crystal Sphere)

| 工具 | 参数 | 说明 |
|---|---|---|
| `crystal_sphere_set_tool(tool)` | `tool`: `"big"` 或 `"small"` | 切换占卜工具。 |
| `crystal_sphere_click_cell(x, y)` | `x`: int, `y`: int | 揭示一个格子。 |
| `crystal_sphere_proceed()` | — | 完成小游戏。 |

## 状态类型参考 (State Types)

| `state_type` | 画面 | 关键数据字段 |
|---|---|---|
| `menu` | 主菜单 | — |
| `monster` / `elite` / `boss` | 战斗（普通怪/精英/Boss） | `battle.enemies`, `battle.round`, `player.hand`, `player.energy` |
| `hand_select` | 战斗内选牌 | `hand_select.cards`, `hand_select.prompt`（中文提示） |
| `rewards` | 战后奖励 | `rewards.items[]`（类型、描述为中文） |
| `card_reward` | 选卡界面 | `card_reward.cards[]`（卡牌名为中文）, `card_reward.can_skip` |
| `map` | 地图导航 | `map.next_options[]`, `map.current_position` |
| `event` | 事件/远古 | `event.options[]`（选项文字为中文）, `event.in_dialogue` |
| `rest_site` | 篝火 | `rest_site.options[]`（选项名为中文） |
| `shop` | 商店 | `shop.items[]`（物品名为中文）含类别、价格、可负担性 |
| `fake_merchant` | 假商人（仅卖遗物） | `fake_merchant.shop.items[]` |
| `treasure` | 宝箱房 | `treasure.relics[]`（遗物名为中文） |
| `card_select` | 卡牌覆盖层 | `card_select.cards[]`, `card_select.screen_type` |
| `bundle_select` | 卡组包覆盖层 | `bundle_select.bundles[]` |
| `relic_select` | Boss 遗物选择 | `relic_select.relics[]` |
| `crystal_sphere` | 水晶球小游戏 | `crystal_sphere.cells[]`, `crystal_sphere.clickable_cells[]` |

## Entity ID 格式
- 大写蛇形命名加数字后缀：`JAW_WORM_0`（颚虫）、`KIN_PRIEST_0`（亲族祭司）、`SLIME_BOSS_0`（史莱姆Boss）
- 作为单体牌和药水的 `target` 参数使用
- **注意：entity_id 始终为英文**，即使游戏界面显示中文怪物名

## 错误处理
- 所有操作返回 `{ "status": "ok" | "error", "message": "..." }`
- 出错时阅读消息并调整（错误状态、缺少目标、能量不足等）
- 常见错误：牌不可打出、能量不足、缺少 target、当前 state_type 不匹配操作
