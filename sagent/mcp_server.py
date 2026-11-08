"""Minimal MCP (stdio, newline-delimited JSON-RPC) server exposing sagent tools to Claude Code."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict

from . import __version__, integrations, safety, skills as skills_mod
from .config import Config
from .integrations.base import Tool, s, schema

PROTOCOL_VERSION = "2024-11-05"


def build_tools(cfg: Config) -> Dict[str, Tool]:
    tools: Dict[str, Tool] = {}
    for integ in integrations.load(cfg):
        if not integ.configured():
            continue
        for tool in integ.tools():
            tool.integration = integ
            tools[tool.name] = tool

    catalog = skills_mod.discover()

    def list_skills(_: Dict[str, Any]) -> str:
        return skills_mod.catalog_text(catalog)

    def load_skill(a: Dict[str, Any]) -> str:
        return skills_mod.resolve(str(a.get("name", "")), catalog).text

    tools["list_skills"] = Tool("list_skills", "List available skills.", schema({}), list_skills)
    tools["load_skill"] = Tool(
        "load_skill", "Load the full instructions of a skill by name.",
        schema({"name": s("Skill name")}, ["name"]), load_skill,
    )
    return tools


def serve(cfg: Config, dry_run: bool, assume_yes: bool) -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    tools = build_tools(cfg)

    def send(message: Dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method is None or mid is None:
            continue  # notifications and responses need no reply

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sagent", "version": __version__},
            }})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "tools/list":
            # alwaysLoad: agy otherwise loads MCP tools on demand and the model may not find them.
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": t.name, "description": t.description, "inputSchema": t.schema,
                 "_meta": {"antigravity/alwaysLoad": True}} for t in tools.values()
            ]}})
        elif method == "tools/call":
            tool = tools.get(str(params.get("name")))
            if tool is None:
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"Unknown tool {params.get('name')!r}"}], "isError": True}})
                continue
            args = params.get("arguments") or {}
            text, is_error = safety.execute(tool, args if isinstance(args, dict) else {}, dry_run, assume_yes)
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}], "isError": is_error}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})
    return 0
