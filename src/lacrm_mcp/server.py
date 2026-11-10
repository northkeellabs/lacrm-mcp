"""FastMCP server exposing a small set of LACRM workflows as tools.

Note: no ``from __future__ import annotations`` here; FastMCP inspects the
tool signatures at runtime.
"""

import argparse
import functools
import logging
import sys
from datetime import date
from typing import Annotated, Any, Awaitable, Callable, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .client import LacrmClient, LacrmError
from .config import ConfigError, Settings
from .security import DATA_NOTICE, redact, sanitize

log = logging.getLogger("lacrm_mcp")

# LACRM ids ("Uid") are ~31-digit numbers that must be handled as strings.
Uid = Annotated[str, Field(pattern=r"^\d{1,40}$", description="LACRM record id (digits only, passed as a string)")]
MaxResults = Annotated[int, Field(ge=1, le=50, description="Results per page (1-50)")]
PageNo = Annotated[int, Field(ge=1, le=1000, description="Page number, starting at 1")]

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def _display_name(rec: dict) -> str:
    name = rec.get("Name")
    if isinstance(name, dict):
        parts = [name.get(k) for k in ("Salutation", "FirstName", "MiddleName", "LastName", "Suffix")]
        return " ".join(p for p in parts if p)
    return name or rec.get("Company Name") or ""


def _first_text(items: Any) -> str | None:
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0].get("Text")
    return None


def _contact_summary(rec: dict) -> dict:
    return {
        "contact_id": rec.get("ContactId"),
        "is_company": rec.get("IsCompany"),
        "name": _display_name(rec),
        "company_name": rec.get("Company Name") if not rec.get("IsCompany") else None,
        "job_title": rec.get("Job Title"),
        "email": _first_text(rec.get("Email")),
        "phone": _first_text(rec.get("Phone")),
        "assigned_to": rec.get("AssignedTo"),
        "last_update": rec.get("LastUpdate"),
    }


def _wrap(results: list, has_more: Any, page: int) -> dict:
    return sanitize({"data_notice": DATA_NOTICE, "page": page,
                     "has_more_results": bool(has_more), "count": len(results), "results": results})


def build_server(settings: Settings, client: LacrmClient | None = None) -> FastMCP:
    client = client or LacrmClient(settings)
    mcp = FastMCP(
        "lacrm",
        instructions="Unofficial tools for reading and updating a Less Annoying CRM account."
        + (" This server is in read-only mode." if settings.read_only else ""),
    )

    def tool(annotations: ToolAnnotations, *, write: bool = False):
        """Register a tool; write tools are omitted entirely in read-only mode."""
        def deco(fn: Callable[..., Awaitable[Any]]):
            @functools.wraps(fn)
            async def guarded(*a, **kw):
                if write and settings.read_only:
                    raise ToolError("This server is in read-only mode; write tools are disabled.")
                try:
                    return await fn(*a, **kw)
                except ToolError:
                    raise
                except LacrmError as exc:
                    raise ToolError(redact(str(exc), [settings.api_key])) from None
                except Exception as exc:  # never leak internals or secrets
                    log.error("unexpected error in %s: %s", fn.__name__, type(exc).__name__)
                    raise ToolError(f"Unexpected error ({type(exc).__name__}).") from None
            if write and settings.read_only:
                return guarded
            return mcp.tool(annotations=annotations)(guarded)
        return deco

    async def _default_user() -> str:
        user = await client.call("GetUser")
        return str(user["UserId"])

    # ------------------------------------------------------------- read tools

    @tool(READ)
    async def search_contacts(
        query: Annotated[str | None, Field(max_length=200, description="Text to search for in contact/company fields. Omit to list recent records.")] = None,
        record_type: Annotated[Literal["Contacts", "Companies"] | None, Field(description="Restrict to people or companies")] = None,
        sort_by: Literal["Relevance", "FirstName", "LastName", "CompanyName", "DateCreated", "LastUpdate"] = "Relevance",
        max_results: MaxResults = 20,
        page: PageNo = 1,
    ) -> dict:
        """Search Less Annoying CRM contacts and companies by text (calls GetContacts). Returns a short summary per record (id, name, company, title, first email and phone, owner, last update), paged, at most 50 per page. Use get_contact for full details."""
        params: dict[str, Any] = {"SortBy": sort_by, "SortDirection": "Descending",
                                  "MaxNumberOfResults": max_results, "Page": page}
        if query:
            params["SearchTerms"] = query
        if record_type:
            params["RecordTypeFilter"] = record_type
        data = await client.call("GetContacts", params)
        results = [_contact_summary(r) for r in (data or {}).get("Results", []) if isinstance(r, dict)]
        return _wrap(results, (data or {}).get("HasMoreResults"), page)

    @tool(READ)
    async def get_contact(contact_id: Uid) -> dict:
        """Get the full record for one contact or company by id (calls GetContact), including emails, phones, addresses, websites and background info. Text fields are truncated at 2000 characters."""
        data = await client.call("GetContact", {"ContactId": contact_id})
        return sanitize({"data_notice": DATA_NOTICE, "contact": data})

    @tool(READ)
    async def list_contact_notes(contact_id: Uid, max_results: MaxResults = 20, page: PageNo = 1) -> dict:
        """List the notes attached to a contact or company (calls GetNotesAttachedToContact): note id, text, author user id and the date shown in the contact history. Paged, at most 50 per page."""
        data = await client.call("GetNotesAttachedToContact",
                                 {"ContactId": contact_id, "MaxNumberOfResults": max_results, "Page": page})
        results = [{"note_id": n.get("NoteId"), "date": n.get("DateDisplayedInHistory"),
                    "user_id": n.get("UserId"), "note": n.get("Note")}
                   for n in (data or {}).get("Results", []) if isinstance(n, dict)]
        return _wrap(results, (data or {}).get("HasMoreResults"), page)

    @tool(READ)
    async def list_tasks(
        start_date: Annotated[date, Field(description="Earliest task date, YYYY-MM-DD")],
        end_date: Annotated[date, Field(description="Latest task date, YYYY-MM-DD")],
        completion_status: Literal["Both", "Incomplete", "Complete"] = "Incomplete",
        contact_id: Annotated[Uid | None, Field(description="Only tasks attached to this contact")] = None,
        max_results: MaxResults = 20,
        page: PageNo = 1,
    ) -> dict:
        """List tasks due within a date range (calls GetTasks), optionally filtered by completion status or by a contact. Returns task id, name, due date, assignee and description. Paged, at most 50 per page."""
        if end_date < start_date:
            raise ToolError("end_date must not be before start_date.")
        if (end_date - start_date).days > 366:
            raise ToolError("Date range may not exceed 366 days.")
        params: dict[str, Any] = {"StartDate": start_date.isoformat(), "EndDate": end_date.isoformat(),
                                  "CompletionStatus": completion_status,
                                  "MaxNumberOfResults": max_results, "Page": page}
        if contact_id:
            params["ContactId"] = contact_id
        data = await client.call("GetTasks", params)
        results = [{k: t.get(src) for k, src in (("task_id", "TaskId"), ("name", "Name"), ("due_date", "DueDate"),
                                                  ("assigned_to", "AssignedTo"), ("description", "Description"),
                                                  ("contact_id", "ContactId"), ("is_complete", "IsComplete"))
                    if src in t}
                   for t in (data or {}).get("Results", []) if isinstance(t, dict)]
        return _wrap(results, (data or {}).get("HasMoreResults"), page)

    @tool(READ)
    async def list_pipelines() -> dict:
        """List the account's pipelines and their statuses (calls GetPipelines, without custom field definitions). Use it to find the pipeline and status ids needed by list_pipeline_items and update_pipeline_item_status."""
        data = await client.call("GetPipelines", {"IncludeCustomFields": False})
        pipelines = [{"pipeline_id": p.get("PipelineId"), "name": p.get("Name"),
                      "statuses": [{"status_id": s.get("StatusId"), "name": s.get("Name"),
                                    "is_active": s.get("IsActive")} for s in p.get("Statuses", [])
                                   if isinstance(s, dict)]}
                     for p in (data or []) if isinstance(p, dict)]
        return sanitize({"data_notice": DATA_NOTICE, "pipelines": pipelines})

    @tool(READ)
    async def list_pipeline_items(
        pipeline_id: Uid,
        status_ids: Annotated[list[Uid] | None, Field(max_length=20, description="Only items in these statuses; omit for all active statuses")] = None,
        sort_by: Literal["Status", "DateCreated", "LastUpdate"] = "Status",
        max_results: MaxResults = 20,
        page: PageNo = 1,
    ) -> dict:
        """List items in one pipeline (calls GetPipelineItems), optionally limited to certain statuses. Returns item id, contact id and name, status, last update and last note. Paged, at most 50 per page."""
        params: dict[str, Any] = {"PipelineId": pipeline_id, "SortBy": sort_by,
                                  "MaxNumberOfResults": max_results, "Page": page}
        if status_ids:
            params["StatusFilter"] = status_ids
        data = await client.call("GetPipelineItems", params)
        results = []
        for i in (data or {}).get("Results", []):
            if not isinstance(i, dict):
                continue
            results.append({
                "pipeline_item_id": i.get("PipelineItemId"), "contact_id": i.get("ContactId"),
                "contact_name": (i.get("ContactMetaData") or {}).get("Name"),
                "pipeline": (i.get("PipelineMetaData") or {}).get("Name"),
                "status_id": i.get("StatusId"), "status": (i.get("StatusMetaData") or {}).get("Name"),
                "last_update": i.get("LastUpdate"), "last_note": i.get("LastNote"),
            })
        return _wrap(results, (data or {}).get("HasMoreResults"), page)

    # ------------------------------------------------------------ write tools

    @tool(WRITE, write=True)
    async def create_contact(
        name: Annotated[str, Field(min_length=1, max_length=200, description="Full name of the person, or the company name")],
        is_company: Annotated[bool, Field(description="True to create a company record")] = False,
        email: Annotated[str | None, Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")] = None,
        phone: Annotated[str | None, Field(max_length=40)] = None,
        company_name: Annotated[str | None, Field(max_length=200, description="Employer; linked or created if missing. Ignored for companies.")] = None,
        job_title: Annotated[str | None, Field(max_length=200)] = None,
        background_info: Annotated[str | None, Field(max_length=5000)] = None,
        assigned_to: Annotated[Uid | None, Field(description="User id to own the record; defaults to the API key's user")] = None,
    ) -> dict:
        """Create a new contact or company (calls CreateContact). Does not check for duplicates. Returns the new contact id."""
        owner = assigned_to or await _default_user()
        params: dict[str, Any] = {"IsCompany": is_company, "AssignedTo": owner}
        params["Company Name" if is_company else "Name"] = name
        if email:
            params["Email"] = [{"Text": email}]
        if phone:
            params["Phone"] = [{"Text": phone}]
        if company_name and not is_company:
            params["Company Name"] = company_name
        if job_title and not is_company:
            params["Job Title"] = job_title
        if background_info:
            params["Background Info"] = background_info
        data = await client.call("CreateContact", params, idempotent=False)
        return {"contact_id": (data or {}).get("ContactId")}

    @tool(WRITE, write=True)
    async def add_note(contact_id: Uid, note: Annotated[str, Field(min_length=1, max_length=5000)]) -> dict:
        """Attach a plain-text note to an existing contact or company (calls CreateNote), timestamped now. Returns the new note id."""
        data = await client.call("CreateNote", {"ContactId": contact_id, "Note": note}, idempotent=False)
        return {"note_id": (data or {}).get("NoteId")}

    @tool(WRITE, write=True)
    async def create_task(
        name: Annotated[str, Field(min_length=1, max_length=200)],
        due_date: Annotated[date | None, Field(description="YYYY-MM-DD")] = None,
        description: Annotated[str | None, Field(max_length=5000)] = None,
        contact_id: Annotated[Uid | None, Field(description="Attach the task to this contact")] = None,
        assigned_to: Annotated[Uid | None, Field(description="User id; LACRM defaults to the API key's user")] = None,
    ) -> dict:
        """Create a task (calls CreateTask), optionally attached to a contact. Returns the new task id."""
        params: dict[str, Any] = {"Name": name}
        if due_date:
            params["DueDate"] = due_date.isoformat()
        if description:
            params["Description"] = description
        if contact_id:
            params["ContactId"] = contact_id
        if assigned_to:
            params["AssignedTo"] = assigned_to
        data = await client.call("CreateTask", params, idempotent=False)
        return {"task_id": (data or {}).get("TaskId")}

    @tool(WRITE, write=True)
    async def update_pipeline_item_status(
        pipeline_item_id: Uid,
        status_id: Annotated[Uid, Field(description="Target status id (see list_pipelines)")],
        note: Annotated[str | None, Field(max_length=2000, description="Optional note recorded in the item's history")] = None,
    ) -> dict:
        """Move a pipeline item to another status (calls EditPipelineItem). Status automations are explicitly not run. Returns a confirmation."""
        params: dict[str, Any] = {"PipelineItemId": pipeline_item_id, "StatusId": status_id,
                                  "RunStatusAutomation": False}
        if note:
            params["Note"] = note
        await client.call("EditPipelineItem", params, idempotent=False)
        return {"ok": True, "pipeline_item_id": pipeline_item_id, "status_id": status_id}

    mcp._lacrm_client = client  # exposed for tests / cleanup
    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lacrm-mcp", description="Unofficial MCP server for Less Annoying CRM.")
    parser.add_argument("--read-only", action="store_true", help="Disable all write tools (same as LACRM_READ_ONLY=1).")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)  # stdout is the MCP channel
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings.from_env(force_read_only=args.read_only)
    except ConfigError as exc:
        print(f"lacrm-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2)
    build_server(settings).run()  # stdio


if __name__ == "__main__":
    main()
