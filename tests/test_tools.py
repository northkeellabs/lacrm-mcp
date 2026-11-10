import pytest
from mcp.server.fastmcp.exceptions import ToolError

from conftest import UID, UID2, call, ok


async def test_search_contacts(server, rec):
    rec.queue(ok({"HasMoreResults": True, "Results": [
        {"ContactId": UID, "IsCompany": False, "AssignedTo": 5,
         "Name": {"FirstName": "John", "LastName": "Doe"}, "Company Name": "Acme",
         "Job Title": "CTO", "Email": [{"Text": "j@x.com"}], "Phone": [{"Text": "555"}],
         "LastUpdate": "2026-01-01"}]}))
    out = await call(server, "search_contacts", {"query": "john", "max_results": 5})
    body = rec.bodies()[0]
    assert body["Function"] == "GetContacts"
    assert body["Parameters"]["SearchTerms"] == "john"
    assert body["Parameters"]["MaxNumberOfResults"] == 5
    assert out["has_more_results"] is True
    assert out["results"][0]["name"] == "John Doe"
    assert out["results"][0]["email"] == "j@x.com"
    assert "untrusted" in out["data_notice"]


async def test_request_format_and_auth_header(server, rec):
    rec.queue(ok({"HasMoreResults": False, "Results": []}))
    await call(server, "search_contacts", {})
    req = rec.requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.lessannoyingcrm.com/v2/"
    assert req.headers["Authorization"] == "sekret-test-key-123456"
    assert set(rec.bodies()[0]) == {"Function", "Parameters"}


async def test_get_contact(server, rec):
    rec.queue(ok({"ContactId": UID, "Name": {"FirstName": "A"}, "Background Info": "x" * 5000}))
    out = await call(server, "get_contact", {"contact_id": UID})
    assert rec.bodies()[0] == {"Function": "GetContact", "Parameters": {"ContactId": UID}}
    assert len(out["contact"]["Background Info"]) < 2100
    assert "truncated" in out["contact"]["Background Info"]


async def test_list_contact_notes(server, rec):
    rec.queue(ok({"HasMoreResults": False, "Results": [
        {"NoteId": UID2, "ContactId": UID, "UserId": "1", "DateDisplayedInHistory": "d", "Note": "hello"}]}))
    out = await call(server, "list_contact_notes", {"contact_id": UID})
    assert rec.bodies()[0]["Function"] == "GetNotesAttachedToContact"
    assert out["results"][0]["note"] == "hello"


async def test_list_tasks(server, rec):
    rec.queue(ok({"HasMoreResults": False, "Results": [
        {"TaskId": UID, "Name": "Call", "DueDate": "2026-11-16", "AssignedTo": "1"}]}))
    out = await call(server, "list_tasks", {"start_date": "2026-11-01", "end_date": "2026-11-30",
                                            "contact_id": UID})
    p = rec.bodies()[0]["Parameters"]
    assert rec.bodies()[0]["Function"] == "GetTasks"
    assert p["StartDate"] == "2026-11-01" and p["EndDate"] == "2026-11-30"
    assert p["CompletionStatus"] == "Incomplete" and p["ContactId"] == UID
    assert out["results"][0]["task_id"] == UID


async def test_list_pipelines(server, rec):
    rec.queue(ok([{"PipelineId": UID, "Name": "Sales", "Fields": [{"secret": 1}],
                   "Statuses": [{"StatusId": UID2, "Name": "Prospect", "IsActive": True}]}]))
    out = await call(server, "list_pipelines")
    assert rec.bodies()[0] == {"Function": "GetPipelines", "Parameters": {"IncludeCustomFields": False}}
    assert out["pipelines"][0]["statuses"][0]["name"] == "Prospect"
    assert "Fields" not in out["pipelines"][0]


async def test_list_pipeline_items(server, rec):
    rec.queue(ok({"HasMoreResults": False, "Results": [
        {"PipelineItemId": UID, "ContactId": UID2, "StatusId": UID, "LastNote": "n",
         "ContactMetaData": {"Name": "John"}, "StatusMetaData": {"Name": "Prospect"},
         "PipelineMetaData": {"Name": "Sales"}}]}))
    out = await call(server, "list_pipeline_items", {"pipeline_id": UID, "status_ids": [UID2]})
    p = rec.bodies()[0]["Parameters"]
    assert rec.bodies()[0]["Function"] == "GetPipelineItems"
    assert p["PipelineId"] == UID and p["StatusFilter"] == [UID2]
    assert out["results"][0]["status"] == "Prospect"


async def test_create_contact_defaults_owner_from_getuser(server, rec):
    rec.queue(ok({"UserId": 77, "FirstName": "x"}), ok({"ContactId": UID}))
    out = await call(server, "create_contact", {"name": "Jane Roe", "email": "jane@x.com",
                                                "company_name": "Acme", "job_title": "CEO"})
    assert out == {"contact_id": UID}
    b = rec.bodies()
    assert b[0]["Function"] == "GetUser"
    assert b[1]["Function"] == "CreateContact"
    assert b[1]["Parameters"] == {"IsCompany": False, "AssignedTo": "77", "Name": "Jane Roe",
                                  "Email": [{"Text": "jane@x.com"}], "Company Name": "Acme",
                                  "Job Title": "CEO"}


async def test_create_company_uses_company_name_key(server, rec):
    rec.queue(ok({"ContactId": UID}))
    await call(server, "create_contact", {"name": "Acme Inc", "is_company": True, "assigned_to": UID2})
    assert len(rec.requests) == 1  # no GetUser when assigned_to is given
    p = rec.bodies()[0]["Parameters"]
    assert p["Company Name"] == "Acme Inc" and p["IsCompany"] is True and "Name" not in p


async def test_add_note(server, rec):
    rec.queue(ok({"NoteId": UID2}))
    out = await call(server, "add_note", {"contact_id": UID, "note": "Called."})
    assert out == {"note_id": UID2}
    assert rec.bodies()[0] == {"Function": "CreateNote", "Parameters": {"ContactId": UID, "Note": "Called."}}


async def test_create_task(server, rec):
    rec.queue(ok({"TaskId": UID2}))
    out = await call(server, "create_task", {"name": "Follow up", "due_date": "2026-12-01", "contact_id": UID})
    assert out == {"task_id": UID2}
    assert rec.bodies()[0]["Parameters"] == {"Name": "Follow up", "DueDate": "2026-12-01", "ContactId": UID}


async def test_update_pipeline_item_status(server, rec):
    rec.queue(httpx_empty())
    out = await call(server, "update_pipeline_item_status",
                     {"pipeline_item_id": UID, "status_id": UID2, "note": "moved"})
    assert out["ok"] is True
    assert rec.bodies()[0] == {"Function": "EditPipelineItem", "Parameters": {
        "PipelineItemId": UID, "StatusId": UID2, "RunStatusAutomation": False, "Note": "moved"}}


def httpx_empty():
    import httpx
    return httpx.Response(200, content=b"")


@pytest.mark.parametrize("name,args", [
    ("get_contact", {"contact_id": "abc"}),
    ("get_contact", {"contact_id": "1; DROP"}),
    ("search_contacts", {"max_results": 500}),
    ("search_contacts", {"max_results": 0}),
    ("search_contacts", {"page": 0}),
    ("search_contacts", {"record_type": "Vendors"}),
    ("search_contacts", {"query": "x" * 201}),
    ("list_tasks", {"start_date": "yesterday", "end_date": "2026-01-01"}),
    ("list_tasks", {"start_date": "2026-01-01"}),
    ("list_pipeline_items", {"pipeline_id": UID, "status_ids": [UID] * 21}),
    ("create_contact", {"name": ""}),
    ("create_contact", {"name": "A", "email": "not-an-email"}),
    ("add_note", {"contact_id": UID, "note": ""}),
    ("add_note", {"contact_id": UID, "note": "x" * 5001}),
    ("create_task", {"name": "x" * 201}),
    ("update_pipeline_item_status", {"pipeline_item_id": UID}),
])
async def test_validation_failures_make_no_http_call(server, rec, name, args):
    rec.queue(ok({}))
    with pytest.raises(ToolError):
        await call(server, name, args)
    assert rec.requests == []


async def test_task_date_range_checks(server, rec):
    rec.queue(ok({}))
    with pytest.raises(ToolError, match="before"):
        await call(server, "list_tasks", {"start_date": "2026-12-01", "end_date": "2026-11-01"})
    with pytest.raises(ToolError, match="366"):
        await call(server, "list_tasks", {"start_date": "2024-01-01", "end_date": "2026-11-01"})
    assert rec.requests == []


async def test_control_chars_stripped_from_results(server, rec):
    rec.queue(ok({"HasMoreResults": False, "Results": [
        {"NoteId": UID, "Note": "hi\x00\x1b[31m there"}]}))
    out = await call(server, "list_contact_notes", {"contact_id": UID})
    assert "\x00" not in out["results"][0]["note"] and "\x1b" not in out["results"][0]["note"]
