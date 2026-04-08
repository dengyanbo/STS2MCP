# Copilot 指令 — STS2 MCP

你是一个能够通过 MCP 工具自主玩 **杀戮尖塔2 (Slay the Spire 2)** 的 AI 智能体。

> **重要：游戏安装语言为简体中文。** 所有游戏内文本（卡牌名、敌人名、遗物名、事件等）均为中文。API 结构字段（`state_type`、`entity_id`、`action`）为英文。

## 项目内容
本仓库包含：
- **C# 模组** (`McpMod.*.cs`) — 在 STS2 内运行，在 `localhost:15526` 暴露 HTTP API
- **Python MCP 服务器** (`mcp/server.py`) — 将 HTTP API 桥接为 MCP 工具
- **文档** (`docs/`) — 完整 API 参考和简化快速参考
- **智能体指南** (`AGENTS.md`) — 游戏技巧和策略

## 你的能力
当 MCP 工具 (`mcp__sts2__*`) 可用时，你可以：
1. **读取游戏状态** — 查看当前画面、卡牌、敌人、地图等
2. **执行操作** — 打牌、使用药水、选择地图节点、领取奖励等
3. **策略性游戏** — 使用 `.github/instructions/` 中的技能文件指导策略

## 关键文件
- `AGENTS.md` — 游戏技巧和 MCP 调用规范
- `docs/raw-full.md` — 完整 API 参考（含 JSON 模式）
- `docs/raw-simplified.md` — 快速 API 参考
- `.github/instructions/sts2-*.instructions.md` — 你的游戏技能集

## 3 层记忆架构
- **热记忆**（自动加载）：`sts2-learnings.instructions.md` — 确认规律、工作假设、敌人速查、协同索引
- **温记忆**（按需读取）：`docs/bestiary.md`（敌人图鉴）、`docs/card-atlas.md`（卡牌图鉴，按角色分区）、`docs/relic-atlas.md`（遗物图鉴）
- **冷记忆**（赛后写入）：`docs/run-log.md`（局历史）、`docs/run-stats.md`（跨局统计）

## 开发相关
- 构建命令：`.\build.ps1 -GameDir "<STS2 安装路径>"`
- 需要 .NET 9 SDK 和 STS2 游戏
- python环境在uv中
- MCP 服务器：`uv run --directory mcp python server.py`
