import json
import os
import time
import unittest
from pathlib import Path
from uuid import uuid4
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from psycopg.types.json import Jsonb

import test_rehearsal as api
from fleet.event_worker import consume_one, ensure_topic, new_consumer, publish_to
from fleet.observations import TOPIC, project_message, relay_one
from fleet.rehearsal import site_state


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.tenant = "zz-event-" + uuid4().hex
        self.scope = (self.tenant, "harbor")
        self.path = f"/v1/tenants/{self.tenant}/sites/harbor"
        fixture = json.loads(Path("fixtures/peer_down_after_approval.json").read_text(encoding="utf-8"))
        with api.connect() as db:
            db.execute("INSERT INTO tenants VALUES (%s,'Event test')", (self.tenant,))
            db.execute("INSERT INTO sites VALUES (%s,%s,'Event test')", self.scope)
            db.execute("""INSERT INTO assets (tenant_id,id,site_id,name,product,running_release,release_status,
                          trunk_port,trustsec,macsec,observed_at,raw_evidence)
                          SELECT %s,id,site_id,name,product,running_release,release_status,trunk_port,trustsec,
                                 macsec,observed_at,raw_evidence FROM assets
                          WHERE tenant_id='northstar' AND site_id='harbor'""", (self.tenant,))
            db.execute("INSERT INTO rehearsal_snapshots VALUES (%s,%s,1,%s)", (*self.scope, Jsonb(fixture["snapshot"])))
            db.execute("INSERT INTO rehearsal_policies VALUES (%s,%s,1,%s)", (*self.scope, Jsonb(fixture["policy"])))
            db.execute("""INSERT INTO rehearsal_sites (tenant_id,site_id,snapshot_revision,policy_revision,clock_at)
                          VALUES (%s,%s,1,1,%s)""", (*self.scope, "2026-11-10T19:00:00Z"))
            with db.cursor() as cursor:
                cursor.executemany("INSERT INTO grants (principal_id,tenant_id,site_id,role) VALUES (%s,%s,%s,%s)",
                                   [(actor, *self.scope, role) for actor, role in (
                                       ("demo-partner", "partner_proposer"), ("demo-approver", "customer_approver"),
                                       ("demo-northstar", "operator"))])
        self.tokens = {actor: api.call("/v1/demo/session", body={"principal_id": actor})[1]["access_token"]
                       for actor in ("demo-partner", "demo-approver", "demo-northstar", "demo-summit")}
        self.operator = self.tokens["demo-northstar"]

    def accepted(self, revision=5, healthy=False):
        event = {"schema_version": 1, "event_id": str(uuid4()), "asset_id": "harbor-02",
                 "source_id": "synthetic-health", "source_revision": revision,
                 "observed_at": "2026-11-10T19:00:30Z" if not healthy else "2026-11-10T18:58:00Z",
                 "evidence": {"healthy": healthy}}
        status, receipt, _ = api.call(self.path + "/observations", self.operator, event)
        self.assertEqual(status, 202, receipt)
        self.assertEqual(api.call(self.path + "/observations", self.operator, event)[1], receipt)
        return receipt

    def snapshot(self):
        with api.connect() as db:
            return site_state(db, self.scope)["snapshot_revision"]

    def test_pending_quarantine_replay_and_late_delivery(self):
        plan_path = self.path + "/plans/dispatch/revisions"
        status, plan, _ = api.call(plan_path, self.tokens["demo-partner"], {"base_revision": 0})
        self.assertEqual(status, 201, plan)
        body = {"expected_fingerprint": plan["fingerprint"]}
        self.assertEqual(api.call(plan_path + "/1/approval", self.tokens["demo-approver"], body)[0], 201)
        receipt = self.accepted()
        context_url = self.path + "/rehearsal/context"
        self.assertTrue(api.call(context_url, self.operator)[1]["pending"])
        start = plan_path + "/1/waves/1/runs"
        request = Request(api.BASE + start, data=json.dumps(body).encode(), headers={
            "Authorization": f"Bearer {self.operator}", "Content-Type": "application/json",
            "Idempotency-Key": uuid4().hex,
        })
        with self.assertRaises(HTTPError) as blocked:
            urlopen(request, timeout=5)
        with blocked.exception as response:
            self.assertEqual(response.code, 409)
            correlation = response.headers["X-Request-ID"]
            decision = json.load(response)
        self.assertEqual(decision["code"], "pending_evidence")
        audit = api.call(self.path + "/audit", self.operator)[1]["items"]
        self.assertEqual(next(item["correlation_id"] for item in audit if item["audit_id"] == decision["audit_id"]),
                         correlation)
        with urlopen(api.BASE + "/metrics", timeout=5) as response:
            metrics = response.read().decode()
        self.assertRegex(metrics, r"fleet_observations_unapplied [1-9]\d*")
        self.assertRegex(metrics, r'fleet_rehearsal_blocked_total\{reason="pending_evidence"\} [1-9]\d*')
        self.assertNotIn(self.tenant, metrics)

        delivered = []

        def crash_after_send(key, raw):
            delivered.append((key, raw))
            raise RuntimeError("relay interrupted after send")

        with self.assertRaisesRegex(RuntimeError, "relay interrupted"), api.connect() as db:
            relay_one(db, crash_after_send)
        with api.connect() as db:
            self.assertTrue(relay_one(db, lambda key, raw: delivered.append((key, raw))))
        self.assertEqual(delivered[0], delivered[1])
        key, raw = delivered[-1]
        topic = f"{TOPIC}-{self.tenant}"
        corrupted = {**receipt, "evidence": {"healthy": True}}
        with api.connect() as db:
            self.assertFalse(project_message(db, key, json.dumps(corrupted).encode(), (topic, 0, 1)))
        quarantine_url = self.path + "/observations/quarantine"
        entry = api.call(quarantine_url, self.operator)[1]["items"][0]
        self.assertEqual(entry["event_id"], receipt["event_id"])
        self.assertNotIn("payload", entry)
        self.assertTrue(api.call(context_url, self.operator)[1]["pending"])
        replay = self.path + f"/observations/{receipt['event_id']}/replay"
        self.assertEqual(api.call(replay, self.tokens["demo-summit"], {})[0], 403)
        self.assertEqual(api.call(replay, self.operator, {})[0], 202)
        with api.connect() as db:
            self.assertTrue(relay_one(db, lambda k, data: delivered.append((k, data))))
        self.assertEqual(delivered[-1], (key, raw))
        for offset in (2, 3):
            with api.connect() as db:
                self.assertTrue(project_message(db, key, raw, (topic, 0, offset)))
        self.assertEqual(self.snapshot(), 2)
        self.assertFalse(api.call(context_url, self.operator)[1]["pending"])
        self.assertIsNotNone(api.call(quarantine_url, self.operator)[1]["items"][0]["repaired_at"])
        self.accepted(revision=4, healthy=True)
        with api.connect() as db:
            self.assertTrue(relay_one(db, lambda k, data: delivered.append((k, data))))
        with api.connect() as db:
            self.assertTrue(project_message(db, *delivered[-1], (topic, 0, 4)))
            db.execute("SELECT 1 FROM sites WHERE (tenant_id,id)=(%s,%s) FOR UPDATE", self.scope)
            db.execute("UPDATE rehearsal_sites SET clock_at='2026-11-10T19:01:00Z' WHERE (tenant_id,site_id)=(%s,%s)", self.scope)
        self.assertEqual(self.snapshot(), 2)
        self.assertEqual(api.call(start, self.operator, body)[1]["code"], "alternate_path_unavailable")
        self.assertNotIn("simulator_started", [row["kind"] for row in api.call(self.path + "/audit", self.operator)[1]["items"]])

    @unittest.skipUnless(os.environ.get("FLEET_KAFKA_BOOTSTRAP"), "Requires the Compose Kafka broker.")
    def test_real_broker_replays_after_projection_before_offset_commit(self):
        from confluent_kafka import Producer

        bootstrap = os.environ["FLEET_KAFKA_BOOTSTRAP"]
        ensure_topic(bootstrap)
        self.accepted()
        producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True})
        with api.connect() as db:
            self.assertTrue(relay_one(db, lambda key, raw: publish_to(producer, key, raw)))
        group = "fleet-test-" + uuid4().hex
        consumer = new_consumer(bootstrap, group)
        try:
            def crash():
                if self.snapshot() == 2:
                    raise RuntimeError("projector interrupted before offset commit")

            with self.assertRaisesRegex(RuntimeError, "projector interrupted"):
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    with api.connect() as db:
                        consume_one(db, consumer, timeout=5, before_commit=crash)
            self.assertEqual(self.snapshot(), 2)
        finally:
            consumer.close()
        restarted = new_consumer(bootstrap, group)
        try:
            with api.connect() as db:
                self.assertTrue(consume_one(db, restarted, timeout=15))
            self.assertEqual(self.snapshot(), 2)
        finally:
            restarted.close()
