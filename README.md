<p align="center">
  <img src="docs/teaser.png" alt="STS2 MCP" width="90%" />
</p>

<p align="center"><em>An Experimental Research Project to Fully-Automate your Slay the Spire 2 Runs</em></p>

A mod for [**Slay the Spire 2**](https://store.steampowered.com/app/2868840/Slay_the_Spire_2/) that lets AI agents play the game. Exposes game state and actions via a localhost REST API, with an optional MCP server for Claude Desktop / Claude Code integration.

Singleplayer and multiplayer (co-op) supported. Tested against STS2 `v0.99.1`.

> [!warning]
> This mod allows external programs to read and control your game via a localhost API. Use at your own risk with runs you care less about.

> [!caution]
> Multiplayer support is in **beta** — expect bugs. Any multiplayer issues encountered with this mod installed are very likely caused by the mod, not the game. Please disable the mod and verify the issue persists before reporting bugs to the STS2 developers.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  STS2 Game + C# Mod (McpMod)                            │
│  HTTP API on localhost:15526                              │
└────────────────┬────────────────────────────────────────┘
                 │ REST
┌────────────────▼────────────────────────────────────────┐
│  MCP Server (mcp/)                                       │
│  Bridges HTTP API → 70+ MCP tools                        │
│  Python 3.11+ · FastMCP · httpx                          │
└────────┬───────────────────────────────────┬────────────┘
         │ stdio (MCP protocol)              │ HTTP POST
┌────────▼────────────┐        ┌─────────────▼────────────┐
│  AI Agent            │        │  Displayer (displayer/)   │
│  (Claude, GPT, etc.) │        │  Live narration dashboard │
└─────────────────────┘        │  localhost:15580           │
                                └──────────────────────────┘
```

| Component | Path | Description |
|---|---|---|
| **C# Mod** | `McpMod.*.cs` | In-game HTTP API server (v0.3.3, .NET 9) |
| **MCP Server** | [`mcp/`](mcp/) | Bridges HTTP API to MCP tools for AI agents |
| **Displayer** | [`displayer/`](displayer/) | Browser dashboard showing live AI narration |
| **Docs** | `docs/` | API reference (`raw-full.md`) and quick reference (`raw-simplified.md`) |
| **AI Skills** | `.github/instructions/` | Auto-loaded gameplay strategy for Copilot agents |

## For Players

### 1. Install the Mod

Grab the [latest release](https://github.com/Gennadiyev/STS2MCP/releases/latest):

1. Copy `STS2_MCP.dll` and `STS2_MCP.json` to `<game_install>/mods/`
2. Launch the game and enable mods in settings (a consent dialog appears on first launch)
3. The mod starts an HTTP server on `localhost:15526` automatically

### 2. Connect Your AI Agent

**Clone or download the repository**, then choose your approach:

| Approach | Setup |
|---|---|
| **Skill-based** (simplest) | Point your AI agent at `docs/raw-simplified.md`. No extra dependencies. |
| **MCP Server** (recommended) | Requires [Python 3.11+](https://www.python.org/) and [uv](https://docs.astral.sh/uv/). See below. |

Add to your MCP config (`.mcp.json` for Claude Code, `claude_desktop_config.json` for Claude Desktop):

```json
{
  "mcpServers": {
    "sts2": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/STS2_MCP/mcp", "python", "server.py"]
    }
  }
}
```

<details>
<summary>MCP server options</summary>

| Flag | Description |
|---|---|
| `--host HOST` | Game API host (default: `localhost`) |
| `--port PORT` | Game API port (default: `15526`) |
| `--no-trust-env` | Ignore proxy env vars (useful in containers) |
| `--displayer-url URL` | Displayer server URL (default: `http://localhost:15580`) |
| `--no-displayer` | Disable displayer integration |

</details>

### 3. Live Dashboard (Optional)

Watch the AI think in real-time with the [displayer dashboard](displayer/):

```bash
uv run python displayer/server.py
# Open http://localhost:15580
```

The MCP server automatically sends events to the displayer — no extra config needed.

## For Developers

### Build & Install

Requires [.NET 9 SDK](https://dotnet.microsoft.com/download/dotnet/9.0) and the base game.

```powershell
# Pass game path directly:
.\build.ps1 -GameDir "C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2"

# Or set it once and forget:
$env:STS2_GAME_DIR = "C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2"
.\build.ps1
```

The script builds `STS2_MCP.dll` into `out/STS2_MCP/`. Copy it along with the manifest to `<game_install>/mods/`:

```
out/STS2_MCP/STS2_MCP.dll   →  <game_install>/mods/STS2_MCP.dll
mod_manifest.json            →  <game_install>/mods/STS2_MCP.json
```

### Project Layout

```
McpMod.cs                    # Entry point, HTTP listener, routing
McpMod.Actions.cs            # Singleplayer action handlers
McpMod.MultiplayerActions.cs # Multiplayer action handlers
McpMod.StateBuilder.cs       # Game state → JSON serialization
McpMod.MultiplayerState.cs   # Multiplayer state serialization
McpMod.Formatting.cs         # Markdown formatter + combat analysis
McpMod.Helpers.cs            # Shared utilities
McpMod.SettingsUI.cs         # In-game settings UI
mcp/server.py                # MCP server (70+ tools)
displayer/                   # Live narration dashboard
docs/                        # API reference
.github/instructions/        # AI gameplay strategy files
```

## License

MIT

## FAQ

<details>
<summary><strong>Why let the AI play the game for me?</strong></summary>

I started building this mod to co-op with an AI player. Singleplayer automation was originally just for validation.

As a researcher who loves games, STS2MCP tests AI models in a rarely explored (out-of-distribution) domain. It may eventually become a benchmark for evaluating reasoning and decision-making capabilities of different language models.

**I have no intention to replace human players with AI, and I would rather play STS2 myself** as a big fan of the game.
</details>

<details>
<summary><strong>Is this a cheat mod?</strong></summary>

The mod itself does not alter gameplay — it's just an interface for external programs to interact with the game. What you do with that interface is up to you.
</details>

<details>
<summary><strong>How many tokens does a run consume?</strong></summary>

Tested on Ironclad: Claude Sonnet 4.6 uses ~8M tokens per full run; GPT-5.4 averages ~7.3M tokens (input + output + tool responses). Your mileage will vary by prompt and model.
</details>

<details>
<summary><strong>What's on the roadmap?</strong></summary>

- Solidifying multiplayer features and fixing bugs
- In-game communication for AI multiplayer co-op
- Self-reflection and learning from past runs
- Benchmarking different models and agents
</details>
