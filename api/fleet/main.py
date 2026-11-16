import hashlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from functools import partial
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID, uuid4

import psycopg
from anyio import to_thread
from fastapi import Depends, FastAPI, Header, HTTPException, Path as PathParam, Query, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout, TooManyRequests
from pydantic import BaseModel, Field

from fleet.inventory_import import IMPORT_SCHEMA, InventoryBatch, read_inventory_import, store_inventory_import
from fleet.hosted_auth import HostedTokens
from fleet.metrics import observe_request, render_metrics
from fleet.migrate import migrate
from fleet import observations, rehearsal
from fleet.settings import HostedSettings, LocalSettings, runtime_settings
from fleet.storage import INVENTORY_PAGE_SQL, schema_ready


ADVISORY_URL = "https://www.cisco.com/c/en/us/support/docs/csa/cisco-sa-cat9k-PtmD7bgy.html"
Finding = Literal["affected", "unaffected", "needs_evidence"]
SiteRole = Literal["viewer", "partner_proposer", "customer_approver", "operator"]
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
    try:
        with request.app.state.db_pool.connection() as conn:
            yield conn
    except (PoolTimeout, TooManyRequests):
        raise HTTPException(503, "Database connection pool is busy.") from None


Database = Annotated[psycopg.Connection, Depends(get_db)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = runtime_settings(os.environ)
    if isinstance(settings, LocalSettings):
        await to_thread.run_sync(migrate, settings.database_url, settings.fixture_path)
    pool = ConnectionPool(
        settings.database_url, kwargs={"row_factory": dict_row, "connect_timeout": 5}, open=False,
        min_size=2, max_size=8, max_waiting=32, timeout=3,
    )
    app.state.settings = settings
    app.state.hosted_tokens = HostedTokens(settings) if isinstance(settings, HostedSettings) else None
    app.state.db_pool = pool
    try:
        await to_thread.run_sync(partial(pool.open, wait=True, timeout=10))
        if isinstance(settings, HostedSettings):
            def check_schema():
                with pool.connection() as db:
                    if not schema_ready(db):
                        raise RuntimeError("Run the release migration job before starting the hosted API.")

            await to_thread.run_sync(check_schema)
        yield
    finally:
        await to_thread.run_sync(pool.close)


app = FastAPI(title="Fleet Rehearsal", version="0.1.0", lifespan=lifespan)
log = logging.getLogger("uvicorn.error")


@app.middleware("http")
async def observe_http(request: Request, call_next):
    start = perf_counter()
    correlation = uuid4()
    token = rehearsal.request_correlation.set(correlation)
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = str(correlation)
        return response
    finally:
        rehearsal.request_correlation.reset(token)
        if request.url.path not in ("/metrics", "/healthz", "/readyz"):
            elapsed = perf_counter() - start
            observe_request(status, elapsed)
            route = getattr(request.scope.get("route"), "path", "unmatched")
            log.info("request_id=%s route=%s status=%d duration_ms=%.1f", correlation, route, status, elapsed * 1000)


def principal_id(
    db: Database,
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    settings = request.app.state.settings
    if credentials and isinstance(settings, HostedSettings):
        subject = request.app.state.hosted_tokens.subject(credentials.credentials)
        if subject:
            identity = db.execute("SELECT principal_id FROM hosted_identities WHERE (issuer,subject)=(%s,%s)",
                                  (settings.issuer, subject)).fetchone()
            if identity:
                return identity["principal_id"]
    elif credentials and len(credentials.credentials) == 43:
        token_hash = hashlib.sha256(credentials.credentials.encode()).digest()
        session = db.execute(
            "SELECT principal_id FROM demo_sessions WHERE token_hash = %s AND expires_at > now()",
            (token_hash,),
        ).fetchone()
        if session:
            return session["principal_id"]
    raise HTTPException(401, "Sign in to continue.", headers={"WWW-Authenticate": "Bearer"})


Caller = Annotated[str, Depends(principal_id)]


def require_tenant(db: psycopg.Connection, caller: str, tenant: str) -> None:
    grant = db.execute(
        "SELECT 1 FROM grants WHERE principal_id = %s AND tenant_id = %s LIMIT 1",
        (caller, tenant),
    ).fetchone()
    if grant is None:
        raise HTTPException(403, "This identity does not have access to that customer.")


def require_site(db: psycopg.Connection, caller: str, tenant: str, site: str, allowed: tuple[SiteRole, ...], lock=False):
    require_tenant(db, caller, tenant)
    grant = db.execute(
        "SELECT role FROM grants WHERE principal_id = %s AND tenant_id = %s AND site_id = %s"
        + (" FOR SHARE" if lock else ""),
        (caller, tenant, site),
    ).fetchone()
    if grant is None:
        raise HTTPException(404, "Site not found in granted scope.")
    if grant["role"] not in allowed:
        raise HTTPException(403, "This identity cannot perform that action.")
    return grant["role"]


def require_site_role(db: psycopg.Connection, caller: str, tenant: str, site: str, role: SiteRole) -> None:
    require_site(db, caller, tenant, site, (role,))


@app.post(
    "/v1/tenants/{tenant_id}/sites/{site_id}/inventory/import",
    operation_id="importInventory",
    openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": IMPORT_SCHEMA}}}},
    responses={code: {"description": detail} for code, detail in {
        401: "Invalid session", 403: "Operator role required", 404: "Site not found in granted scope",
        409: "Batch rejected without changes; row conflicts", 413: "Batch too large", 415: "JSON required",
    }.items()},
)
def import_inventory(
    tenant_id: AssetId,
    site_id: AssetId,
    db: Database,
    caller: Caller,
    batch: Annotated[InventoryBatch, Depends(read_inventory_import)],
) -> dict[str, int]:
    with db.transaction():
        lock_site_operator(db, caller, (tenant_id, site_id))
        return {"accepted": store_inventory_import(db, tenant_id, site_id, batch)}


SCOPE_PATH = "/v1/tenants/{tenant_id}/sites/{site_id}"
PLAN_PATH = SCOPE_PATH + "/plans/{plan_id}/revisions"
REVISION_PATH = PLAN_PATH + "/{revision}"
READ_ROLES = ("partner_proposer", "customer_approver", "operator")
Revision = Annotated[int, PathParam(ge=1, le=9007199254740991)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", pattern=r"^[A-Za-z0-9_-]{16,128}$")]


class PreviewRequest(BaseModel):
    model_config = {"extra": "forbid"}
    base_revision: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]


class FingerprintRequest(BaseModel):
    model_config = {"extra": "forbid"}
    expected_fingerprint: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class RehearsalContext(BaseModel):
    role: SiteRole
    clock_at: str
    last_observed_at: str | None
    stale: bool
    pending: bool


@app.get(SCOPE_PATH + "/rehearsal/context", operation_id="rehearsalContext")
def rehearsal_context(tenant_id: AssetId, site_id: AssetId, db: Database, caller: Caller) -> RehearsalContext:
    role = require_site(db, caller, tenant_id, site_id, READ_ROLES)
    state = rehearsal.site_state(db, (tenant_id, site_id))
    clock = state["clock_at"]
    observed = [datetime.fromisoformat(device["observed_at"]) for device in state["graph"]["devices"].values()
                if device.get("observed_at")]
    oldest = clock - timedelta(seconds=state["graph"]["max_age_seconds"])
    return RehearsalContext(role=role, clock_at=rehearsal.stamp(clock),
                            last_observed_at=rehearsal.stamp(max(observed)) if observed else None,
                            stale=any(value < oldest or value > clock for value in observed),
                            pending=rehearsal.pending_evidence(db, (tenant_id, site_id)))


def lock_site_operator(db, caller, scope):
    require_site_role(db, caller, *scope, "operator")
    db.execute("SELECT 1 FROM sites WHERE (tenant_id,id)=(%s,%s) FOR UPDATE", scope)
    require_site(db, caller, *scope, ("operator",), lock=True)


@app.post(SCOPE_PATH + "/observations", status_code=202, operation_id="acceptObservation")
def accept_observation(tenant_id: AssetId, site_id: AssetId, body: observations.ObservationRequest,
                       db: Database, caller: Caller):
    scope = (tenant_id, site_id)
    with db.transaction():
        lock_site_operator(db, caller, scope)
        return observations.accept(db, scope, body)


@app.get(SCOPE_PATH + "/observations/quarantine", operation_id="observationQuarantine")
def observation_quarantine(tenant_id: AssetId, site_id: AssetId, db: Database, caller: Caller):
    require_site_role(db, caller, tenant_id, site_id, "operator")
    return observations.quarantine_page(db, (tenant_id, site_id))


@app.post(SCOPE_PATH + "/observations/{event_id}/replay", status_code=202, operation_id="replayObservation")
def replay_observation(tenant_id: AssetId, site_id: AssetId, event_id: UUID, db: Database, caller: Caller):
    scope = (tenant_id, site_id)
    with db.transaction():
        lock_site_operator(db, caller, scope)
        observations.replay(db, scope, event_id)
    return {"queued": True}


def rehearsal_write(db, scope, caller, role, operation, key, payload, action):
    with db.transaction():
        require_site_role(db, caller, *scope, role)
        db.execute("SELECT 1 FROM sites WHERE (tenant_id,id)=(%s,%s) FOR UPDATE", scope)
        require_site(db, caller, *scope, (role,), lock=True)
        status, body, location = rehearsal.write_once(db, scope, caller, operation, key, payload, action)
    headers = {"Location": location} if location else {}
    return JSONResponse(body, status, headers=headers)


def scoped_errors(request):
    return request.url.path.startswith("/v1/tenants/") and not request.url.path.endswith("/inventory/import")


@app.exception_handler(HTTPException)
async def scoped_http_error(request: Request, error: HTTPException):
    if not scoped_errors(request):
        return await http_exception_handler(request, error)
    if isinstance(error.detail, dict):
        body = error.detail
    else:
        code = {401: "unauthenticated", 403: "forbidden", 404: "not_found"}.get(error.status_code, "invalid_request")
        body = {"code": code, "detail": error.detail}
    return JSONResponse(body, error.status_code, headers=error.headers)


@app.exception_handler(RequestValidationError)
async def scoped_validation_error(request: Request, error: RequestValidationError):
    if scoped_errors(request):
        return JSONResponse({"code": "invalid_request", "detail": "Check the submitted fields and headers."}, 422)
    return await request_validation_exception_handler(request, error)


@app.post(PLAN_PATH, status_code=201, operation_id="previewPlan")
def preview_plan(tenant_id: AssetId, site_id: AssetId, plan_id: AssetId,
                 body: PreviewRequest, key: IdempotencyKey, db: Database, caller: Caller):
    scope = (tenant_id, site_id)
    return rehearsal_write(db, scope, caller, "partner_proposer", "preview", key, [plan_id, body.base_revision],
                           lambda correlation: rehearsal.preview_plan(db, scope, caller, plan_id, body.base_revision, correlation))


@app.get(REVISION_PATH, operation_id="readPlanRevision")
def read_plan(tenant_id: AssetId, site_id: AssetId, plan_id: AssetId, revision: Revision, db: Database, caller: Caller):
    require_site(db, caller, tenant_id, site_id, READ_ROLES)
    return rehearsal.read_plan(db, (tenant_id, site_id), plan_id, revision)[0]


@app.post(REVISION_PATH + "/approval", status_code=201, operation_id="approvePlanRevision")
def approve_plan(tenant_id: AssetId, site_id: AssetId, plan_id: AssetId, revision: Revision,
                 body: FingerprintRequest, key: IdempotencyKey, db: Database, caller: Caller):
    scope, expected = (tenant_id, site_id), body.expected_fingerprint
    return rehearsal_write(db, scope, caller, "customer_approver", "approve", key, [plan_id, revision, expected],
                           lambda correlation: rehearsal.approve_plan(db, scope, caller, plan_id, revision, expected, correlation))


@app.post(REVISION_PATH + "/waves/{wave_number}/runs", status_code=202, operation_id="startRehearsal")
def start_rehearsal(tenant_id: AssetId, site_id: AssetId, plan_id: AssetId, revision: Revision, wave_number: Revision,
                    body: FingerprintRequest, key: IdempotencyKey, db: Database, caller: Caller):
    scope, expected = (tenant_id, site_id), body.expected_fingerprint
    return rehearsal_write(db, scope, caller, "operator", "start", key, [plan_id, revision, wave_number, expected],
                           lambda correlation: rehearsal.reserve_wave(db, scope, caller, plan_id, revision,
                                                                      wave_number, expected, correlation))


@app.get(SCOPE_PATH + "/runs/{run_id}", operation_id="readRehearsal")
def read_run(tenant_id: AssetId, site_id: AssetId, run_id: AssetId, db: Database, caller: Caller):
    require_site(db, caller, tenant_id, site_id, READ_ROLES)
    return rehearsal.read_run(db, (tenant_id, site_id), run_id)


@app.get(SCOPE_PATH + "/audit", operation_id="listSiteAudit")
def audit_page(tenant_id: AssetId, site_id: AssetId, db: Database, caller: Caller,
               after: Annotated[str | None, Query(max_length=512)] = None,
               limit: Annotated[int, Query(ge=1, le=100)] = 25):
    require_site(db, caller, tenant_id, site_id, READ_ROLES)
    return rehearsal.audit_page(db, (tenant_id, site_id), after, limit)


@app.get("/healthz")
def health(db: Database) -> dict[str, str]:
    db.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def ready(db: Database) -> dict[str, str]:
    if not schema_ready(db):
        raise HTTPException(503, "Database migrations are incomplete.")
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
def metrics(db: Database):
    return PlainTextResponse(render_metrics(db), media_type="text/plain; version=0.0.4")


def require_local(request: Request):
    if not isinstance(request.app.state.settings, LocalSettings):
        raise HTTPException(404, "Not found.")


class AuthConfig(BaseModel):
    mode: Literal["local", "hosted"]
    issuer: str | None = None
    audience: str | None = None
    client_id: str | None = None


@app.get("/v1/auth/config", operation_id="authConfig")
def auth_config(request: Request) -> AuthConfig:
    settings = request.app.state.settings
    if isinstance(settings, HostedSettings):
        return AuthConfig(mode="hosted", issuer=settings.issuer, audience=settings.audience, client_id=settings.client_id)
    return AuthConfig(mode="local")


@app.get("/v1/auth/profile", operation_id="authProfile")
def auth_profile(db: Database, caller: Caller) -> Principal:
    row = db.execute(
        """SELECT p.id,p.label,COALESCE(
               (SELECT json_agg(json_build_object('id',t.id,'label',t.label) ORDER BY t.id)
                FROM tenants t WHERE EXISTS (SELECT 1 FROM grants g WHERE g.principal_id=p.id AND g.tenant_id=t.id)),
               '[]'::json) AS tenants
           FROM principals p WHERE p.id=%s""", (caller,),
    ).fetchone()
    if not row:
        raise HTTPException(401, "Sign in to continue.")
    return Principal(**row)


@app.get("/v1/demo/principals", dependencies=[Depends(require_local)])
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


@app.post("/v1/demo/session", dependencies=[Depends(require_local)])
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
