import json
import os
import unittest
from pathlib import Path

import psycopg
from psycopg.errors import ForeignKeyViolation

from fleet.storage import INVENTORY_PAGE_SQL, initialize_inventory


SEED = json.loads(Path("fixtures/inventory.json").read_text(encoding="utf-8"))
LEGACY_DDL = (Path(__file__).resolve().parents[1] / "fleet/migrations/001_inventory.sql").read_text(encoding="utf-8")


class StorageTests(unittest.TestCase):
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
            self.assertEqual(len(assets), 5)
            self.assertEqual([row[0] for row in versions], [1, 2])
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
            self.assertEqual(total, 3)
            self.assertEqual([row["id"] for row in rows], ["harbor-02", "harbor-03"])
