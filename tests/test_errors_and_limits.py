import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import API_KEY, UID, call, make_server, ok


async def test_429_retries_then_succeeds_honouring_retry_after(server, rec):
    rec.queue(httpx.Response(429, headers={"Retry-After": "2"}),
              httpx.Response(429),
              ok({"HasMoreResults": False, "Results": []}))
    out = await call(server, "search_contacts", {})
    assert out["count"] == 0
    assert len(rec.requests) == 3
    assert rec.sleeps == [2.0, 1.0]  # Retry-After first, then exponential (0.5 * 2**1)


async def test_429_exhausted_surfaces_rate_limit_info(server, rec):
    rec.queue(httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(ToolError) as e:
        await call(server, "search_contacts", {})
    msg = str(e.value)
    assert "rate limited" in msg and "7s" in msg
    assert len(rec.requests) == 4  # 1 + 3 retries


async def test_429_backoff_capped(server, rec):
    rec.queue(httpx.Response(429, headers={"Retry-After": "9999"}), ok({"Results": []}))
    await call(server, "search_contacts", {})
    assert rec.sleeps == [30.0]


async def test_write_not_retried_on_5xx(server, rec):
    rec.queue(httpx.Response(503))
    with pytest.raises(ToolError, match="server error"):
        await call(server, "add_note", {"contact_id": UID, "note": "x"})
    assert len(rec.requests) == 1


async def test_write_retried_on_429(server, rec):
    rec.queue(httpx.Response(429), ok({"NoteId": UID}))
    out = await call(server, "add_note", {"contact_id": UID, "note": "x"})
    assert out["note_id"] == UID and len(rec.requests) == 2


async def test_read_retried_on_5xx(server, rec):
    rec.queue(httpx.Response(502), ok({"Results": []}))
    await call(server, "search_contacts", {})
    assert len(rec.requests) == 2


async def test_timeout_reported_and_reads_retried(rec):
    server = make_server(rec, max_retries=1)
    rec.queue(httpx.ReadTimeout("slow"))
    with pytest.raises(ToolError, match="timed out"):
        await call(server, "get_contact", {"contact_id": UID})
    assert len(rec.requests) == 2


async def test_write_timeout_not_retried(rec):
    server = make_server(rec)
    rec.queue(httpx.ReadTimeout("slow"))
    with pytest.raises(ToolError, match="timed out"):
        await call(server, "add_note", {"contact_id": UID, "note": "x"})
    assert len(rec.requests) == 1


async def test_client_has_timeout_configured(rec):
    server = make_server(rec, timeout=5.0)
    assert server._lacrm_client._http.timeout.read == 5.0


@pytest.mark.parametrize("key", ["ErrorDescription", "ErrorDecription"])
async def test_400_error_object_both_spellings(server, rec, key):
    rec.queue(httpx.Response(400, json={"ErrorCode": "abc123", key: "Contact not found."}))
    with pytest.raises(ToolError) as e:
        await call(server, "get_contact", {"contact_id": UID})
    assert "Contact not found." in str(e.value) and "abc123" in str(e.value)
    assert len(rec.requests) == 1


async def test_401_message(server, rec):
    rec.queue(httpx.Response(401, text="nope"))
    with pytest.raises(ToolError, match="not authorised"):
        await call(server, "get_contact", {"contact_id": UID})


async def test_api_key_redacted_from_api_error_text(server, rec):
    rec.queue(httpx.Response(400, json={
        "ErrorCode": "x", "ErrorDescription": f"Bad key {API_KEY} in Authorization: {API_KEY}"}))
    with pytest.raises(ToolError) as e:
        await call(server, "get_contact", {"contact_id": UID})
    assert API_KEY not in str(e.value)
    assert "[REDACTED]" in str(e.value)


async def test_unexpected_exception_does_not_leak(server, rec):
    rec.queue(ok("not-a-dict-or-list"))
    # search_contacts tolerates odd payloads; force a crash via a non-dict 'Results' item type
    rec.responses = [ok({"Results": 5})]
    with pytest.raises(ToolError) as e:
        await call(server, "search_contacts", {})
    assert API_KEY not in str(e.value)


async def test_network_error_message_has_no_secret(server, rec):
    rec.queue(httpx.ConnectError(f"cannot connect with {API_KEY}"))
    with pytest.raises(ToolError) as e:
        await call(server, "get_contact", {"contact_id": UID})
    assert API_KEY not in str(e.value)


async def test_api_key_not_in_logs(server, rec, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    rec.queue(httpx.Response(429), httpx.Response(500), ok({"Results": []}))
    await call(server, "search_contacts", {})
    assert API_KEY not in caplog.text
