"""Measure duplicate observation recovery against disposable PostgreSQL and Kafka."""

import argparse
import json
import os
import platform
import re
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import psycopg
from confluent_kafka import Producer, TopicPartition, libversion
from confluent_kafka.admin import AdminClient, NewTopic
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from fleet.event_worker import consume_one, new_consumer
from fleet.migrate import migrate
from fleet.observations import ObservationRequest, accept, partition_key
from fleet.rehearsal import approve_plan, preview_plan, process_one, read_run, reserve_wave, site_state, write_once


SCOPE = ("northstar", "harbor")
OBSERVED_AT = "2026-11-10T18:59:00Z"


class ProjectorInterrupted(Exception):
    """An intentional stop after projection and before offset commit."""


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def seed_and_publish(db, bootstrap, topic, unique, copies):
    producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True})
    delivery_errors = []
    previous = None

    def delivered(error, _message):
        if error:
            delivery_errors.append(error)

    for revision in range(1, unique + 1):
        event = ObservationRequest.model_validate({
            "schema_version": 1, "event_id": str(UUID(int=revision)), "asset_id": "harbor-02",
            "source_id": "synthetic-health", "source_revision": revision, "observed_at": OBSERVED_AT,
            "evidence": {"healthy": revision % 2 == 0},
        })
        with db.transaction():
            receipt = accept(db, SCOPE, event)
        key, raw = partition_key(receipt), json.dumps(receipt).encode()
        for index in range(copies):
            value = previous if previous is not None and index >= copies // 2 else raw
            producer.produce(topic, key=key, value=value, on_delivery=delivered)
        previous = raw
        producer.poll(0)
    check(not producer.flush(30) and not delivery_errors, "Kafka did not acknowledge all benchmark deliveries.")
    db.execute("UPDATE observation_events SET published_at=now()")


def replay(db, bootstrap, topic, unique, copies):
    total = unique * copies
    committed_before_restart = total // 2
    partition = TopicPartition(topic, 0)

    def interrupt():
        raise ProjectorInterrupted

    consumer = new_consumer(bootstrap, topic)
    consumer.subscribe([topic])
    try:
        for _ in range(committed_before_restart):
            check(consume_one(db, consumer, timeout=30), "Timed out waiting for a Kafka record.")
        try:
            consume_one(db, consumer, timeout=30, before_commit=interrupt)
        except ProjectorInterrupted:
            pass
        else:
            raise RuntimeError("No interruption was injected before the offset commit.")
        check(consumer.committed([partition], timeout=10)[0].offset == committed_before_restart,
              "The interrupted record's offset was unexpectedly committed.")
    finally:
        consumer.close()

    crash_snapshot = unique // 2 + 2
    check(site_state(db, SCOPE)["snapshot_revision"] == crash_snapshot,
          "The interrupted observation was not durably projected.")
    consumer = new_consumer(bootstrap, topic)
    consumer.subscribe([topic])
    try:
        check(consume_one(db, consumer, timeout=30), "Timed out waiting for the interrupted record.")
        check(consumer.committed([partition], timeout=10)[0].offset == committed_before_restart + 1
              and site_state(db, SCOPE)["snapshot_revision"] == crash_snapshot,
              "The interrupted record was not redelivered exactly once without reapplying it.")
        for _ in range(committed_before_restart + 1, total):
            check(consume_one(db, consumer, timeout=30), "Timed out waiting for a Kafka record after restart.")
        check(consumer.committed([partition], timeout=10)[0].offset == total,
              "The projector did not advance through the complete input.")
    finally:
        consumer.close()


def verify_rehearsal(dsn):
    operator, plan_id, key = "demo-northstar", "bench", "benchmark-start"
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as db:
        with db.transaction():
            _, plan, _ = preview_plan(db, SCOPE, "demo-partner", plan_id, 0, uuid4())
            expected = plan["fingerprint"]
            approve_plan(db, SCOPE, "demo-approver", plan_id, 1, expected, uuid4())

        def start(connection, request_key):
            with connection.transaction():
                return write_once(connection, SCOPE, operator, "start-rehearsal", request_key,
                                  [plan_id, 1, 1, expected],
                                  lambda correlation: reserve_wave(connection, SCOPE, operator, plan_id, 1, 1,
                                                                   expected, correlation))

        first = start(db, key)
        check(first[0] == 202 and start(db, key) == first, "Duplicate start did not return the same queued run.")
        check(process_one(db, simulation_seed=1), "The worker did not pick up the rehearsal.")
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as restarted:
        check(process_one(restarted) and not process_one(restarted), "The worker did not finish once after restart.")
        status, blocked, _ = start(restarted, "new-key")
        check(start(restarted, key) == first and status == 409 and blocked.get("code") == "already_rehearsed",
              "The finished wave could be started again.")
        run = read_run(restarted, SCOPE, first[1]["run_id"])
        count = restarted.execute("SELECT count(*) AS count FROM rehearsal_audit WHERE kind='simulator_started'").fetchone()
        check(run["state"] == "succeeded" and count["count"] == 1,
              "Rehearsal did not finish with exactly one logical start.")
    return count["count"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Run the recovery check with eight broker records.")
    args = parser.parse_args()
    config = conninfo_to_dict(os.environ["DATABASE_URL"])
    bootstrap = os.environ["FLEET_KAFKA_BOOTSTRAP"]
    check(os.environ.get("FLEET_DEMO_MODE") == "local"
          and config.get("host") in ("db", "localhost", "127.0.0.1")
          and not config.keys() & {"hostaddr", "service", "options"}
          and re.fullmatch(r"(kafka|localhost|127\.0\.0\.1):[0-9]{1,5}", bootstrap),
          "Use a disposable local broker and PostgreSQL instance in local demo mode.")

    suffix = uuid4().hex
    database = f"fleet_replay_{suffix}"
    topic = f"fleet-replay-{suffix}"
    admin = AdminClient({"bootstrap.servers": bootstrap})
    unique, copies = (4, 2) if args.quick else (500, 100)
    with psycopg.connect(make_conninfo(**{**config, "dbname": "postgres"}), autocommit=True) as maintenance:
        maintenance.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            dsn = make_conninfo(**{**config, "dbname": database})
            migrate(dsn, Path("fixtures/inventory.json"))
            admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)],
                                operation_timeout=15, request_timeout=20)[topic].result()
            try:
                with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as db:
                    started = monotonic()
                    seed_and_publish(db, bootstrap, topic, unique, copies)
                    published = monotonic()
                    replay(db, bootstrap, topic, unique, copies)
                    duration = monotonic() - published
                    state = site_state(db, SCOPE)
                    expected_peer = {"healthy": True, "observed_at": OBSERVED_AT}
                    peer = state["graph"]["devices"]["harbor-02"]
                    check(all(peer[k] == v for k, v in expected_peer.items())
                          and state["snapshot_revision"] == unique + 1
                          and db.execute("SELECT count(*) AS n FROM observation_events WHERE processed_at IS NOT NULL")
                          .fetchone()["n"] == unique
                          and db.execute("SELECT count(*) AS n FROM observation_quarantine").fetchone()["n"] == 0,
                          "The final observation projection did not match the unique input.")
                    watermark = db.execute("SELECT accepted, applied FROM observation_watermarks").fetchone()
                    check(watermark == {"accepted": unique, "applied": unique}, "The watermarks did not converge.")
                    postgres = db.execute("SHOW server_version").fetchone()["server_version"]
                starts = verify_rehearsal(dsn)
            finally:
                admin.delete_topics([topic], operation_timeout=15, request_timeout=20)[topic].result()
        finally:
            maintenance.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
    print(json.dumps({
        "kafka_records": unique * copies, "unique_observations": unique,
        "duplicate_records": unique * (copies - 1), "uncommitted_redeliveries": 1,
        "duplicates_of_superseded_observations": (unique - 1) * (copies - copies // 2),
        "consumer_restarts": 1, "final_snapshot_revision": unique + 1,
        "final_peer_healthy": True, "simulator_starts": starts,
        "publish_seconds": round(published - started, 3), "replay_seconds": round(duration, 3),
        "records_per_second": round(unique * copies / duration, 2),
        "postgres_version": postgres, "librdkafka_version": libversion()[0],
        "python_version": platform.python_version(), "platform": platform.platform(),
        "cpu_count": os.cpu_count(), "git_sha": os.environ.get("GITHUB_SHA"),
    }, indent=2))


if __name__ == "__main__":
    main()
