import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from fleet.settings import LocalSettings
from fleet.storage import INVENTORY_PAGE_SQL, initialize_inventory


ADVISORY_URL = "https://www.cisco.com/c/en/us/support/docs/csa/cisco-sa-cat9k-PtmD7bgy.html"
Finding = Literal["affected", "unaffected", "needs_evidence"]
ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
TenantId = Annotated[str, Query(pattern=ID_PATTERN)]
AssetId = Annotated[str, PathParam(pattern=ID_PATTERN)]
Cursor = Annotated[str | None, Query(pattern=ID_PATTERN)]
bearer = HTTPBearer(auto_error=False)


class Tenant(BaseModel):
    id: str
    label: str


class Principal(BaseModel):
    id: str
    label: str
    tenants: list[Tenant]


class PrincipalList(BaseModel):
    items: list[Principal]


class SessionRequest(BaseModel):
    principal_id: str = Field(pattern=ID_PATTERN)


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class Asset(BaseModel):
    id: str
    name: str
    tenant_id: str
    site_id: str
    site_name: str
    product: str
    running_release: str
    finding: Finding
    reason: str
    observed_at: datetime
    advisory_id: Literal["CVE-2025-20311"] = "CVE-2025-20311"
    advisory_revision: Literal["1.1"] = "1.1"
    advisory_url: str = ADVISORY_URL


class InventoryPage(BaseModel):
    items: list[Asset]
    next_cursor: str | None
    total: int


def get_db(request: Request):
    settings: LocalSettings = request.app.state.settings
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


Database = Annotated[psycopg.Connection, Depends(get_db)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = LocalSettings.from_env(os.environ)
    seed = json.loads(settings.fixture_path.read_text(encoding="utf-8"))
    with psycopg.connect(settings.database_url) as conn:
        initialize_inventory(conn, seed)
    app.state.settings = settings
    yield


app = FastAPI(title="Fleet Rehearsal", version="0.1.0", lifespan=lifespan)


def principal_id(
    db: Database,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials and len(credentials.credentials) == 43:
        token_hash = hashlib.sha256(credentials.credentials.encode()).digest()
        session = db.execute(
            "SELECT principal_id FROM demo_sessions WHERE token_hash = %s AND expires_at > now()",
            (token_hash,),
        ).fetchone()
        if session:
            return session["principal_id"]
    raise HTTPException(401, "Sign in with a demo identity.", headers={"WWW-Authenticate": "Bearer"})


Caller = Annotated[str, Depends(principal_id)]


def require_tenant(db: psycopg.Connection, caller: str, tenant: str) -> None:
    grant = db.execute(
        "SELECT 1 FROM grants WHERE principal_id = %s AND tenant_id = %s LIMIT 1",
        (caller, tenant),
    ).fetchone()
    if grant is None:
        raise HTTPException(403, "This identity does not have access to that customer.")


@app.get("/healthz")
def health(db: Database) -> dict[str, str]:
    db.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/v1/demo/principals")
def demo_principals(db: Database) -> PrincipalList:
    rows = db.execute(
        """SELECT p.id, p.label,
            json_agg(json_build_object('id', t.id, 'label', t.label) ORDER BY t.id) AS tenants
        FROM principals p
        JOIN (SELECT DISTINCT principal_id, tenant_id FROM grants) g ON g.principal_id = p.id
        JOIN tenants t ON t.id = g.tenant_id
        GROUP BY p.id, p.label
        ORDER BY CASE WHEN p.id = 'demo-northstar' THEN 0 ELSE 1 END, p.id"""
    ).fetchall()
    return PrincipalList(items=[Principal(**row) for row in rows])


@app.post("/v1/demo/session")
def new_session(request: SessionRequest, db: Database) -> SessionResponse:
    if db.execute("SELECT 1 FROM grants WHERE principal_id = %s LIMIT 1", (request.principal_id,)).fetchone() is None:
        raise HTTPException(404, "Demo identity not found.")
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM demo_sessions WHERE expires_at <= now()")
    db.execute(
        "INSERT INTO demo_sessions VALUES (%s, %s, now() + interval '1 hour')",
        (hashlib.sha256(token.encode()).digest(), request.principal_id),
    )
    db.commit()
    return SessionResponse(access_token=token)


@app.get("/v1/assets", responses={401: {"description": "Invalid demo session"}, 403: {"description": "Tenant forbidden"}})
def inventory(
    db: Database,
    caller: Caller,
    tenant_id: TenantId,
    finding: Finding | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    after: Cursor = None,
) -> InventoryPage:
    require_tenant(db, caller, tenant_id)
    result = db.execute(
        INVENTORY_PAGE_SQL,
        {"caller": caller, "tenant": tenant_id, "finding": finding, "after": after, "fetch": limit + 1},
    ).fetchone()
    rows = result["items"]
    items = [Asset(**row) for row in rows[:limit]]
    return InventoryPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None, total=result["total"])


@app.get("/v1/assets/{asset_id}", responses={401: {"description": "Invalid demo session"}, 403: {"description": "Tenant forbidden"}, 404: {"description": "Asset not found in granted scope"}})
def evidence(asset_id: AssetId, tenant_id: TenantId, db: Database, caller: Caller) -> Asset:
    require_tenant(db, caller, tenant_id)
    row = db.execute(
        """SELECT e.* FROM inventory_evidence e
        JOIN grants g ON (g.tenant_id, g.site_id) = (e.tenant_id, e.site_id)
        WHERE g.principal_id = %s AND e.tenant_id = %s AND e.id = %s""",
        (caller, tenant_id, asset_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Device not found in the granted sites.")
    return Asset(**row)
