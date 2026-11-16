import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from fleet.rehearsal import process_one
from fleet.scenario import apply_scenario

BASE = os.environ.get("FLEET_TEST_URL", "http://127.0.0.1:8000")


def call(path, actor=None, body=None, key=None):
    headers = {}
    if actor:
        headers["Authorization"] = f"Bearer {actor}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = key or uuid4().hex
    request = Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        response = urlopen(request, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response), response.headers.get("Location")


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


class RehearsalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tokens = {
            actor: call("/v1/demo/session", body={"principal_id": actor})[1]["access_token"]
            for actor in ("demo-partner", "demo-approver", "demo-northstar", "demo-summit")
        }

    def make_site(self):
        tenant = "test-" + uuid4().hex
        scope = (tenant, "harbor")
        fixture = json.loads(Path("fixtures/peer_down_after_approval.json").read_text(encoding="utf-8"))
        inventory = json.loads(Path("fixtures/inventory.json").read_text(encoding="utf-8"))
        with connect() as db:
            db.execute("INSERT INTO tenants VALUES (%s,'Rehearsal test')", (tenant,))
            db.execute("INSERT INTO sites VALUES (%s,%s,'Rehearsal test')", scope)
            db.execute("INSERT INTO rehearsal_snapshots VALUES (%s,%s,1,%s)", (*scope, Jsonb(fixture["snapshot"])))
            db.execute("INSERT INTO rehearsal_policies VALUES (%s,%s,1,%s)", (*scope, Jsonb(fixture["policy"])))
            db.execute("""INSERT INTO rehearsal_sites (tenant_id,site_id,snapshot_revision,policy_revision,clock_at)
                          VALUES (%s,%s,1,1,%s)""", (*scope, "2026-11-10T19:00:00Z"))
            with db.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO assets (tenant_id,id,site_id,name,product,running_release,release_status,
                           trunk_port,trustsec,macsec,observed_at,raw_evidence)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'{}')""",
                    [(tenant, *row[1:]) for row in inventory["assets"] if row[0] == "northstar" and row[2] == "harbor"],
                )
                cursor.executemany("INSERT INTO grants (principal_id,tenant_id,site_id,role) VALUES (%s,%s,%s,%s)",
                                   [(actor, *scope, role) for actor, role in (
                                       ("demo-partner", "partner_proposer"), ("demo-approver", "customer_approver"),
                                       ("demo-northstar", "operator"))])
        return scope, f"/v1/tenants/{tenant}/sites/harbor"

    def import_fixed_peer(self, path):
        status, result, _ = call(f"{path}/inventory/import", self.tokens["demo-northstar"], {"rows": [{
            "id": "harbor-02", "name": "Harbor access switch 02", "product": "Catalyst 9300",
            "running_release": "lab-fixed", "release_status": "unaffected", "trunk_port": True,
            "trustsec": False, "macsec": False, "observed_at": "2026-11-10T18:30:00Z", "source_revision": 2,
        }]})
        self.assertEqual((status, result), (200, {"accepted": 1}))

    def preview(self, path, name="dispatch", base=0):
        status, plan, location = call(f"{path}/plans/{name}/revisions", self.tokens["demo-partner"],
                                      {"base_revision": base})
        self.assertEqual(status, 201, plan)
        self.assertEqual(location, f"/api{path}/plans/{name}/revisions/{base + 1}")
        return plan

    def approve(self, path, plan):
        payload = {"expected_fingerprint": plan["fingerprint"]}
        url = f"{path}/plans/{plan['plan_id']}/revisions/{plan['revision']}/approval"
        self.assertEqual(call(url, self.tokens["demo-partner"], payload)[0], 403)
        status, approval, _ = call(url, self.tokens["demo-approver"], payload)
        self.assertEqual(status, 201, approval)
        return approval

    def start(self, path, plan, key=None, number=1):
        url = f"{path}/plans/{plan['plan_id']}/revisions/{plan['revision']}/waves/{number}/runs"
        return call(url, self.tokens["demo-northstar"], {"expected_fingerprint": plan["fingerprint"]}, key)

    def work(self, seed=1):
        with connect() as db:
            self.assertTrue(process_one(db, simulation_seed=seed))

    def events(self, path):
        return call(f"{path}/audit", self.tokens["demo-approver"])[1]["items"]

    def test_immutable_revisions_and_scoped_reads(self):
        scope, path = self.make_site()
        old = self.preview(path)
        self.assertEqual([wave["device_ids"] for wave in old["waves"]], [["harbor-01"], ["harbor-02"]])
        self.assertEqual(old["deferred"], [
            {"asset_id": "harbor-03", "reason": "advisory_evidence_missing"},
            {"asset_id": "harbor-04", "reason": "advisory_unaffected"},
        ])
        with connect() as db:
            apply_scenario(db, scope, "peer-down")
        new = self.preview(path, base=1)
        self.assertEqual(new["waves"], [])
        self.assertNotEqual(new["fingerprint"], old["fingerprint"])
        url = f"{path}/plans/dispatch/revisions/1"
        self.assertEqual(call(url, self.tokens["demo-partner"])[1], old)
        self.assertEqual(call(url, self.tokens["demo-summit"])[0], 403)
        status, error, _ = call(url + "/approval", self.tokens["demo-approver"],
                                 {"expected_fingerprint": old["fingerprint"]})
        self.assertEqual((status, error["code"], error["current_revision"]), (409, "stale_revision", 2))

    def test_inventory_import_changes_preview_and_invalidates_prior_approval(self):
        _, path = self.make_site()
        unapproved = self.preview(path, name="awaiting-approval")
        approved = self.preview(path)
        self.approve(path, approved)
        self.import_fixed_peer(path)
        status, error, _ = call(f"{path}/plans/awaiting-approval/revisions/1/approval", self.tokens["demo-approver"],
                                 {"expected_fingerprint": unapproved["fingerprint"]})
        self.assertEqual((status, error["code"]), (409, "inventory_changed"))
        status, error, _ = self.start(path, approved)
        self.assertEqual((status, error["code"]), (409, "inventory_changed"))
        fresh = self.preview(path, base=1)
        self.assertNotEqual(fresh["inventory_fingerprint"], approved["inventory_fingerprint"])
        self.assertEqual([wave["device_ids"] for wave in fresh["waves"]], [["harbor-01"]])
        self.assertIn({"asset_id": "harbor-02", "reason": "advisory_unaffected"}, fresh["deferred"])
        self.approve(path, fresh)

    def test_inventory_import_after_reservation_blocks_worker(self):
        _, path = self.make_site()
        plan = self.preview(path)
        self.approve(path, plan)
        status, _, location = self.start(path, plan)
        self.assertEqual(status, 202)
        self.import_fixed_peer(path)
        self.work()
        run = call(location.removeprefix("/api"), self.tokens["demo-approver"])[1]
        self.assertEqual((run["state"], run["reason"], run["devices"]), ("blocked", "inventory_changed", []))
        self.assertNotIn("simulator_started", [event["kind"] for event in self.events(path)])

    def test_peer_down_after_approval_commits_audit_without_a_run(self):
        scope, path = self.make_site()
        plan = self.preview(path)
        approved = self.approve(path, plan)
        with connect() as db:
            snapshot = apply_scenario(db, scope, "peer-down")
        key = uuid4().hex
        status, blocked, location = self.start(path, plan, key)
        self.assertEqual((status, blocked["code"], blocked["snapshot_revision"], location),
                         (409, "alternate_path_unavailable", snapshot, None))
        self.assertEqual(self.start(path, plan, key), (status, blocked, location))
        events = self.events(path)
        self.assertEqual([event["kind"] for event in events], ["plan_previewed", "plan_approved", "wave_blocked"])
        self.assertEqual(events[-1]["audit_id"], blocked["audit_id"])
        self.assertNotIn("run_id", events[-1])
        self.assertEqual(call(f"{path}/plans/dispatch/revisions/1", self.tokens["demo-approver"])[1]["approval"], approved)

    def test_concurrent_reservations_and_restart_produce_one_start(self):
        _, path = self.make_site()
        plan = self.preview(path)
        self.approve(path, plan)
        keys = [uuid4().hex, uuid4().hex]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda key: self.start(path, plan, key), keys))
        self.assertEqual(sorted(result[0] for result in results), [202, 409])
        index = next(i for i, result in enumerate(results) if result[0] == 202)
        _, receipt, location = results[index]
        self.assertEqual(self.start(path, plan, keys[index]), results[index])
        self.assertEqual(self.start(path, plan, keys[index], number=2)[1]["code"], "idempotency_conflict")
        self.work(seed=None)
        self.work()
        run = call(location.removeprefix("/api"), self.tokens["demo-northstar"])[1]
        self.assertEqual(run["verification"]["seed"], 1 + int(receipt["run_id"][-8:], 16))
        self.assertEqual(run["state"], "succeeded" if run["devices"][0]["boot_succeeded"] else "rolled_back")
        self.assertEqual(self.start(path, plan, keys[index])[1], receipt)
        self.assertEqual(sum(event["kind"] == "simulator_started" for event in self.events(path)), 1)
        _, page, _ = call(f"{path}/audit?limit=1", self.tokens["demo-approver"])
        self.assertTrue(page["next_cursor"])
        self.assertNotEqual(call(f"{path}/audit?after={page['next_cursor']}", self.tokens["demo-approver"])[1]["items"][0],
                            page["items"][0])

    def test_worker_rechecks_grant_evidence_and_window_after_reservation(self):
        for mode, reason in (("peer-down", "alternate_path_unavailable"), ("revoked", "authorization_revoked"),
                             ("expired", "outside_window")):
            with self.subTest(mode=mode):
                scope, path = self.make_site()
                plan = self.preview(path)
                self.approve(path, plan)
                key = uuid4().hex
                status, _, location = self.start(path, plan, key)
                self.assertEqual(status, 202)
                with connect() as db:
                    if mode == "peer-down":
                        apply_scenario(db, scope, mode)
                    elif mode == "revoked":
                        db.execute("INSERT INTO sites VALUES (%s,'river','Other granted site')", (scope[0],))
                        db.execute("INSERT INTO grants VALUES ('demo-northstar',%s,'river','operator')", (scope[0],))
                        db.execute("DELETE FROM grants WHERE (tenant_id,site_id,principal_id)=(%s,%s,'demo-northstar')", scope)
                    else:
                        db.execute("UPDATE rehearsal_sites SET clock_at='2026-11-10T19:05:00Z' WHERE (tenant_id,site_id)=(%s,%s)", scope)
                self.work()
                run = call(location.removeprefix("/api"), self.tokens["demo-approver"])[1]
                self.assertEqual((run["state"], run["reason"], run["devices"]), ("blocked", reason, []))
                self.assertNotIn("simulator_started", [event["kind"] for event in self.events(path)])
                if mode == "revoked":
                    self.assertEqual(self.start(path, plan, key)[0], 404)

    def test_canary_rollback_does_not_unlock_next_wave(self):
        scope, path = self.make_site()
        plan = self.preview(path)
        self.approve(path, plan)
        with connect() as db:
            apply_scenario(db, scope, "canary-fail")
        status, _, location = self.start(path, plan)
        self.assertEqual(status, 202)
        self.work()
        self.work()
        run = call(location.removeprefix("/api"), self.tokens["demo-northstar"])[1]
        self.assertEqual((run["state"], run["reason"]), ("rolled_back", "verification_failed"))
        with connect() as db:
            apply_scenario(db, scope, "healthy-next")
        self.assertEqual(self.start(path, plan, number=2)[1]["code"], "prerequisite_incomplete")

    def test_separate_sites_reserve_concurrently_and_failed_audit_rolls_back_start(self):
        _, first = self.make_site()
        _, second = self.make_site()
        key = uuid4().hex
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda plan_id: call(f"{first}/plans/{plan_id}/revisions",
                self.tokens["demo-partner"], {"base_revision": 0}, key), ("conflict-first", "conflict-second")))
        self.assertEqual(sorted(result[0] for result in results), [201, 409])
        self.assertEqual(next(result[1]["code"] for result in results if result[0] == 409), "idempotency_conflict")
        plans = [self.preview(first), self.preview(second)]
        for path, plan in zip((first, second), plans, strict=True):
            self.approve(path, plan)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda item: self.start(*item), zip((first, second), plans, strict=True)))
        self.assertEqual([result[0] for result in results], [202, 202])
        with patch("fleet.rehearsal.write_audit", side_effect=RuntimeError("injected audit failure")):
            with self.assertRaisesRegex(RuntimeError, "injected audit failure"), connect() as db:
                process_one(db)
        for _, _, location in results:
            self.assertEqual(call(location.removeprefix("/api"), self.tokens["demo-northstar"])[1]["state"], "queued")
        for _ in range(4):
            self.work()
        for path in (first, second):
            self.assertEqual(sum(event["kind"] == "simulator_started" for event in self.events(path)), 1)

    def test_worker_restarts_with_same_draw_and_checks_latest_applied_topology(self):
        scope, path = self.make_site()
        plan = self.preview(path)
        self.approve(path, plan)
        with connect() as db:
            started_revision = apply_scenario(db, scope, "canary-fail")
        status, _, location = self.start(path, plan)
        self.assertEqual(status, 202)
        self.work()
        run_path = location.removeprefix("/api")
        operator = self.tokens["demo-northstar"]
        self.assertEqual(call(run_path, operator)[1]["state"], "running")
        with connect() as db:
            verified_revision = apply_scenario(db, scope, "peer-down")
        self.work()
        result = call(run_path, operator)[1]
        self.assertEqual((result["state"], result["snapshot_revision"]), ("rolled_back", started_revision))
        self.assertFalse(result["devices"][0]["boot_succeeded"])
        evidence = result["verification"]
        self.assertEqual(evidence["simulated_elapsed_seconds"], result["devices"][0]["upgrade_seconds"])
        self.assertEqual(evidence["snapshot_revision"], verified_revision)
        self.assertEqual(evidence["unreachable_services"], ["dispatch"])
        self.assertEqual(evidence["rollback_unreachable_services"], [])
        events = self.events(path)
        starts = [event for event in events if event["kind"] == "simulator_started"]
        self.assertEqual(len(starts), 1)
        self.assertTrue(starts[0]["failure_fixture"])
        self.assertEqual(events[-1]["verification"], evidence)
