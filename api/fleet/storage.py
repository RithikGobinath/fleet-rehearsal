from pathlib import Path

from psycopg import Connection
from psycopg.types.json import Jsonb


MIGRATIONS = (
    "001_inventory.sql", "002_raw_evidence.sql", "003_site_roles.sql",
    "004_inventory_revision.sql", "005_rehearsal.sql", "006_observations.sql",
    "007_hosted_identity.sql",
    "008_worker_heartbeat.sql",
)
EVIDENCE_FIELDS = ("running_release", "release_status", "trunk_port", "trustsec", "macsec", "observed_at")
INVENTORY_PAGE_SQL = """WITH visible AS MATERIALIZED (
    SELECT e.* FROM inventory_evidence e
    JOIN grants g ON (g.tenant_id, g.site_id) = (e.tenant_id, e.site_id)
    WHERE g.principal_id = %(caller)s AND e.tenant_id = %(tenant)s
        AND (%(finding)s::text IS NULL OR e.finding = %(finding)s)
), page AS (
    SELECT * FROM visible WHERE %(after)s::text IS NULL OR id > %(after)s
    ORDER BY id LIMIT %(fetch)s
)
SELECT (SELECT count(*) FROM visible) AS total,
    COALESCE((SELECT json_agg(page ORDER BY id) FROM page), '[]'::json) AS items"""


def initialize_inventory(conn: Connection, seed: dict) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(812670)")
        cur.execute("""CREATE TABLE IF NOT EXISTS inventory_migrations (
            version integer PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
        )""")
        versions = list(range(1, len(MIGRATIONS) + 1))
        applied = [version for (version,) in cur.execute("SELECT version FROM inventory_migrations ORDER BY version")]
        if applied != versions[:len(applied)]:
            raise RuntimeError("Unexpected inventory migration history.")
        directory = Path(__file__).with_name("migrations")
        for version, filename in zip(versions[len(applied):], MIGRATIONS[len(applied):], strict=True):
            cur.execute((directory / filename).read_text(encoding="utf-8"))
            cur.execute("INSERT INTO inventory_migrations (version) VALUES (%s)", (version,))

        cur.executemany("INSERT INTO tenants (id, label) VALUES (%s, %s) ON CONFLICT DO NOTHING", seed["tenants"])
        cur.executemany(
            "INSERT INTO sites (tenant_id, id, label) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", seed["sites"],
        )
        cur.executemany(
            "INSERT INTO principals (id, label) VALUES (%s, %s) ON CONFLICT DO NOTHING", seed["principals"],
        )
        cur.executemany(
            "INSERT INTO grants (principal_id, tenant_id, site_id, role) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            seed["grants"],
        )
        cur.executemany(
            """INSERT INTO assets (
                tenant_id, id, site_id, name, product, running_release, release_status,
                trunk_port, trustsec, macsec, observed_at, raw_evidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING""",
            [(*row, Jsonb(dict(zip(EVIDENCE_FIELDS, row[5:], strict=True)))) for row in seed["assets"]],
        )


def worker_heartbeat(db: Connection, worker: str) -> None:
    db.execute("""INSERT INTO worker_heartbeats VALUES (%s, clock_timestamp())
                  ON CONFLICT (worker) DO UPDATE SET last_seen=EXCLUDED.last_seen""", (worker,))
