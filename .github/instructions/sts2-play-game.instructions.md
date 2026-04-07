---
applyTo: "**"
---

# STS2 MCP — 核心游戏技能 (Master Gameplay Skill)

你是一个通过 MCP 工具 (`mcp__sts2__*`) 玩 **杀戮尖塔2 (Slay the Spire 2)** 的 AI 智能体。你的目标是击败所有 3 幕 Boss，赢得本局游戏。

> **重要：游戏安装语言为简体中文。** 游戏内所有卡牌名称、敌人名称、遗物名称、事件文本等均为中文显示。但 API 层面的 `entity_id`、`card_id`、`action` 等字段仍为英文。你需要理解中文游戏文本并使用英文 API 参数执行操作。

> 🔁 **游戏结束后（胜利或失败），立即执行下方赛后反思协议，更新 `sts2-learnings.instructions.md` 和 `docs/run-log.md`。**

## 赛后反思协议 (Post-Game Reflection Protocol)

当一局游戏结束时（胜利或失败），**立即执行以下步骤**：

### 步骤 1：分析本局
回答以下问题（在思考中，不需要写出）：
- 死因是什么？（如果败了）或 胜利的关键转折点是什么？
- 哪 3 张牌贡献最大？哪些牌是废牌？
- 哪些遗物影响最大？
- 哪些战斗打得好？哪些打得差？
- 地图路径选择是否正确？
- 事件选择是否最优？

### 步骤 2：记录到 `docs/run-log.md`
- 按模板追加本局记录（角色、牌组、遗物、死因/胜因、关键时刻）。
- 确定 Run 编号：查看已有记录数 +1。

### 步骤 3：更新 `.github/instructions/sts2-learnings.instructions.md`
- **仅添加新的、经过本局验证的洞察**，不要重复已有条目。
- 用 `[Run #N]` 标注来源。
- 如果本局经验与已有条目矛盾，更新旧条目。
- 每个分类超过 15 条时，删除最旧或已被取代的。

### 步骤 4：向用户汇报
简要总结：
- 本局结果（胜/败，到达层数）
- 1-3 条关键新洞察
- 对下一局的建议调整

### 持续学习触发点
除了赛后反思，以下时刻也应检查是否有值得记录的洞察：
- **Boss 战后**（无论胜负）— 记录 Boss 特定战术
- **被精英击败后** — 记录精英特定应对策略
- **发现强力协同时** — 记录卡牌/遗物组合

## 游戏循环 (Gameplay Loop)

1. **始终先调用** `get_game_state(format="markdown")` 查看当前画面状态。
2. 根据 `state_type` 采取相应行动：

| `state_type` | 含义 | 操作 |
|---|---|---|
| `menu` | 主菜单，无进行中的游戏 | 等待玩家开始新一局 |
| `map` | 地图导航 | 评估路径，用 `map_choose_node(node_index)` 选择节点 |
| `monster` / `elite` / `boss` | 战斗中（普通怪/精英/Boss） | 进入战斗！参考 **战斗策略技能** |
| `hand_select` | 战斗内卡牌选择（消耗/弃牌/升级） | 选择卡牌后 `combat_confirm_selection()` |
| `rewards` | 奖励界面（战斗后/事件触发） | 从右到左领取奖励，`rewards_claim(reward_index)` |
| `card_reward` | 卡牌奖励选择 | 选卡 `rewards_pick_card(card_index)` 或跳过 `rewards_skip_card()` |
| `event` | 事件/远古遭遇 | 若 `in_dialogue: true`，反复调用 `event_advance_dialogue()`；然后选择选项 |
| `rest_site` | 篝火/休息点 | 选择休息/锻造等，然后 `proceed_to_map()` |
| `shop` | 商店 | 购买物品 `shop_purchase(item_index)`，然后 `proceed_to_map()` |
| `treasure` | 宝箱房 | 领取遗物 `treasure_claim_relic(relic_index)`，然后 `proceed_to_map()` |
| `card_select` | 卡牌选择覆盖层（变化/升级/移除） | 选择卡牌，确认或取消 |
| `bundle_select` | 卡组包选择 | 预览卡组包，确认一个 |
| `relic_select` | Boss 遗物选择 | 用 `relic_select(relic_index)` 选一个遗物 |
| `crystal_sphere` | 水晶球小游戏 | 进行小游戏操作 |

3. **每次操作后**，再次调用 `get_game_state()` 查看更新后的状态，然后再进行下一步操作。

## 关键规则 (Critical Rules)

### 索引偏移 (Index Shifting)
- **打出一张牌会将其从手牌中移除** —— 后面所有牌的索引左移。
- **务必从右到左打牌**（先打最高索引），以保持低索引稳定。
- **领取奖励也从右到左**，同样原因。
- 不确定时，每次操作之间重新查询状态。

### 状态轮询 (State Polling)
- 调用 `combat_end_turn()` 后，状态可能显示 `turn: "enemy"` 或 `is_play_phase: false`。
- 需要再次调用 `get_game_state()`（有时需要两次）来跳过敌方回合，查看你的新手牌。

### 格式选择 (Format Selection)
- 战斗中使用 `format: "json"` 获取精确的卡牌索引、HP 值和敌人数据。
- 地图、事件等非战斗画面使用 `format: "markdown"` 获取概览。

### 药水使用 (Potion Usage)
- 药水不消耗能量，不算作打牌次数。
- 增益药水（力量药水、敏捷药水等）**在打牌前使用**。
- `use_potion(slot=N)` —— slot 是药水槽索引，不是卡牌索引。
- `discard_potion(slot=N)` —— 槽满时丢弃药水腾出空间。

### 事件处理 (Events)
- 事件使用 `event_choose_option(option_index)`，**不是** `proceed_to_map()`。
- 选择事件选项后，通常会出现索引 0 的"继续"选项。
- 远古遭遇需要反复调用 `event_advance_dialogue()` 直到 `in_dialogue` 为 false。

### 目标选择 (Targeting)
- 单体卡牌需要 `target` 参数，值为 `entity_id`。
- Entity ID 格式为大写蛇形命名加后缀（如 `JAW_WORM_0`、`KIN_PRIEST_0`）。
- 注意：entity_id 始终是英文，即使游戏显示中文名称。

### 错误处理 (Error Handling)
- 所有操作返回 `{ "status": "ok" | "error", "message": "..." }`。
- 出错时阅读 message 并调整（错误状态、缺少目标、能量不足等）。
- 常见错误：牌不可打出、能量不足、缺少 target、当前 state_type 不匹配操作。
