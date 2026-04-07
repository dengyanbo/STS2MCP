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

## 开发相关
- 构建命令：`.\build.ps1 -GameDir "<STS2 安装路径>"`
- 需要 .NET 9 SDK 和 STS2 游戏
- MCP 服务器：`uv run --directory mcp python server.py`
