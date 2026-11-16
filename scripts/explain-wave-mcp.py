"""Read one blocked rehearsal decision via the caller's existing audit API grant."""

import json
import os
import re
from http.client import HTTPConnection
from typing import Any
from urllib.parse import urlencode

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations


mcp = MCPServer("Fleet Rehearsal")
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                      idempotent_hint=True, open_world_hint=False))
def explain_blocked_wave(tenant_id: str, site_id: str, audit_id: str) -> dict[str, Any]:
    """Return the existing scoped wave_blocked audit event for the supplied audit ID."""
    number = re.fullmatch(r"audit-([1-9][0-9]{0,18})", audit_id)
    if not (ID.fullmatch(tenant_id) and ID.fullmatch(site_id) and number):
        raise ToolError("Supply a tenant, site and blocked rehearsal audit ID.")
    configured = os.environ.get("FLEET_LOCAL_API", "http://127.0.0.1:8080/api")
    origin = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})(/api)?", configured)
    token = os.environ.get("FLEET_AUDIT_BEARER")
    if not origin or int(origin[1]) > 65535 or not token:
        raise ToolError("Set a loopback Fleet API and a scoped session in the MCP host.")

    desired = int(number[1])
    prefix = origin[2] or ""
    route = f"{prefix}/v1/tenants/{tenant_id}/sites/{site_id}/audit"
    query: dict[str, int | str] = {"limit": 100}
    connection = HTTPConnection("127.0.0.1", int(origin[1]), timeout=3)
    try:
        for _ in range(10):
            connection.request("GET", f"{route}?{urlencode(query)}", headers={"Authorization": f"Bearer {token}"})
            response = connection.getresponse()
            if response.status in (401, 403, 404):
                break
            if response.status != 200:
                raise ToolError("Fleet audit is temporarily unavailable.")
            page = json.loads(response.read())
            for event in page["items"]:
                if event["audit_id"] == audit_id and event["kind"] == "wave_blocked":
                    return event
                if int(event["audit_id"].removeprefix("audit-")) >= desired:
                    raise ToolError("No blocked decision is available for that scope and audit ID.")
            cursor = page["next_cursor"]
            if not cursor:
                break
            query["after"] = cursor
    except (ValueError, KeyError, OSError) as error:
        raise ToolError("Fleet audit is temporarily unavailable.") from error
    finally:
        connection.close()
    raise ToolError("No blocked decision is available for that scope and audit ID.")


if __name__ == "__main__":
    mcp.run()
