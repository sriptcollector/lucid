# Lucid Data API — connect your agent

Lucid exposes a **read-only** API so your code or AI agent can pull your notes,
people, and action items as JSON. It's protected by a **data key** that's
separate from your app login, so you can hand it to code without giving away
account access.

## 1. Get your key

In Lucid: **Settings → "Data API · for your code" → Generate API key → Copy.**
The key looks like `lkd_xxxxxxxx…`. Your base URL is your Lucid address (the
public link in Settings, or `http://127.0.0.1:8000` on the same machine).

Send the key on every request, any one of these ways:

- Header: `X-API-Key: <key>`
- Header: `Authorization: Bearer <key>`
- Query string: `?key=<key>`

## 2. Endpoints

| Method & path | What it returns |
|---|---|
| `GET /api/data` | Index + counts |
| `GET /api/data/notes?limit=&offset=&since=YYYY-MM-DD&full=true` | List of notes (newest first) |
| `GET /api/data/notes/{id}` | One note, full detail |
| `GET /api/data/people` | People/clients Lucid knows |
| `GET /api/data/action-items` | Every action item, tagged with its source note |

Each note includes: `id`, `created_at`, `headline`, `summary`, `sentiment`,
`people`, `key_points`, `action_items`, `link`. Add `full=true` (or fetch a
single note) for `ideas`, `timeline`, `notable_quotes`, `plans`, `commitments`,
and `relationship_dynamics`.

### curl
```bash
curl -H "X-API-Key: lkd_xxx" "https://your-lucid-url/api/data/notes?limit=5"
curl -H "X-API-Key: lkd_xxx" "https://your-lucid-url/api/data/action-items"
```

### Python
```python
import requests
H = {"X-API-Key": "lkd_xxx"}
base = "https://your-lucid-url"
notes = requests.get(f"{base}/api/data/notes", headers=H, params={"limit": 20}).json()
people = requests.get(f"{base}/api/data/people", headers=H).json()
```

## 3. Connect it as MCP tools (recommended for agents)

Lucid ships a zero-dependency MCP server at `tools/lucid_mcp.py`. It gives your
agent these tools: `lucid_list_notes`, `lucid_search_notes`, `lucid_get_note`,
`lucid_list_people`, `lucid_list_action_items`.

**Claude Code:**
```bash
claude mcp add lucid \
  -e LUCID_URL=https://your-lucid-url \
  -e LUCID_API_KEY=lkd_xxx \
  -- python /absolute/path/to/lucid/tools/lucid_mcp.py
```

**Claude Desktop** — add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "lucid": {
      "command": "python",
      "args": ["C:\\Users\\orion\\OneDrive\\Desktop\\code\\lucid\\tools\\lucid_mcp.py"],
      "env": {
        "LUCID_URL": "https://your-lucid-url",
        "LUCID_API_KEY": "lkd_xxx"
      }
    }
  }
}
```
Restart the app and your agent can ask things like *"search my Lucid notes for
the Acme meeting"* or *"list my open action items."*

## 4. Drop-in system prompt (for an agent without MCP)

> You can read the user's Lucid notes via a REST API. Base URL: `<BASE>`.
> Authenticate with header `X-API-Key: <KEY>`. Endpoints:
> `GET /api/data/notes?limit=&since=YYYY-MM-DD&full=true`,
> `GET /api/data/notes/{id}`, `GET /api/data/people`,
> `GET /api/data/action-items`. It's read-only — never attempt writes.
