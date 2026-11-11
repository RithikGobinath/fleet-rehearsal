import hashlib
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as DecodeError
from datetime import datetime
from datetime import timezone
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from fleet.planner import normalize_snapshot, plan_waves, validate_wave, validate_wave_policy


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fail(status, code, detail, **fields):
    raise HTTPException(status, {"code": code, "detail": detail, **fields})


def seed_rehearsal(db):
    scope = ("northstar", "harbor")
    if not db.execute("SELECT 1 FROM sites WHERE tenant_id=%s AND id=%s FOR UPDATE", scope).fetchone():
        return
    fixture = json.loads(Path("fixtures/peer_down_after_approval.json").read_text(encoding="utf-8"))
    db.execute("INSERT INTO rehearsal_snapshots VALUES (%s,%s,1,%s) ON CONFLICT DO NOTHING",
               (*scope, Jsonb(normalize_snapshot(fixture["snapshot"]))))
    db.execute("INSERT INTO rehearsal_policies VALUES (%s,%s,1,%s) ON CONFLICT DO NOTHING",
               (*scope, Jsonb(fixture["policy"])))
    db.execute("""INSERT INTO rehearsal_sites (tenant_id,site_id,snapshot_revision,policy_revision,clock_at)
                  VALUES (%s,%s,1,1,%s) ON CONFLICT DO NOTHING""", (*scope, fixture["at"]))


def site_state(db, scope):
    state = db.execute(
        """SELECT r.snapshot_revision,r.policy_revision,r.clock_at,r.fail_canary,s.graph,p.policy
           FROM rehearsal_sites r
           JOIN rehearsal_snapshots s ON (s.tenant_id,s.site_id,s.revision) =
                                         (r.tenant_id,r.site_id,r.snapshot_revision)
           JOIN rehearsal_policies p ON (p.tenant_id,p.site_id,p.revision) =
                                        (r.tenant_id,r.site_id,r.policy_revision)
           WHERE (r.tenant_id,r.site_id)=(%s,%s)""", scope,
    ).fetchone()
    if state is None:
        fail(409, "missing_evidence", "This site has no rehearsal snapshot.")
    return state


def write_audit(db, scope, actor, correlation, kind, **context):
    return db.execute(
        """INSERT INTO rehearsal_audit (tenant_id,site_id,actor_id,correlation_id,kind,context,id)
           SELECT %s,%s,%s,%s,%s,%s,COALESCE(max(id),0)+1 FROM rehearsal_audit
           WHERE (tenant_id,site_id)=(%s,%s) RETURNING id""",
        (*scope, actor, correlation, kind, Jsonb(context), *scope),
    ).fetchone()["id"]


def write_once(db, scope, actor, operation, key, request, apply):
    digest = fingerprint([*scope, request])
    identity = (scope[0], actor, operation, key)
    created = db.execute(
        """INSERT INTO rehearsal_receipts (tenant_id,actor_id,operation,request_key,digest)
           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING digest""", (*identity, digest),
    ).fetchone()
    if created is None:
        saved = db.execute(
            """SELECT digest,status,body,location FROM rehearsal_receipts
               WHERE (tenant_id,actor_id,operation,request_key)=(%s,%s,%s,%s)""", identity,
        ).fetchone()
        if saved["digest"] != digest:
            fail(409, "idempotency_conflict", "This key was already used for a different request.")
        return saved["status"], saved["body"], saved["location"]
    status, body, location = apply(uuid4())
    db.execute("""UPDATE rehearsal_receipts SET status=%s,body=%s,location=%s
                  WHERE (tenant_id,actor_id,operation,request_key)=(%s,%s,%s,%s)""",
               (status, Jsonb(body), location, *identity))
    return status, body, location


def scope_url(scope):
    return f"/api/v1/tenants/{scope[0]}/sites/{scope[1]}"


def latest_revision(db, scope, plan):
    return db.execute(
        "SELECT COALESCE(max(revision),0) AS revision FROM plan_revisions WHERE (tenant_id,site_id,plan_id)=(%s,%s,%s)",
        (*scope, plan),
    ).fetchone()["revision"]


def stale(current):
    fail(409, "stale_revision", "Reload the current plan revision before retrying.",
         **({"current_revision": current} if current else {}))


def approval_body(row):
    return {"fingerprint": row["fingerprint"], "approved_by": row["approved_by"],
            "approved_at": stamp(row["approved_at"]), "audit_id": f"audit-{row['audit_id']}"}


def read_plan(db, scope, plan, revision):
    key = (*scope, plan, revision)
    row = db.execute(
        "SELECT document,proposer FROM plan_revisions WHERE (tenant_id,site_id,plan_id,revision)=(%s,%s,%s,%s)", key,
    ).fetchone()
    if row is None:
        fail(404, "not_found", "Plan revision not found in granted scope.")
    approval = db.execute(
        "SELECT * FROM plan_approvals WHERE (tenant_id,site_id,plan_id,revision)=(%s,%s,%s,%s)", key,
    ).fetchone()
    return {**row["document"], "approval": approval_body(approval) if approval else None}, row["proposer"]


def preview_plan(db, scope, actor, plan, base, correlation):
    current = latest_revision(db, scope, plan)
    if base != current:
        stale(current)
    state = site_state(db, scope)
    result = plan_waves(state["graph"], state["policy"], stamp(state["clock_at"]))
    revision = current + 1
    document = {
        "plan_id": plan, "revision": revision, "snapshot_revision": state["snapshot_revision"],
        "policy_revision": state["policy_revision"],
        "protected_services": sorted(state["graph"]["services"]),
        "waves": [dict(wave, wave_number=index) for index, wave in enumerate(result["waves"], 1)],
        "deferred": [{"asset_id": item["id"], "reason": item["reason"]} for item in result["deferred"]],
    }
    document["fingerprint"] = fingerprint([*scope, document])
    document["approval"] = None
    db.execute("""INSERT INTO plan_revisions (tenant_id,site_id,plan_id,revision,snapshot_revision,
                      policy_revision,proposer,fingerprint,document)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
               (*scope, plan, revision, state["snapshot_revision"], state["policy_revision"],
                actor, document["fingerprint"], Jsonb(document)))
    write_audit(db, scope, actor, correlation, "plan_previewed", plan_id=plan, revision=revision,
                snapshot_revision=state["snapshot_revision"])
    return 201, document, f"{scope_url(scope)}/plans/{plan}/revisions/{revision}"


def approve_plan(db, scope, actor, plan, revision, expected, correlation):
    document, proposer = read_plan(db, scope, plan, revision)
    current = latest_revision(db, scope, plan)
    if current != revision or document["fingerprint"] != expected:
        stale(current)
    if proposer == actor:
        fail(403, "forbidden", "A proposer cannot approve their own revision.")
    if document["approval"] or not document["waves"]:
        fail(409, "invalid_plan_state", "This revision cannot be approved.")
    audit = write_audit(db, scope, actor, correlation, "plan_approved", plan_id=plan, revision=revision,
                        snapshot_revision=document["snapshot_revision"])
    row = db.execute(
        """INSERT INTO plan_approvals (tenant_id,site_id,plan_id,revision,fingerprint,approved_by,audit_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""", (*scope, plan, revision, expected, actor, audit),
    ).fetchone()
    return 201, approval_body(row), None


def wave_at(document, number):
    if not 1 <= number <= len(document["waves"]):
        fail(404, "not_found", "Wave not found in granted scope.")
    return document["waves"][number - 1]


def pending_evidence(db, scope, graph=None, wave=None):
    rows = db.execute(
        """SELECT asset_id FROM observation_watermarks
           WHERE (tenant_id,site_id)=(%s,%s) AND source_id='synthetic-health' AND accepted>applied""", scope,
    ).fetchall()
    if not rows or graph is None:
        return bool(rows)
    relevant = set(graph["services"].values())
    while True:
        expanded = relevant | {node for link in graph["links"] if relevant.intersection(link) for node in link}
        if expanded == relevant:
            break
        relevant = expanded
    return bool((relevant | set(wave["device_ids"])).intersection(row["asset_id"] for row in rows))


def guard_reason(db, scope, state, document, number):
    plan, revision = document["plan_id"], document["revision"]
    wave = wave_at(document, number)
    now = state["clock_at"]
    if latest_revision(db, scope, plan) != revision:
        return "stale_revision"
    if state["policy_revision"] != document["policy_revision"]:
        return "policy_changed"
    if not datetime.fromisoformat(wave["starts_at"]) <= now < datetime.fromisoformat(wave["ends_at"]):
        return "outside_window"
    if pending_evidence(db, scope, state["graph"], wave):
        return "pending_evidence"
    previous = db.execute(
        """SELECT wave_number,devices FROM rehearsal_runs
           WHERE (tenant_id,site_id,plan_id,revision)=(%s,%s,%s,%s) AND state='succeeded'""",
        (*scope, plan, revision),
    ).fetchall()
    if not set(range(1, number)).issubset(row["wave_number"] for row in previous):
        return "prerequisite_incomplete"
    succeeded = {item["asset_id"] for row in previous for item in row["devices"] if item["outcome"] == "succeeded"}
    return validate_wave_policy(state["policy"], wave, succeeded) or validate_wave(state["graph"], wave, stamp(now))["reason"]


def run_body(row):
    body = {name: row[name] for name in ("run_id", "plan_id", "revision", "wave_number",
            "snapshot_revision", "state", "devices")}
    if row["reason"]:
        body["reason"] = row["reason"]
    if row["audit_id"]:
        body["audit_id"] = f"audit-{row['audit_id']}"
    return body


def read_run(db, scope, run):
    row = db.execute("SELECT * FROM rehearsal_runs WHERE (tenant_id,site_id,run_id)=(%s,%s,%s)",
                     (*scope, run)).fetchone()
    if row is None:
        fail(404, "not_found", "Run not found in granted scope.")
    return run_body(row)


def audit_wave(db, scope, actor, correlation, kind, plan, revision, number, snapshot, **context):
    return write_audit(db, scope, actor, correlation, kind, plan_id=plan, revision=revision,
                       wave_number=number, snapshot_revision=snapshot, **context)


def block_reserved(db, scope, row, snapshot, reason):
    audit = audit_wave(db, scope, row["actor_id"], row["correlation_id"], "wave_blocked", row["plan_id"],
                       row["revision"], row["wave_number"], snapshot, run_id=row["run_id"], reason=reason)
    db.execute("""UPDATE rehearsal_runs SET state='blocked',snapshot_revision=%s,reason=%s,audit_id=%s
                  WHERE (tenant_id,site_id,run_id)=(%s,%s,%s) AND state='queued'""",
               (snapshot, reason, audit, *scope, row["run_id"]))


def reserve_wave(db, scope, actor, plan, revision, number, expected, correlation):
    document, _ = read_plan(db, scope, plan, revision)
    current = latest_revision(db, scope, plan)
    if current != revision or document["fingerprint"] != expected:
        stale(current)
    if not document["approval"]:
        fail(409, "invalid_plan_state", "Approve this revision before rehearsing it.")
    wave_at(document, number)
    state = site_state(db, scope)
    active = db.execute(
        """SELECT * FROM rehearsal_runs WHERE (tenant_id,site_id)=(%s,%s)
           AND state IN ('queued','running','paused')""", scope,
    ).fetchone()
    if active and active["state"] == "queued":
        old, _ = read_plan(db, scope, active["plan_id"], active["revision"])
        if state["clock_at"] >= datetime.fromisoformat(wave_at(old, active["wave_number"])["ends_at"]):
            block_reserved(db, scope, active, state["snapshot_revision"], "outside_window")
            active = None
    started = db.execute(
        """SELECT 1 FROM rehearsal_runs WHERE (tenant_id,site_id,plan_id,revision,wave_number)=(%s,%s,%s,%s,%s)
           AND started_at IS NOT NULL""", (*scope, plan, revision, number),
    ).fetchone()
    reason = "already_rehearsed" if started else "site_busy" if active else guard_reason(db, scope, state, document, number)
    snapshot = state["snapshot_revision"]
    if reason:
        audit = audit_wave(db, scope, actor, correlation, "wave_blocked", plan, revision, number, snapshot, reason=reason)
        return 409, {"code": reason, "detail": "The simulator did not start this wave.",
                     "snapshot_revision": snapshot, "audit_id": f"audit-{audit}"}, None
    run = "run-" + uuid4().hex
    audit_wave(db, scope, actor, correlation, "wave_reserved", plan, revision, number, snapshot, run_id=run)
    row = db.execute(
        """INSERT INTO rehearsal_runs (tenant_id,site_id,run_id,plan_id,revision,wave_number,
                    snapshot_revision,actor_id,correlation_id,state)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'queued') RETURNING *""",
        (*scope, run, plan, revision, number, snapshot, actor, correlation),
    ).fetchone()
    return 202, run_body(row), f"{scope_url(scope)}/runs/{run}"


def audit_page(db, scope, after, limit):
    previous = 0
    if after:
        try:
            tenant, site, value = urlsafe_b64decode(after + "=" * (-len(after) % 4)).decode().split("/")
            previous = int(value)
            if (tenant, site) != scope or not 0 < previous <= 2**63 - 1:
                raise ValueError
            if not db.execute("SELECT 1 FROM rehearsal_audit WHERE (tenant_id,site_id,id)=(%s,%s,%s)",
                              (*scope, previous)).fetchone():
                raise ValueError
        except (ValueError, DecodeError):
            fail(422, "invalid_request", "Invalid audit cursor.")
    rows = db.execute(
        """SELECT id,actor_id,occurred_at,correlation_id,kind,context FROM rehearsal_audit
           WHERE (tenant_id,site_id)=(%s,%s) AND id>%s ORDER BY id LIMIT %s""", (*scope, previous, limit + 1),
    ).fetchall()
    cursor = urlsafe_b64encode(f"{scope[0]}/{scope[1]}/{rows[limit - 1]['id']}".encode()).decode().rstrip("=") if len(rows) > limit else None
    return {"items": [{**row["context"], "audit_id": f"audit-{row['id']}", "actor_id": row["actor_id"],
                      "occurred_at": stamp(row["occurred_at"]), "correlation_id": str(row["correlation_id"]),
                      "kind": row["kind"]} for row in rows[:limit]], "next_cursor": cursor}


def process_one(db):
    """Commit at most one synthetic step while holding the site's reservation lock."""
    with db.transaction():
        site = db.execute(
            """SELECT s.tenant_id,s.id AS site_id FROM sites s
               JOIN rehearsal_runs r ON (r.tenant_id,r.site_id)=(s.tenant_id,s.id)
               WHERE r.state IN ('queued','running') ORDER BY r.tenant_id,r.site_id
               LIMIT 1 FOR UPDATE OF s SKIP LOCKED""",
        ).fetchone()
        if site is None:
            return False
        scope = (site["tenant_id"], site["site_id"])
        row = db.execute(
            """SELECT * FROM rehearsal_runs WHERE (tenant_id,site_id)=(%s,%s)
               AND state IN ('queued','running')""", scope,
        ).fetchone()
        if row is None:
            return False
        state = site_state(db, scope)
        document, _ = read_plan(db, scope, row["plan_id"], row["revision"])
        wave = wave_at(document, row["wave_number"])
        audit_key = (db, scope, row["actor_id"], row["correlation_id"])
        coordinates = (row["plan_id"], row["revision"], row["wave_number"])
        run = row["run_id"]
        if row["state"] == "queued":
            grant = db.execute(
                """SELECT 1 FROM grants WHERE (tenant_id,site_id,principal_id)=(%s,%s,%s)
                   AND role='operator' FOR SHARE""", (*scope, row["actor_id"]),
            ).fetchone()
            reason = guard_reason(db, scope, state, document, row["wave_number"]) if grant else "authorization_revoked"
            if reason:
                block_reserved(db, scope, row, state["snapshot_revision"], reason)
            else:
                db.execute("""UPDATE rehearsal_runs SET state='running',started_at=now(),snapshot_revision=%s
                              WHERE (tenant_id,site_id,run_id)=(%s,%s,%s) AND state='queued'""",
                           (state["snapshot_revision"], *scope, run))
                audit_wave(*audit_key, "simulator_started", *coordinates, state["snapshot_revision"], run_id=run)
        else:
            failed = wave["canary"] and state["fail_canary"]
            outcome = "rolled_back" if failed else "succeeded"
            reason = "verification_failed" if failed else None
            audit = audit_wave(*audit_key, "run_updated", *coordinates, row["snapshot_revision"],
                               run_id=run, **({"reason": reason} if reason else {}))
            devices = [{"asset_id": asset, "outcome": outcome} for asset in wave["device_ids"]]
            db.execute("""UPDATE rehearsal_runs SET state=%s,reason=%s,devices=%s,audit_id=%s
                          WHERE (tenant_id,site_id,run_id)=(%s,%s,%s) AND state='running'""",
                       (outcome, reason, Jsonb(devices), audit, *scope, run))
        return True
