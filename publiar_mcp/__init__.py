"""Publiar MCP server — Sprint F (2026-06-23).

Stdio MCP server qui expose les endpoints Publiar (génération lead magnet,
corpus de référence, autopilot) sous forme de tools pour les agents IA
externes (Claude Desktop, Cursor, Codex CLI, autres clients MCP-compatibles).

Auth : `PUBLIAR_API_KEY` (clé créée depuis /profile sur publiar.app).
Endpoint : `PUBLIAR_API_URL` (default https://api.publiar.app/api).

Usage côté client (extrait de ~/.config/claude_desktop_config.json) :

    {
      "mcpServers": {
        "publiar": {
          "command": "publiar-mcp",
          "env": {
            "PUBLIAR_API_KEY": "mcp_pub_xxxx",
            "PUBLIAR_API_URL": "https://api.publiar.app/api"
          }
        }
      }
    }
"""
__version__ = "0.1.0"
