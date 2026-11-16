import argparse
import os
import time

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from psycopg.rows import dict_row

from fleet.observations import TOPIC, project_message, relay_one
from fleet.settings import runtime_settings
from fleet.storage import worker_heartbeat


def ensure_topic(bootstrap):
    client = AdminClient({"bootstrap.servers": bootstrap})
    future = client.create_topics([NewTopic(TOPIC, num_partitions=3, replication_factor=1)],
                                  operation_timeout=10, request_timeout=15)[TOPIC]
    try:
        future.result()
    except KafkaException as error:
        if error.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise


def publish_to(producer, key, value):
    delivery = []
    producer.produce(TOPIC, key=key, value=value, callback=lambda error, message: delivery.append(error))
    if producer.flush(5) or delivery != [None]:
        raise RuntimeError("Broker did not acknowledge the observation.")


def new_consumer(bootstrap, group):
    consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": group, "enable.auto.commit": False,
                         "enable.auto.offset.store": False, "auto.offset.reset": "earliest"})
    consumer.subscribe([TOPIC])
    return consumer


def consume_one(db, consumer, timeout=1, before_commit=None):
    message = consumer.poll(timeout)
    if message is None:
        return False
    if message.error():
        raise KafkaException(message.error())
    project_message(db, message.key(), message.value(), (message.topic(), message.partition(), message.offset()))
    if before_commit:
        before_commit()
    offsets = consumer.commit(message=message, asynchronous=False)
    for offset in offsets:
        if offset.error:
            raise KafkaException(offset.error)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job", choices=("relay", "project"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = runtime_settings(os.environ)
    bootstrap = os.environ["FLEET_KAFKA_BOOTSTRAP"]
    ensure_topic(bootstrap)
    if args.job == "relay":
        producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True, "message.timeout.ms": 3000})
        while True:
            try:
                with psycopg.connect(settings.database_url, row_factory=dict_row) as db:
                    changed = relay_one(db, lambda key, value: publish_to(producer, key, value))
                    worker_heartbeat(db, "observation-relay")
            except RuntimeError:
                if args.once:
                    raise
                changed = False
            if args.once:
                return
            if not changed:
                time.sleep(0.5)
    else:
        consumer = new_consumer(bootstrap, "fleet-observation-projector-v1")
        try:
            while True:
                with psycopg.connect(settings.database_url, row_factory=dict_row) as db:
                    consumed = consume_one(db, consumer, timeout=10 if args.once else 5)
                    worker_heartbeat(db, "observation-projector")
                if args.once:
                    if not consumed:
                        raise RuntimeError("No observation arrived before the poll deadline.")
                    return
        finally:
            consumer.close()


if __name__ == "__main__":
    main()
