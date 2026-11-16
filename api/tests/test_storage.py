import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi import HTTPException
from psycopg import sql
from psycopg.errors import DivisionByZero, ForeignKeyViolation
from psycopg.rows import dict_row

from fleet.main import require_site_role
from fleet.storage import INVENTORY_PAGE_SQL, MIGRATIONS, initialize_inventory, schema_ready


SEED = json.loads(Path("fixtures/inventory.json").read_text(encoding="utf-8"))
LEGACY_DDL = (Path(__file__).resolve().parents[1] / "fleet/migrations/001_inventory.sql").read_text(encoding="utf-8")


class StorageTests(unittest.TestCase):
    def test_concurrent_migrations_wait_and_leave_one_version_of_each_row(self):
        database = os.environ["DATABASE_URL"]
        schema = f"migration_concurrency_{uuid4().hex}"
        name = sql.Identifier(schema)
        pending = Queue(maxsize=1)

        def connect():
            return psycopg.connect(database, autocommit=True, connect_timeout=5,
                                   options=f"-c search_path={schema} -c statement_timeout=15000")

        def migrate_second():
            with connect() as db:
                pending.put(db.info.backend_pid)
                initialize_inventory(db, SEED)

        with connect() as first, connect() as observer, ThreadPoolExecutor(max_workers=1) as pool:
            observer.execute(sql.SQL("CREATE SCHEMA {}").format(name))
            try:
                with first.transaction():
                    initialize_inventory(first, SEED)
                    second = pool.submit(migrate_second)
                    pid = pending.get(timeout=5)
                    deadline = monotonic() + 8
                    while monotonic() < deadline:
                        waiting = observer.execute(
                            """SELECT %s = ANY(pg_blocking_pids(%s)) AND EXISTS (
                                SELECT 1 FROM pg_locks WHERE pid=%s AND locktype='advisory'
                                AND objid=812670 AND NOT granted)""",
                            (first.info.backend_pid, pid, pid),
                        ).fetchone()[0]
                        if waiting:
                            break
                        self.assertFalse(second.done(), "Second migration finished before the transaction committed.")
                        sleep(0.02)
                    else:
                        self.fail("Second migration never waited on the migration lock.")
                    self.assertIsNone(observer.execute("SELECT to_regclass('inventory_migrations')").fetchone()[0])
                second.result(timeout=15)
                versions = observer.execute("SELECT version FROM inventory_migrations ORDER BY version").fetchall()
                self.assertEqual(versions, [(version,) for version in range(1, len(MIGRATIONS) + 1)])
                self.assertEqual(observer.execute("SELECT count(*) FROM assets").fetchone()[0], len(SEED["assets"]))
            finally:
                observer.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(name))

    def test_readiness_accepts_an_additive_upgrade_and_rejects_missing_versions(self):
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction(force_rollback=True):
            conn.execute("SET LOCAL search_path TO pg_temp")
            self.assertFalse(schema_ready(conn))
            conn.execute("CREATE TEMP TABLE inventory_migrations (version integer PRIMARY KEY)")
            conn.execute("INSERT INTO inventory_migrations SELECT generate_series(1,%s)", (len(MIGRATIONS) + 1,))
            self.assertTrue(schema_ready(conn))
            conn.execute("DELETE FROM inventory_migrations WHERE version=1")
            self.assertFalse(schema_ready(conn))

    def test_failed_migration_preserves_existing_schema_and_ledger(self):
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction(force_rollback=True):
            conn.execute("CREATE SCHEMA migration_failure_test")
            conn.execute("SET LOCAL search_path TO migration_failure_test")
            initialize_inventory(conn, SEED)
            with TemporaryDirectory() as directory:
                migrations = Path(directory) / "migrations"
                migrations.mkdir()
                (migrations / "broken.sql").write_text(
                    "CREATE TABLE partial_upgrade (id int); SELECT 1 / 0;", encoding="utf-8",
                )
                with patch("fleet.storage.__file__", str(Path(directory) / "storage.py")), \
                        patch("fleet.storage.MIGRATIONS", (*MIGRATIONS, "broken.sql")), \
                        self.assertRaises(DivisionByZero):
                    initialize_inventory(conn, SEED)
            self.assertTrue(schema_ready(conn))
            self.assertEqual(conn.execute("SELECT count(*) FROM inventory_migrations").fetchone()[0], len(MIGRATIONS))
            self.assertIsNone(conn.execute("SELECT to_regclass('partial_upgrade')").fetchone()[0])

    def test_adopts_legacy_database_and_reseeds_without_changing_evidence(self):
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction(force_rollback=True):
            conn.execute("CREATE SCHEMA migration_test")
            conn.execute("SET LOCAL search_path TO migration_test")
            conn.execute(LEGACY_DDL)
            conn.execute("INSERT INTO tenants VALUES ('northstar', 'Legacy tenant')")
            conn.execute("INSERT INTO sites VALUES ('northstar', 'harbor', 'Legacy harbor')")
            conn.execute("""INSERT INTO assets VALUES (
                'northstar', 'harbor-01', 'harbor', 'Existing device', 'Catalyst 9300',
                'lab-affected', 'affected', TRUE, NULL, FALSE, '2026-11-10T18:00:00Z'
            )""")

            def read_state():
                assets = conn.execute(
                    "SELECT tenant_id, id, name, raw_evidence FROM assets ORDER BY tenant_id, id",
                ).fetchall()
                versions = conn.execute("SELECT * FROM inventory_migrations ORDER BY version").fetchall()
                return assets, versions

            initialize_inventory(conn, SEED)
            assets, versions = read_state()
            self.assertEqual(len(assets), 6)
            self.assertEqual([row[0] for row in versions], list(range(1, len(MIGRATIONS) + 1)))
            self.assertEqual(assets[0][2], "Existing device")
            self.assertIsNone(assets[0][3]["trustsec"])
            self.assertIsNone(assets[2][3]["trunk_port"])
            self.assertEqual(assets[2][3]["observed_at"], "2026-11-10T18:00:00Z")
            initialize_inventory(conn, SEED)
            self.assertEqual(read_state(), (assets, versions))

    def test_asset_cannot_reference_another_tenants_site(self):
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction(force_rollback=True):
            with self.assertRaises(ForeignKeyViolation), conn.transaction():
                conn.execute("""INSERT INTO assets (
                    tenant_id, id, site_id, name, product, running_release, release_status, observed_at, raw_evidence
                ) VALUES ('summit', 'cross-tenant-test', 'harbor', 'Invalid', 'Catalyst 9300',
                    'lab-fixed', 'unaffected', '2026-11-10T18:00:00Z', '{}')""")

    def test_approval_requires_an_approver_at_that_site(self):
        with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
            require_site_role(conn, "demo-approver", "northstar", "harbor", "customer_approver")
            for caller, site, status in (("demo-partner", "harbor", 403), ("demo-approver", "river", 404)):
                with self.subTest(caller=caller, site=site), self.assertRaises(HTTPException) as denied:
                    require_site_role(conn, caller, "northstar", site, "customer_approver")
                self.assertEqual(denied.exception.status_code, status)

    def test_scoped_inventory_uses_an_index_with_many_ungranted_sites(self):
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction(force_rollback=True):
            conn.execute("CREATE SCHEMA inventory_plan_test")
            conn.execute("SET LOCAL search_path TO inventory_plan_test")
            initialize_inventory(conn, SEED)
            conn.execute("""INSERT INTO sites
                SELECT 'northstar', 'lab-' || n, 'Synthetic site ' || n FROM generate_series(1, 40) AS n""")
            conn.execute("""INSERT INTO assets (
                tenant_id, site_id, id, name, product, running_release, release_status, observed_at, raw_evidence
            )
            SELECT 'northstar', site.id, site.id || '-' || lpad(n::text, 3, '0'), 'Synthetic asset',
                'Catalyst 9300', 'lab-fixed', 'unaffected', '2026-11-10T18:00:00Z', '{}'
            FROM sites site CROSS JOIN generate_series(1, 300) AS n
            WHERE site.tenant_id = 'northstar' AND site.id LIKE 'lab-%'""")
            conn.execute("ANALYZE assets, sites, grants")
            params = {"caller": "demo-partner", "tenant": "northstar", "finding": None, "after": "harbor-01", "fetch": 2}
            plan = conn.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + INVENTORY_PAGE_SQL, params).fetchone()[0]
            self.assertIn('"Index Name": "assets_by_site"', json.dumps(plan))
            total, rows = conn.execute(INVENTORY_PAGE_SQL, params).fetchone()
            self.assertEqual(total, 4)
            self.assertEqual([row["id"] for row in rows], ["harbor-02", "harbor-03"])
