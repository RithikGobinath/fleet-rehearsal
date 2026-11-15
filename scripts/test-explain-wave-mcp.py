"""Check MCP against a running local demo after the Harbor blocked rehearsal."""

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from mcp import Client, StdioServerParameters


async def main():
    base = os.environ.get("FLEET_LOCAL_API", "http://127.0.0.1:8080/api")
    with urlopen(Request(f"{base}/v1/demo/session", data=b'{"principal_id":"demo-partner"}',
                         headers={"Content-Type": "application/json"})) as response:
        token = json.load(response)["access_token"]
    with urlopen(Request(f"{base}/v1/tenants/northstar/sites/harbor/audit?limit=100",
                         headers={"Authorization": f"Bearer {token}"})) as response:
        rows = json.load(response)["items"]
    event = next(row for row in reversed(rows) if row["kind"] == "wave_blocked")
    server = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name("explain-wave-mcp.py"))],
                                   env={"FLEET_LOCAL_API": base, "FLEET_AUDIT_BEARER": token,
                                        "PYTHONPATH": os.environ.get("PYTHONPATH", "")})
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 1 and tools[0].annotations.read_only_hint
        args = {"tenant_id": "northstar", "site_id": "harbor", "audit_id": event["audit_id"]}
        found = await client.call_tool("explain_blocked_wave", args)
        assert not found.is_error and found.structured_content == event
        denied = await client.call_tool("explain_blocked_wave", {**args, "tenant_id": "summit", "site_id": "ridge"})
        unknown = await client.call_tool("explain_blocked_wave", {**args, "audit_id": "audit-9223372036854775807"})
        assert denied.is_error and unknown.is_error and denied.content == unknown.content


if __name__ == "__main__":
    asyncio.run(main())
