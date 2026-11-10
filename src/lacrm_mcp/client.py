"""Minimal async client for the LACRM v2 API (POST {"Function", "Parameters"})."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import httpx

from .config import Settings
from .security import redact

log = logging.getLogger("lacrm_mcp")

MAX_BACKOFF = 30.0


class LacrmError(Exception):
    """An error from the LACRM API or transport. Message is already redacted."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.retry_after = retry_after


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class LacrmClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._s = settings
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(settings.timeout),
            headers={"Authorization": settings.api_key, "Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _clean(self, text: str) -> str:
        return redact(text, [self._s.api_key])

    async def call(self, function: str, parameters: dict[str, Any] | None = None, *,
                   idempotent: bool = True) -> Any:
        """Call one API function.

        429 is retried for every call (the request was rejected, not processed).
        5xx and timeouts are retried only for read calls (idempotent=True), so a
        write is never silently repeated.
        """
        body = {"Function": function, "Parameters": parameters or {}}
        attempt = 0
        while True:
            try:
                resp = await self._http.post(self._s.api_url, json=body)
            except httpx.TimeoutException as exc:
                if idempotent and attempt < self._s.max_retries:
                    await self._backoff(attempt, None)
                    attempt += 1
                    continue
                raise LacrmError(f"{function}: request timed out after {self._s.timeout:g}s") from exc
            except httpx.HTTPError as exc:
                raise LacrmError(f"{function}: network error ({type(exc).__name__})") from None

            if resp.status_code == 429:
                ra = _retry_after(resp)
                if attempt < self._s.max_retries:
                    await self._backoff(attempt, ra)
                    attempt += 1
                    continue
                raise LacrmError(
                    f"{function}: rate limited (HTTP 429) after {attempt} retries."
                    + (f" Server asked to wait {ra:g}s." if ra is not None else ""),
                    status=429, retry_after=ra,
                )
            if resp.status_code >= 500:
                if idempotent and attempt < self._s.max_retries:
                    await self._backoff(attempt, _retry_after(resp))
                    attempt += 1
                    continue
                raise LacrmError(f"{function}: LACRM server error (HTTP {resp.status_code})",
                                 status=resp.status_code)
            return self._parse(function, resp)

    async def _backoff(self, attempt: int, retry_after: float | None) -> None:
        delay = retry_after if retry_after is not None else 0.5 * (2 ** attempt)
        delay = min(delay, MAX_BACKOFF)
        log.warning("retrying after %.1fs (attempt %d)", delay, attempt + 1)
        await self._sleep(delay)

    def _parse(self, function: str, resp: httpx.Response) -> Any:
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code == 400:
            # Docs show the key as "ErrorDecription" (sic); accept both spellings.
            d = data if isinstance(data, dict) else {}
            desc = d.get("ErrorDescription") or d.get("ErrorDecription") or "Bad request"
            code = d.get("ErrorCode")
            raise LacrmError(
                self._clean(f"{function}: LACRM rejected the request"
                            f"{f' (error {code})' if code else ''}: {desc}"),
                status=400, code=str(code) if code else None)
        if resp.status_code in (401, 403):
            raise LacrmError(f"{function}: not authorised (HTTP {resp.status_code}). "
                             "Check the API key and that it has the needed permissions.",
                             status=resp.status_code)
        if resp.status_code >= 400:
            raise LacrmError(f"{function}: unexpected HTTP {resp.status_code}", status=resp.status_code)
        if not resp.content:
            return None  # functions that return nothing
        if data is None:
            raise LacrmError(f"{function}: response was not valid JSON")
        return data
