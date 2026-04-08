# STS2 — 赛后反思协议 (Post-Game Reflection Protocol)

> 本文件在游戏结束时由 AI 读取执行。不自动加载——由 `sts2-play-game` 中的触发器调用。

## 3 层记忆架构

| 层级 | 文件 | 加载 | 更新 |
|---|---|---|---|
| **热记忆** | `sts2-learnings.instructions.md` | 自动 | 赛后 |
| **温记忆** | `docs/bestiary.md`, `docs/card-atlas.md`, `docs/relic-atlas.md` | `grep_search` 按需 | 赛后 |
| **冷记忆** | `docs/run-log.md`, `docs/run-stats.md` | 赛后写入 | 赛后 |

### 温记忆查询规则
- 战斗前 → `docs/bestiary.md` 搜敌人名
- 卡牌奖励 → `docs/card-atlas.md` 搜候选卡
- 遗物选择 → `docs/relic-atlas.md` 搜遗物名

### 局内微日志格式

```
## Turn N | Floor X | [场景]
- 决策: [做了什么]
- 理由: [为什么]
- 结果: [好/坏/待观察]
```

## 反思步骤

### 步骤 1：分析本局
在思考中回答：死因/胜因？最佳 3 牌？废牌？最佳遗物？打得好/差的战斗？路径？事件？

### 步骤 1.5：读取微日志
读 `/memories/session/run-journal.md`，提取关键微决策和错误模式。

### 步骤 2：记录 `docs/run-log.md`
按模板追加（角色、牌组、遗物、死因/胜因、关键时刻）。Run# = 已有+1。
含决策审计 3-5 个：
- `✅ 正确: [决策] → [结果]`
- `❌ 错误: [决策] → [结果]`
- `❓ 待验证: [决策] — 需更多数据`

### 步骤 3：更新热记忆 `sts2-learnings.instructions.md`
- 仅新洞察，标注 `[通用]`/`[角色名]` + `[N/M 确认, Run #X]`
- 支持已有假设 → `[N+1/M+1]`；矛盾 2+ 次 → 移除
- 3+ 确认 → 升级「确认规律」
- 每分区 ≤15 条

### 步骤 3.5：更新温记忆知识库
- **bestiary.md** — 新敌人档案；已有敌人补充角色应对+遭遇记录
- **card-atlas.md** — 表现突出/差的卡牌评级更新
- **relic-atlas.md** — 获取遗物的评级更新
- **run-stats.md** — 总览/角色/死因统计更新

### 步骤 4：向用户汇报
结果（胜/败+层数）+ 1-3 关键洞察 + 下局建议。

### 步骤 5：策略审查（Run# 为 5 的倍数时）
1. 审查 `sts2-learnings.instructions.md` 中所有「确认规律」
2. 5+ 确认 + 高影响 → 写入策略文件 (combat/deck/map) 作为新规则
3. 写入后从 learnings 移除（已成为核心策略）
4. 审查 `docs/run-stats.md` → 系统弱点 → 添加纠正规则
5. 策略文件头部更新 `<!-- 上次策略审查：Run #N -->`

### 持续学习触发
- **Boss 战后** → 更新 bestiary
- **精英击败后** → 更新 bestiary
- **发现强协同** → 更新协同索引
