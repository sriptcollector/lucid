#!/usr/bin/env python3
"""Lucid MCP server — exposes your Lucid data (notes, people, action items) as
tools to any MCP-capable agent (Claude Desktop, Claude Code, Cursor, etc.).

Zero dependencies — pure Python stdlib. It just calls Lucid's read-only Data API.

Configure via environment variables:
  LUCID_URL      base URL of your Lucid app  (default: http://127.0.0.1:8000)
  LUCID_API_KEY  the data key from Lucid → Settings → "Data API · for your code"

Register it with Claude Code:
  claude mcp add lucid -- python /path/to/tools/lucid_mcp.py \\
      -e LUCID_URL=https://your-lucid-url -e LUCID_API_KEY=lkd_xxx

…or add it to claude_desktop_config.json (see docs/AGENT_API.md).
"""
import json
import os
import sys
import urllib.error
import urllib.request

LUCID_URL = os.environ.get("LUCID_URL", "http://127.0.0.1:8000").rstrip("/")
LUCID_API_KEY = os.environ.get("LUCID_API_KEY", "")
PROTOCOL = "2024-11-05"


def _get(path: str) -> dict:
    req = urllib.request.Request(
        LUCID_URL + path,
        headers={"X-API-Key": LUCID_API_KEY, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "{}")


TOOLS = [
    {
        "name": "lucid_list_notes",
        "description": "List recent Lucid notes (summary, people, action items), newest first.",
        "inputSchema": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "max notes (default 20)"},
            "since": {"type": "string", "description": "only notes on/after YYYY-MM-DD"},
        }},
    },
    {
        "name": "lucid_search_notes",
        "description": "Search Lucid notes by keyword across headline, summary, people, and key points.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "description": "max results (default 20)"},
        }, "required": ["query"]},
    },
    {
        "name": "lucid_get_note",
        "description": "Get one Lucid note in full (ideas, timeline, quotes, plans, commitments).",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string"},
        }, "required": ["id"]},
    },
    {
        "name": "lucid_list_people",
        "description": "List the people/clients Lucid knows, with how often they appear.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "lucid_list_action_items",
        "description": "List all action items across every note, each tagged with its source note.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name: str, args: dict) -> dict:
    if name == "lucid_list_notes":
        q = f"?limit={int(args.get('limit', 20))}"
        if args.get("since"):
            q += "&since=" + str(args["since"])
        return _get("/api/data/notes" + q)
    if name == "lucid_search_notes":
        data = _get("/api/data/notes?limit=1000")
        ql = str(args.get("query", "")).lower()
        hits = [n for n in data.get("notes", []) if ql in json.dumps(n).lower()]
        return {"query": args.get("query"), "count": len(hits),
                "notes": hits[: int(args.get("limit", 20))]}
    if name == "lucid_get_note":
        return _get("/api/data/notes/" + str(args.get("id", "")))
    if name == "lucid_list_people":
        return _get("/api/data/people")
    if name == "lucid_list_action_items":
        return _get("/api/data/action-items")
    raise ValueError("unknown tool: " + name)


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        mid, method = req.get("id"), req.get("method")
        params = req.get("params") or {}
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion", PROTOCOL),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lucid", "version": "1.0.0"},
            }})
        elif method in ("notifications/initialized", "initialized"):
            pass  # notification — no response
        elif method == "ping":
            _send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name, args = params.get("name"), (params.get("arguments") or {})
            try:
                if not LUCID_API_KEY:
                    raise RuntimeError("LUCID_API_KEY is not set")
                result = call_tool(name, args)
                text = json.dumps(result, indent=2)
                _send({"jsonrpc": "2.0", "id": mid,
                       "result": {"content": [{"type": "text", "text": text}]}})
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="ignore")[:300]
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"Lucid API error {e.code}: {body}"}],
                    "isError": True}})
            except Exception as e:  # noqa: BLE001
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "Error: " + str(e)}],
                    "isError": True}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": "Method not found: " + str(method)}})


if __name__ == "__main__":
    main()
