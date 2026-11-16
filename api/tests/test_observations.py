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
from fleet.observations import LINK_TOPIC, PROBE_TOPIC, TOPIC, partition_key, project_message, relay_one
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

    def source_event(self, source, revision, observed_at, **evidence):
        return {"schema_version": 1, "event_id": str(uuid4()), "tenant_id": self.scope[0], "site_id": self.scope[1],
                "asset_id": "harbor-02", "source_id": f"synthetic-{source}", "source_revision": revision,
                "observed_at": observed_at, "evidence": evidence}

    def assert_peer(self, revision, healthy, observed_at):
        with api.connect() as db:
            state = site_state(db, self.scope)
        peer = state["graph"]["devices"]["harbor-02"]
        self.assertEqual((state["snapshot_revision"], peer["healthy"], peer["observed_at"]),
                         (revision, healthy, observed_at))

    def test_source_messages_are_topic_and_scope_bound(self):
        probe = self.source_event("probe", 1, "2026-11-10T18:59:30Z", sent=4, received=4)
        bad_counts = {**probe, "evidence": {"sent": 4, "received": 5}}
        foreign_tenant = {**probe, "tenant_id": "northstar"}
        foreign_site = {**probe, "site_id": "remote"}
        examples = (
            (LINK_TOPIC, partition_key(probe), probe, self.scope),
            (PROBE_TOPIC, partition_key(probe), bad_counts, self.scope),
            (PROBE_TOPIC, partition_key(foreign_tenant), foreign_tenant, self.scope),
            (PROBE_TOPIC, partition_key(foreign_site), foreign_site, self.scope),
            (PROBE_TOPIC, b"wrong-key", probe, self.scope),
            (PROBE_TOPIC, partition_key(probe), probe, None),
            (TOPIC, partition_key(probe), probe, self.scope),
        )
        first_offset = uuid4().int >> 70
        with api.connect() as db:
            for offset, (topic, key, body, scope) in enumerate(examples, first_offset):
                with self.subTest(topic=topic, offset=offset):
                    self.assertFalse(project_message(db, key, json.dumps(body).encode(), (topic, 0, offset),
                                                     source_scope=scope))
            self.assertIsNone(db.execute("SELECT 1 FROM observation_events WHERE event_id=%s",
                                         (probe["event_id"],)).fetchone())
            for row in db.execute("""SELECT tenant_id,site_id,event_id,payload_hash FROM observation_quarantine
                                     WHERE message_offset >= %s AND message_offset < %s""",
                                  (first_offset, first_offset + len(examples))).fetchall():
                self.assertEqual((row["tenant_id"], row["site_id"], row["event_id"]), (None, None, None))
                self.assertEqual(len(row["payload_hash"]), 64)
        self.assert_peer(1, True, "2026-11-10T18:59:00Z")

    def test_source_fusion_has_independent_watermarks(self):
        down = self.source_event("link", 7, "2026-11-10T18:59:30Z", carrier_up=False)
        probe = self.source_event("probe", 2, "2026-11-10T18:59:45Z", sent=4, received=4)
        older_up = self.source_event("link", 6, "2026-11-10T18:59:55Z", carrier_up=True)
        recovered = self.source_event("link", 8, "2026-11-10T18:59:50Z", carrier_up=True)
        degraded = self.source_event("probe", 3, "2026-11-10T18:59:55Z", sent=4, received=3)
        cases = (
            (LINK_TOPIC, down, (2, False, down["observed_at"])),
            (PROBE_TOPIC, probe, (3, False, down["observed_at"])),
            (LINK_TOPIC, older_up, (3, False, down["observed_at"])),
            (LINK_TOPIC, down, (3, False, down["observed_at"])),
            (LINK_TOPIC, recovered, (4, True, probe["observed_at"])),
            (PROBE_TOPIC, degraded, (5, False, recovered["observed_at"])),
        )
        for offset, (topic, body, expected) in enumerate(cases):
            with api.connect() as db:
                self.assertTrue(project_message(db, partition_key(body), json.dumps(body).encode(),
                                                (topic, 0, offset), source_scope=self.scope))
            self.assert_peer(*expected)
        with api.connect() as db:
            rows = db.execute("""SELECT source_id,accepted,applied FROM observation_watermarks
                                 WHERE (tenant_id,site_id)=(%s,%s) ORDER BY source_id""", self.scope).fetchall()
            self.assertEqual([(row["source_id"], row["accepted"], row["applied"]) for row in rows],
                             [("synthetic-link", 8, 8), ("synthetic-probe", 3, 3)])
            rows = db.execute("""SELECT published_at,processed_at FROM observation_events
                                 WHERE (tenant_id,site_id)=(%s,%s)""", self.scope).fetchall()
            self.assertEqual(len(rows), 5)
            self.assertTrue(all(row["published_at"] and row["processed_at"] for row in rows))

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

    @unittest.skipUnless(os.environ.get("FLEET_KAFKA_BOOTSTRAP"), "Requires the Compose Kafka broker.")
    def test_real_broker_stores_independent_readings_before_projection(self):
        from confluent_kafka import Producer

        bootstrap = os.environ["FLEET_KAFKA_BOOTSTRAP"]
        topics = (LINK_TOPIC, PROBE_TOPIC)
        for topic in topics:
            ensure_topic(bootstrap, topic)
        producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True})
        down = self.source_event("link", 4, "2026-11-10T18:59:30Z", carrier_up=False)
        probe = self.source_event("probe", 4, "2026-11-10T18:59:45Z", sent=4, received=4)

        publish_to(producer, partition_key(down), json.dumps(down).encode(), LINK_TOPIC)
        with api.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM observation_events WHERE event_id=%s",
                                         (down["event_id"],)).fetchone())
        self.assert_peer(1, True, "2026-11-10T18:59:00Z")

        group = "fleet-test-sources-" + uuid4().hex
        consumer = new_consumer(bootstrap, group, topics)
        try:
            deadline = time.monotonic() + 25
            while self.snapshot() == 1 and time.monotonic() < deadline:
                with api.connect() as db:
                    consume_one(db, consumer, timeout=3, source_scope=self.scope)
            self.assert_peer(2, False, down["observed_at"])

            publish_to(producer, partition_key(probe), json.dumps(probe).encode(), PROBE_TOPIC)
            with api.connect() as db:
                self.assertIsNone(db.execute("SELECT 1 FROM observation_events WHERE event_id=%s",
                                             (probe["event_id"],)).fetchone())

            def crash():
                if self.snapshot() == 3:
                    raise RuntimeError("source projection committed before offset")

            with self.assertRaisesRegex(RuntimeError, "committed before offset"):
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    with api.connect() as db:
                        consume_one(db, consumer, timeout=3, before_commit=crash, source_scope=self.scope)
            self.assert_peer(3, False, down["observed_at"])
        finally:
            consumer.close()
        restarted = new_consumer(bootstrap, group, topics)
        try:
            with api.connect() as db:
                self.assertTrue(consume_one(db, restarted, timeout=15, source_scope=self.scope))
            self.assert_peer(3, False, down["observed_at"])
        finally:
            restarted.close()
