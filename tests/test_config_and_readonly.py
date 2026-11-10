import pytest

from conftest import API_KEY, UID, call, make_server, ok
from lacrm_mcp.config import ConfigError, Settings
from lacrm_mcp.security import redact, sanitize
from mcp.server.fastmcp.exceptions import ToolError

WRITE_TOOLS = {"create_contact", "add_note", "create_task", "update_pipeline_item_status"}
READ_TOOLS = {"search_contacts", "get_contact", "list_contact_notes", "list_tasks",
              "list_pipelines", "list_pipeline_items"}


async def test_all_tools_listed_in_read_write_mode(server):
    assert {t.name for t in await server.list_tools()} == READ_TOOLS | WRITE_TOOLS


async def test_read_only_mode_hides_write_tools(ro_server):
    assert {t.name for t in await ro_server.list_tools()} == READ_TOOLS


@pytest.mark.parametrize("name,args", [
    ("create_contact", {"name": "A"}),
    ("add_note", {"contact_id": UID, "note": "x"}),
    ("create_task", {"name": "t"}),
    ("update_pipeline_item_status", {"pipeline_item_id": UID, "status_id": UID}),
])
async def test_read_only_mode_blocks_write_calls(ro_server, rec, name, args):
    rec.queue(ok({}))
    with pytest.raises(ToolError):
        await call(ro_server, name, args)
    assert rec.requests == []


async def test_read_only_still_reads(ro_server, rec):
    rec.queue(ok({"Results": []}))
    await call(ro_server, "search_contacts", {})
    assert len(rec.requests) == 1


def test_settings_requires_env_key():
    with pytest.raises(ConfigError, match="LACRM_API_KEY"):
        Settings.from_env({})
    with pytest.raises(ConfigError):
        Settings.from_env({"LACRM_API_KEY": "  "})


def test_settings_flags_and_bounds():
    s = Settings.from_env({"LACRM_API_KEY": "k", "LACRM_READ_ONLY": "true", "LACRM_TIMEOUT_SECONDS": "5"})
    assert s.read_only and s.timeout == 5.0
    assert Settings.from_env({"LACRM_API_KEY": "k"}, force_read_only=True).read_only
    assert not Settings.from_env({"LACRM_API_KEY": "k"}).read_only
    with pytest.raises(ConfigError):
        Settings.from_env({"LACRM_API_KEY": "k", "LACRM_TIMEOUT_SECONDS": "999"})
    with pytest.raises(ConfigError):
        Settings.from_env({"LACRM_API_KEY": "k", "LACRM_MAX_RETRIES": "x"})


def test_settings_repr_hides_key():
    s = Settings(api_key=API_KEY)
    assert API_KEY not in repr(s) and API_KEY not in str(s)


def test_redact():
    assert API_KEY not in redact(f"oops {API_KEY}", [API_KEY])
    assert API_KEY not in redact("Authorization: abcdef123456")
    assert "abcdef123456" not in redact("Authorization: abcdef123456")


def test_sanitize_limits():
    out = sanitize({"a": ["x"] * 80, "b": "y" * 3000})
    assert len(out["a"]) == 51 and "omitted" in out["a"][-1]
    assert len(out["b"]) < 2100
