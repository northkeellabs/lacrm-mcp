import json

import httpx
import pytest

from lacrm_mcp.client import LacrmClient
from lacrm_mcp.config import Settings
from lacrm_mcp.server import build_server

API_KEY = "sekret-test-key-123456"
UID = "4137270799503468142979802035166"
UID2 = "4137270801977637691866095801882"


class Recorder:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.responses: list = []  # callables or httpx.Response; consumed in order, last one repeats
        self.sleeps: list[float] = []

    def queue(self, *responses):
        self.responses.extend(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item() if callable(item) else item

    def bodies(self):
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def rec():
    return Recorder()


def make_server(rec: Recorder, **kw):
    settings = Settings(api_key=API_KEY, **kw)

    async def fake_sleep(d):
        rec.sleeps.append(d)

    client = LacrmClient(settings, transport=httpx.MockTransport(rec.handler), sleep=fake_sleep)
    return build_server(settings, client)


@pytest.fixture
def server(rec):
    return make_server(rec)


@pytest.fixture
def ro_server(rec):
    return make_server(rec, read_only=True)


def ok(payload):
    return httpx.Response(200, json=payload)


async def call(server, name, args=None):
    """Call a tool and return its decoded JSON result."""
    result = await server.call_tool(name, args or {})
    if isinstance(result, tuple):
        result = result[1] if isinstance(result[1], dict) else result[0]
    if isinstance(result, dict):
        return result.get("result", result) if set(result) == {"result"} else result
    return json.loads(result[0].text)
