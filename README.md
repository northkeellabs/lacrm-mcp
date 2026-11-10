# lacrm-mcp

<!-- mcp-name: com.northkeellabs/lacrm-mcp -->

An MCP (Model Context Protocol) server that lets AI assistants such as Claude work with a
[Less Annoying CRM](https://www.lessannoyingcrm.com) account: search contacts, read notes and
tasks, look at pipelines, and (optionally) add notes, tasks and contacts or move pipeline items.

> **Independent, unofficial project.** lacrm-mcp is built by Northkeel Labs against LACRM's
> public API v2 documentation. It is not affiliated with, endorsed by, or supported by Less
> Annoying CRM. "Less Annoying CRM" and "LACRM" are names of their respective owner and are
> used only to describe compatibility.

## 60-second quickstart

1. In LACRM go to **Settings > Programmer API** and create an API key. Give it only the
   permissions you need (read-only is enough for the six read tools).
2. Install [uv](https://docs.astral.sh/uv/). `uvx` fetches lacrm-mcp from
   [PyPI](https://pypi.org/project/lacrm-mcp/) on first run (or use `pip install lacrm-mcp`).

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "lacrm": {
      "command": "uvx",
      "args": ["lacrm-mcp", "--read-only"],
      "env": { "LACRM_API_KEY": "your-api-key" }
    }
  }
}
```

**Claude Code:**

```bash
claude mcp add lacrm --env LACRM_API_KEY=your-api-key -- uvx lacrm-mcp --read-only
```

Drop `--read-only` when you want the write tools. Then ask, for example: "Find contacts at
Acme and show their latest notes" or "What tasks are due this week?"

## Tools

| Tool | LACRM function | Writes? |
|---|---|---|
| `search_contacts` | GetContacts | no |
| `get_contact` | GetContact | no |
| `list_contact_notes` | GetNotesAttachedToContact | no |
| `list_tasks` | GetTasks | no |
| `list_pipelines` | GetPipelines | no |
| `list_pipeline_items` | GetPipelineItems | no |
| `create_contact` | CreateContact (+ GetUser for the default owner) | yes |
| `add_note` | CreateNote | yes |
| `create_task` | CreateTask | yes |
| `update_pipeline_item_status` | EditPipelineItem | yes |

There are no delete tools. List tools return at most 50 results per page and report
`has_more_results`; long text fields are truncated at 2,000 characters.

**Tested against a live Less Annoying CRM account on 2026-11-10:** all 10 tools, in normal and
read-only mode, over a real MCP stdio connection.

## Read-only mode

Set `LACRM_READ_ONLY=1` or pass `--read-only`. The four write tools are then not registered at
all (the model never sees them), and a second check refuses writes if one is somehow called.
Combine this with a read-only API key for defence in depth.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `LACRM_API_KEY` | required | API key; read only from the environment |
| `LACRM_READ_ONLY` | off | `1`/`true` disables write tools |
| `LACRM_TIMEOUT_SECONDS` | 20 | per-request timeout (1-120) |
| `LACRM_MAX_RETRIES` | 3 | retries for 429 (and, for reads only, 5xx/timeouts); 0-6 |

## How it talks to LACRM

POST to `https://api.lessannoyingcrm.com/v2/` with an `Authorization: <key>` header and a JSON
body `{"Function": "...", "Parameters": {...}}`, per the API v2 documentation. HTTP 400 responses
carry `ErrorCode` and an error description, which are surfaced (with secrets redacted). The docs
do not describe rate limits, so on HTTP 429 the client retries with exponential backoff
(honouring `Retry-After` if sent, capped at 30 s) and reports the wait in the final error.
Writes are never retried on 5xx or timeout, to avoid duplicate records.

## Security notes

See [SECURITY.md](SECURITY.md). In short: CRM text is untrusted input to the model, so results are
size-bounded and labelled as data; write access is opt-in and narrow; the key never leaves the
environment or appears in logs or errors. Review what an assistant proposes before approving
write actions in your client.

## Development

```bash
uv sync --extra dev      # or: python -m venv .venv && pip install -e '.[dev]'
uv run pytest            # all HTTP is mocked; no network or API key needed
```

Requires Python 3.11+ and the MCP Python SDK 1.x (`mcp<2`, which provides `FastMCP`).

## Licence

MIT. See [LICENSE](LICENSE).
