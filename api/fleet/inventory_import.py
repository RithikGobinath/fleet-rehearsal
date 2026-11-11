from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from psycopg import Connection
from psycopg.types.json import Jsonb

ID = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
MAX_BYTES = 128 * 1024


class InventoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ID
    name: Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f]+$")]
    product: Literal["Catalyst 9300"]
    running_release: ID
    release_status: Literal["affected", "unaffected", "unknown"]
    trunk_port: Annotated[bool, Field(strict=True)] | None = None
    trustsec: Annotated[bool, Field(strict=True)] | None = None
    macsec: Annotated[bool, Field(strict=True)] | None = None
    observed_at: datetime
    source_revision: Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]

    @field_validator("observed_at", mode="before")
    @classmethod
    def utc_timestamp(cls, value):
        if not isinstance(value, str):
            raise ValueError("Use a UTC timestamp.")
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() != timedelta(0):
            raise ValueError("Use a UTC timestamp.")
        return stamp


class InventoryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: Annotated[list[InventoryRow], Field(min_length=1, max_length=250)]


IMPORT_SCHEMA = InventoryBatch.model_json_schema()
IMPORT_SCHEMA["properties"]["rows"]["items"] = IMPORT_SCHEMA.pop("$defs")["InventoryRow"]


async def read_inventory_import(request: Request) -> InventoryBatch:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Send an application/json inventory batch.")
    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_BYTES - len(body):
            raise HTTPException(413, "Inventory batch exceeds 128 KiB.")
        body.extend(chunk)
    try:
        batch = InventoryBatch.model_validate_json(body)
    except ValidationError as error:
        location = error.errors(include_input=False)[0]["loc"]
        row = location[1] + 1 if len(location) > 1 and location[0] == "rows" and isinstance(location[1], int) else None
        raise HTTPException(422, {"code": "invalid_inventory", "row": row}) from None
    seen = set()
    for index, row in enumerate(batch.rows, 1):
        if row.id in seen:
            raise HTTPException(422, {"code": "duplicate_asset", "row": index})
        seen.add(row.id)
    return batch


def store_inventory_import(db: Connection, tenant: str, site: str, batch: InventoryBatch) -> int:
    with db.transaction():
        for index, row in sorted(enumerate(batch.rows, 1), key=lambda item: item[1].id):
            raw = row.model_dump(mode="json", exclude={"id", "name", "product", "source_revision"})
            result = db.execute(
                """INSERT INTO assets AS a (tenant_id, site_id, id, name, product, running_release,
                       release_status, trunk_port, trustsec, macsec, observed_at, raw_evidence, source_revision)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id, id) DO UPDATE SET
                       name = EXCLUDED.name, product = EXCLUDED.product, running_release = EXCLUDED.running_release,
                       release_status = EXCLUDED.release_status, trunk_port = EXCLUDED.trunk_port,
                       trustsec = EXCLUDED.trustsec, macsec = EXCLUDED.macsec, observed_at = EXCLUDED.observed_at,
                       raw_evidence = EXCLUDED.raw_evidence, source_revision = EXCLUDED.source_revision
                   WHERE a.site_id = EXCLUDED.site_id AND a.source_revision < EXCLUDED.source_revision
                   RETURNING id""",
                (tenant, site, row.id, row.name, row.product, row.running_release, row.release_status,
                 row.trunk_port, row.trustsec, row.macsec, row.observed_at, Jsonb(raw), row.source_revision),
            ).fetchone()
            if result is None:
                repeated = db.execute(
                    """SELECT 1 FROM assets WHERE tenant_id = %s AND site_id = %s AND id = %s
                       AND source_revision = %s AND name = %s AND product = %s AND raw_evidence = %s""",
                    (tenant, site, row.id, row.source_revision, row.name, row.product, Jsonb(raw)),
                ).fetchone()
                if repeated is None:
                    raise HTTPException(409, {"code": "inventory_conflict", "row": index})
    return len(batch.rows)
