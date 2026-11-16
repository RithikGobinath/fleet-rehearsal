import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StrictBool, ValidationError, field_validator, model_validator

from fleet.rehearsal import fail, site_state, stamp

TOPIC = "fleet-observations-v1"
MAX_MESSAGE = 16 * 1024
IDENTIFIER_PATTERN = r"[a-z0-9][a-z0-9-]{0,63}"
Identifier = Annotated[str, Field(pattern=rf"^{IDENTIFIER_PATTERN}$")]


class HealthEvidence(BaseModel):
    model_config = {"extra": "forbid"}
    healthy: StrictBool


class ObservationRequest(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: Literal[1]
    event_id: UUID
    asset_id: Identifier
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


class LinkEvidence(BaseModel):
    model_config = {"extra": "forbid"}
    carrier_up: StrictBool


class ProbeEvidence(BaseModel):
    model_config = {"extra": "forbid"}
    sent: Annotated[int, Field(strict=True, ge=1, le=100)]
    received: Annotated[int, Field(strict=True, ge=0, le=100)]

    @model_validator(mode="after")
    def check_counts(self):
        if self.received > self.sent:
            raise ValueError("Received probes cannot exceed sent probes.")
        return self


class SourceObservation(ObservationRequest):
    tenant_id: Identifier
    site_id: Identifier


class LinkObservation(SourceObservation):
    source_id: Literal["synthetic-link"]
    evidence: LinkEvidence


class ProbeObservation(SourceObservation):
    source_id: Literal["synthetic-probe"]
    evidence: ProbeEvidence


LINK_TOPIC = "fleet-link-observations-v1"
PROBE_TOPIC = "fleet-probe-observations-v1"
SOURCE_TOPICS = {LINK_TOPIC: LinkObservation, PROBE_TOPIC: ProbeObservation}


def synthetic_stream_scope(env):
    value = env.get("FLEET_SYNTHETIC_STREAM_SCOPE")
    if value is None:
        return None
    if env.get("FLEET_DEMO_MODE") != "local" or not re.fullmatch(rf"{IDENTIFIER_PATTERN}/{IDENTIFIER_PATTERN}", value):
        raise RuntimeError("Set FLEET_SYNTHETIC_STREAM_SCOPE=tenant/site only in the local synthetic demo.")
    tenant, site = value.split("/")
    return tenant, site


def accept(db, scope, event, *, published=False):
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
        """INSERT INTO observation_events (tenant_id,event_id,site_id,asset_id,source_id,source_revision,body,published_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN now() END)
           ON CONFLICT DO NOTHING RETURNING event_id""",
        (tenant, event.event_id, site, event.asset_id, event.source_id, event.source_revision, Jsonb(payload), published),
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
    return relay_batch(db, lambda messages: publish(*messages[0]), limit=1)


def relay_batch(db, publish, limit=32):
    with db.transaction():
        events = db.execute("""SELECT tenant_id,event_id,body FROM observation_events WHERE published_at IS NULL
                               ORDER BY ordinal LIMIT %s FOR UPDATE SKIP LOCKED""", (limit,)).fetchall()
        if not events:
            return False
        publish([(partition_key(row["body"]),
                  json.dumps(row["body"], sort_keys=True, separators=(",", ":")).encode()) for row in events])
        with db.cursor() as cur:
            cur.executemany("UPDATE observation_events SET published_at=now() WHERE (tenant_id,event_id)=(%s,%s)",
                            [(row["tenant_id"], row["event_id"]) for row in events])
        return True


def _quarantine(db, broker, raw, row=None):
    scope = (row["tenant_id"], row["site_id"], row["event_id"]) if row else (None, None, None)
    db.execute(
        """INSERT INTO observation_quarantine
           (topic,partition,message_offset,tenant_id,site_id,event_id,payload_hash,reason)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'invalid_envelope') ON CONFLICT DO NOTHING""",
        (*broker, *scope, hashlib.sha256(raw).hexdigest()),
    )


def _record_source(db, key, body, topic, scope):
    if scope is None:
        return None
    try:
        event = SOURCE_TOPICS[topic].model_validate(body)
        if (event.tenant_id, event.site_id) != scope or key != partition_key(body):
            return None
        if not db.execute("SELECT 1 FROM sites WHERE (tenant_id,id)=(%s,%s) FOR UPDATE", scope).fetchone():
            return None
        return accept(db, scope, event, published=True)
    except (HTTPException, ValidationError):
        return None


def _device_health(db, scope, asset_id):
    readings = db.execute(
        """SELECT e.source_id, e.body
           FROM observation_watermarks w JOIN observation_events e
           ON (e.tenant_id,e.site_id,e.asset_id,e.source_id,e.source_revision)=
              (w.tenant_id,w.site_id,w.asset_id,w.source_id,w.applied)
           WHERE (w.tenant_id,w.site_id,w.asset_id)=(%s,%s,%s)""",
        (*scope, asset_id),
    ).fetchall()
    healthy = True
    for reading in readings:
        evidence = reading["body"]["evidence"]
        if reading["source_id"] == "synthetic-link":
            healthy &= evidence["carrier_up"]
        elif reading["source_id"] == "synthetic-probe":
            healthy &= evidence["received"] == evidence["sent"]
        else:
            healthy &= evidence["healthy"]
    oldest = min(datetime.fromisoformat(r["body"]["observed_at"]) for r in readings)
    return {"healthy": healthy, "observed_at": stamp(oldest)}


def project_message(db, key, raw, broker, *, source_scope=None):
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
        if broker[0] in SOURCE_TOPICS:
            body = _record_source(db, key, body, broker[0], source_scope)
            if body is None:
                _quarantine(db, broker, raw)
                return False
        else:
            row = db.execute(
                "SELECT tenant_id,site_id,event_id,body FROM observation_events WHERE (tenant_id,event_id)=(%s,%s)",
                (body["tenant_id"], event_id),
            ).fetchone()
            if not row:
                _quarantine(db, broker, raw)
                return False
            db.execute("SELECT 1 FROM sites WHERE (tenant_id,id)=(%s,%s) FOR UPDATE", (row["tenant_id"], row["site_id"]))
            matched_key = key == partition_key(row["body"])
            if not matched_key or json.dumps(body, sort_keys=True) != json.dumps(row["body"], sort_keys=True):
                _quarantine(db, broker, raw, row if matched_key else None)
                return False
            body = row["body"]
        scope = (body["tenant_id"], body["site_id"])
        watermark = (*scope, body["asset_id"], body["source_id"])
        applied = db.execute(
            """SELECT applied FROM observation_watermarks
               WHERE (tenant_id,site_id,asset_id,source_id)=(%s,%s,%s,%s)""", watermark,
        ).fetchone()["applied"]
        if body["source_revision"] > applied:
            state = site_state(db, scope)
            graph = deepcopy(state["graph"])
            revision = state["snapshot_revision"] + 1
            graph["revision"] = revision
            db.execute("""UPDATE observation_watermarks SET applied=%s
                          WHERE (tenant_id,site_id,asset_id,source_id)=(%s,%s,%s,%s)""",
                       (body["source_revision"], *watermark))
            graph["devices"][body["asset_id"]].update(_device_health(db, scope, body["asset_id"]))
            db.execute("INSERT INTO rehearsal_snapshots VALUES (%s,%s,%s,%s)", (*scope, revision, Jsonb(graph)))
            db.execute("UPDATE rehearsal_sites SET snapshot_revision=%s WHERE (tenant_id,site_id)=(%s,%s)",
                       (revision, *scope))
        db.execute("""UPDATE observation_events SET processed_at=COALESCE(processed_at,now())
                      WHERE (tenant_id,event_id)=(%s,%s)""", (scope[0], event_id))
        db.execute("""UPDATE observation_quarantine SET repaired_at=now()
                      WHERE (tenant_id,site_id,event_id)=(%s,%s,%s) AND repaired_at IS NULL""", (*scope, event_id))
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
