import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, field_validator
from psycopg.types.json import Jsonb

from fleet.rehearsal import fail, site_state, stamp

TOPIC = "fleet-observations-v1"
MAX_MESSAGE = 16 * 1024


class HealthEvidence(BaseModel):
    model_config = {"extra": "forbid"}
    healthy: StrictBool


class ObservationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: Literal[1]
    event_id: UUID
    asset_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
    source_id: Literal["synthetic-health"]
    source_revision: Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]
    observed_at: datetime
    evidence: HealthEvidence

    @field_validator("observed_at", mode="before")
    @classmethod
    def utc_timestamp(cls, value):
        if not isinstance(value, str):
            raise ValueError("Use a UTC timestamp.")
        moment = datetime.fromisoformat(value)
        if moment.utcoffset() != timedelta(0):
            raise ValueError("Use a UTC timestamp.")
        return moment


def accept(db, scope, event):
    tenant, site = scope
    now = datetime.now(timezone.utc)
    if event.observed_at > now:
        fail(422, "invalid_request", "Observation time cannot be in the future.")
    state = site_state(db, scope)
    if event.asset_id not in state["graph"]["devices"] or not db.execute(
        "SELECT 1 FROM assets WHERE (tenant_id,site_id,id)=(%s,%s,%s)", (*scope, event.asset_id),
    ).fetchone():
        fail(404, "not_found", "Asset not found in granted scope.")
    payload = event.model_dump(mode="json")
    payload.update(tenant_id=tenant, site_id=site)
    receipt = db.execute(
        "SELECT body FROM observation_events WHERE (tenant_id,event_id)=(%s,%s)", (tenant, event.event_id),
    ).fetchone()
    if receipt:
        previous = dict(receipt["body"])
        previous.pop("received_at")
        if previous != payload:
            fail(409, "observation_conflict", "The observation ID or source revision is already in use.")
        return receipt["body"]
    payload["received_at"] = stamp(now)
    inserted = db.execute(
        """INSERT INTO observation_events (tenant_id,event_id,site_id,asset_id,source_id,source_revision,body)
           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING event_id""",
        (tenant, event.event_id, site, event.asset_id, event.source_id, event.source_revision, Jsonb(payload)),
    ).fetchone()
    if inserted is None:
        fail(409, "observation_conflict", "The observation ID or source revision is already in use.")
    db.execute(
        """INSERT INTO observation_watermarks (tenant_id,site_id,asset_id,source_id,accepted)
           VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (tenant_id,site_id,asset_id,source_id) DO UPDATE
           SET accepted=GREATEST(observation_watermarks.accepted, EXCLUDED.accepted)""",
        (*scope, event.asset_id, event.source_id, event.source_revision),
    )
    return payload


def partition_key(event):
    return f"{event['tenant_id']}/{event['asset_id']}".encode()


def relay_one(db, publish):
    with db.transaction():
        event = db.execute("""SELECT tenant_id,event_id,body FROM observation_events WHERE published_at IS NULL
                              ORDER BY ordinal LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
        if not event:
            return False
        payload = event["body"]
        publish(partition_key(payload), json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        db.execute("UPDATE observation_events SET published_at=now() WHERE (tenant_id,event_id)=(%s,%s)",
                   (event["tenant_id"], event["event_id"]))
        return True


def _quarantine(db, broker, raw, row=None):
    scope = (row["tenant_id"], row["site_id"], row["event_id"]) if row else (None, None, None)
    db.execute(
        """INSERT INTO observation_quarantine
           (topic,partition,message_offset,tenant_id,site_id,event_id,payload_hash,reason)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'invalid_envelope') ON CONFLICT DO NOTHING""",
        (*broker, *scope, hashlib.sha256(raw).hexdigest()),
    )


def project_message(db, key, raw, broker):
    """Commit projection or sanitized quarantine before the broker offset can advance."""
    raw = raw or b""
    with db.transaction():
        try:
            body = json.loads(raw) if len(raw) <= MAX_MESSAGE else None
            if not isinstance(body, dict) or not isinstance(body.get("tenant_id"), str):
                raise ValueError
            event_id = UUID(body["event_id"])
        except (ValueError, KeyError, TypeError, AttributeError):
            _quarantine(db, broker, raw)
            return False
        row = db.execute(
            "SELECT * FROM observation_events WHERE (tenant_id,event_id)=(%s,%s)", (body["tenant_id"], event_id),
        ).fetchone()
        if not row:
            _quarantine(db, broker, raw)
            return False
        scope = (row["tenant_id"], row["site_id"])
        db.execute("SELECT 1 FROM sites WHERE (tenant_id,id)=(%s,%s) FOR UPDATE", scope)
        if key != partition_key(row["body"]) or json.dumps(body, sort_keys=True) != json.dumps(row["body"], sort_keys=True):
            _quarantine(db, broker, raw, row if key == partition_key(row["body"]) else None)
            return False
        watermark = (*scope, row["asset_id"], row["source_id"])
        applied = db.execute(
            """SELECT applied FROM observation_watermarks
               WHERE (tenant_id,site_id,asset_id,source_id)=(%s,%s,%s,%s)""", watermark,
        ).fetchone()["applied"]
        if row["source_revision"] > applied:
            state = site_state(db, scope)
            graph = deepcopy(state["graph"])
            revision = state["snapshot_revision"] + 1
            graph["revision"] = revision
            graph["devices"][row["asset_id"]].update(**row["body"]["evidence"], observed_at=row["body"]["observed_at"])
            db.execute("INSERT INTO rehearsal_snapshots VALUES (%s,%s,%s,%s)", (*scope, revision, Jsonb(graph)))
            db.execute("UPDATE rehearsal_sites SET snapshot_revision=%s WHERE (tenant_id,site_id)=(%s,%s)",
                       (revision, *scope))
            db.execute("""UPDATE observation_watermarks SET applied=%s
                          WHERE (tenant_id,site_id,asset_id,source_id)=(%s,%s,%s,%s)""",
                       (row["source_revision"], *watermark))
        db.execute("""UPDATE observation_events SET processed_at=COALESCE(processed_at,now())
                      WHERE (tenant_id,event_id)=(%s,%s)""", (row["tenant_id"], row["event_id"]))
        db.execute("""UPDATE observation_quarantine SET repaired_at=now()
                      WHERE (tenant_id,site_id,event_id)=(%s,%s,%s) AND repaired_at IS NULL""",
                   (*scope, row["event_id"]))
        return True


def replay(db, scope, event_id):
    if not db.execute(
        """SELECT 1 FROM observation_quarantine WHERE (tenant_id,site_id,event_id)=(%s,%s,%s)
           AND repaired_at IS NULL LIMIT 1""", (*scope, event_id),
    ).fetchone():
        fail(404, "not_found", "Repairable observation not found in granted scope.")
    db.execute("""UPDATE observation_events SET published_at=NULL
                  WHERE (tenant_id,site_id,event_id)=(%s,%s,%s)""", (*scope, event_id))


def quarantine_page(db, scope):
    rows = db.execute(
        """SELECT event_id,reason,recorded_at,repaired_at FROM observation_quarantine
           WHERE (tenant_id,site_id)=(%s,%s) ORDER BY recorded_at DESC LIMIT 50""", scope,
    ).fetchall()
    return {"items": [{"event_id": str(row["event_id"]), "reason": row["reason"],
                       "recorded_at": stamp(row["recorded_at"]),
                       "repaired_at": stamp(row["repaired_at"]) if row["repaired_at"] else None} for row in rows]}
