"""Publish a local synthetic link reading or probe sample straight to Kafka."""

import argparse
import os
from uuid import uuid4

from confluent_kafka import Producer
from pydantic import ValidationError

from fleet.event_worker import ensure_topic, publish_to
from fleet.observations import LINK_TOPIC, PROBE_TOPIC, SOURCE_TOPICS, partition_key, synthetic_stream_scope


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="source", required=True)
    link = subcommands.add_parser("link")
    link.add_argument("--carrier", choices=("up", "down"), required=True)
    probe = subcommands.add_parser("probe")
    probe.add_argument("--sent", type=int, required=True)
    probe.add_argument("--received", type=int, required=True)
    for command in (link, probe):
        command.add_argument("asset_id")
        command.add_argument("source_revision", type=int)
        command.add_argument("--observed-at", required=True, help="UTC timestamp relative to the rehearsal clock")

    args = parser.parse_args()
    scope = synthetic_stream_scope(os.environ)
    bootstrap = os.environ.get("FLEET_KAFKA_BOOTSTRAP")
    if not scope or not bootstrap:
        parser.error("Set FLEET_SYNTHETIC_STREAM_SCOPE and FLEET_KAFKA_BOOTSTRAP for the local stream.")
    if args.source == "link":
        topic, evidence = LINK_TOPIC, {"carrier_up": args.carrier == "up"}
    else:
        topic, evidence = PROBE_TOPIC, {"sent": args.sent, "received": args.received}
    try:
        event = SOURCE_TOPICS[topic].model_validate({
            "schema_version": 1, "event_id": uuid4(), "tenant_id": scope[0], "site_id": scope[1],
            "asset_id": args.asset_id, "source_id": f"synthetic-{args.source}",
            "source_revision": args.source_revision, "observed_at": args.observed_at, "evidence": evidence,
        })
    except ValidationError as error:
        parser.error(str(error))

    ensure_topic(bootstrap, topic)
    producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True, "message.timeout.ms": 3000})
    publish_to(producer, partition_key(event.model_dump()), event.model_dump_json().encode(), topic)
    print(f"Published {event.event_id} to {topic}")


if __name__ == "__main__":
    main()
