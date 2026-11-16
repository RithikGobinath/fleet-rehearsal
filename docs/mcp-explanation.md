# Explain a blocked wave locally

After running a blocked Harbor rehearsal, use one read-only MCP tool to retrieve the same decision shown in the audit timeline. The MCP server uses standard input and output and opens no listening socket.

1. In a Python 3.12 virtual environment, install `mcp==2.2.0`. See the [official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/v2/).
2. Configure your local MCP host to run `python scripts/explain-wave-mcp.py` from the repository. Set `FLEET_AUDIT_BEARER` to the active synthetic session token for the caller. Set `FLEET_LOCAL_API` only if the loopback port differs from the default `http://127.0.0.1:8080/api`. Keep the token in the host's environment.
3. Call `explain_blocked_wave` with the tenant, site and audit ID from the console. The tool returns the existing audit row. An ungranted scope and an unavailable decision return the same generic error.

Run `python scripts/test-explain-wave-mcp.py` after the blocked demo to exercise the local MCP transport, compare its result with the API, and verify cross-tenant denial. The lookup reads up to 1,000 authorized audit rows. The browser audit view supports older entries through pagination.
