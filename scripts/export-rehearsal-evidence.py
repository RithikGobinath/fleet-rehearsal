"""Export read-only Fleet Rehearsal API evidence for selected synthetic plans."""

import argparse
import base64
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
import re
import secrets
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def identifier(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value):
        raise ValueError("Expected a Fleet identifier")
    return value


def api_root(value):
    url = urlsplit(value)
    encrypted = url.scheme == "https"
    loopback = url.scheme == "http" and url.hostname in ("127.0.0.1", "::1", "localhost")
    if not (encrypted or loopback) or not url.hostname or "@" in url.netloc:
        raise ValueError("Use an HTTPS API base URL or HTTP on loopback")
    if url.query or url.fragment or url.path.rstrip("/") not in ("", "/api"):
        raise ValueError("The API base URL may end in /api and must have no query or fragment")
    return value.rstrip("/")


def read_json(opener, url, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with opener.open(Request(url, headers=headers), timeout=20) as response:
        limit = 2 * 1024 * 1024
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("API response exceeded 2 MiB")
    return json.loads(data)


def auth0_claims(token, issuer):
    # The hosted API authenticates this same token before the excerpt is emitted.
    _, payload, _ = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    subject = claims["sub"]
    url = urlsplit(issuer)
    if (url.scheme != "https" or not url.hostname or url.netloc != url.hostname
            or url.path != "/" or url.query or url.fragment):
        raise ValueError("Use the HTTPS Auth0 issuer with a trailing slash")
    if claims["iss"] != issuer or not isinstance(subject, str) or not subject:
        raise ValueError("Token issuer or subject does not match the expected Auth0 session")
    # Use a fresh key so the public digest cannot correlate demo users across exports.
    return {"iss": issuer, "sub_redacted": hmac.new(secrets.token_bytes(32), subject.encode(), sha256).hexdigest()}


def export(args, token):
    if not re.fullmatch(r"[A-Za-z0-9_.~-]+", token):
        raise ValueError("FLEET_EVIDENCE_TOKEN must contain only a bearer token")
    base = api_root(args.api_base)
    if args.auth0_issuer and urlsplit(base).scheme != "https":
        raise ValueError("Auth0 evidence requires the hosted HTTPS API")
    tenant, site = identifier(args.tenant), identifier(args.site)
    selected = {identifier(plan) for plan in args.plan}
    scope = f"{base}/v1/tenants/{tenant}/sites/{site}"
    opener = build_opener(NoRedirects())

    def get(path, query=None):
        suffix = "?" + urlencode(query) if query else ""
        return read_json(opener, f"{scope}/{path}{suffix}", token)

    evidence = {
        "source": "site_api",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_base": base,
        "tenant_id": tenant,
        "site_id": site,
        "plan_ids": sorted(selected),
        "context": get("rehearsal/context"),
    }
    if args.source_commit:
        evidence["source_commit"] = args.source_commit
    if args.auth0_issuer:
        try:
            read_json(opener, f"{scope}/rehearsal/context")
        except HTTPError as e:
            if e.code != 401:
                raise
        else:
            raise ValueError("The hosted API accepted an anonymous request")
        evidence["auth0_claims"] = auth0_claims(token, args.auth0_issuer)

    events, cursors = [], set()
    query = {"limit": 25}
    while True:
        page = get("audit", query)
        events.extend(event for event in page["items"] if event.get("plan_id") in selected)
        cursor = page["next_cursor"]
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ValueError("API returned an invalid or repeated audit cursor")
        if len(cursors) >= 999:
            raise ValueError("Audit export exceeded 1,000 pages")
        cursors.add(cursor)
        query["after"] = cursor

    if {event["plan_id"] for event in events} != selected:
        raise ValueError("No audit events were returned for one or more requested plans")
    runs = []
    for run_id in dict.fromkeys(event["run_id"] for event in events if event.get("run_id")):
        run = get(f"runs/{identifier(run_id)}")
        if run["run_id"] != run_id or run["plan_id"] not in selected:
            raise ValueError("Run response does not match the requested evidence scope")
        runs.append(run)
    return {**evidence, "audit_events": events, "runs": runs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", required=True, help="For example, https://demo.example.com/api")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--site", required=True)
    parser.add_argument("--plan", action="append", required=True, help="Repeat for each synthetic plan ID")
    parser.add_argument("--auth0-issuer", help="Record issuer and hashed subject from the accepted access token")
    parser.add_argument("--source-commit", help="Full Git SHA of the running app, when known")
    args = parser.parse_args()
    if args.source_commit and not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        parser.error("--source-commit must be a full Git SHA")
    token = os.environ.get("FLEET_EVIDENCE_TOKEN")
    if not token:
        parser.error("Set FLEET_EVIDENCE_TOKEN to a site reader's access token")
    try:
        evidence = export(args, token)
    except HTTPError as e:
        sys.exit(f"Evidence request failed with HTTP {e.code}")
    except (URLError, TimeoutError):
        sys.exit("Could not reach the evidence API")
    except ValueError as e:
        sys.exit(f"Invalid evidence: {e}" if type(e) is ValueError else "Invalid JSON or token encoding")
    except (KeyError, TypeError):
        sys.exit("Invalid API evidence, identifier, URL or token claims")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
