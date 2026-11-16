import argparse
import os
import time

import psycopg
from confluent_kafka import Consumer, KafkaError, KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from psycopg.rows import dict_row

from fleet.observations import SOURCE_TOPICS, TOPIC, project_message, relay_batch, synthetic_stream_scope
from fleet.settings import runtime_settings
from fleet.storage import worker_heartbeat


def ensure_topic(bootstrap, topic=TOPIC):
    client = AdminClient({"bootstrap.servers": bootstrap})
    future = client.create_topics([NewTopic(topic, num_partitions=3, replication_factor=1)],
                                  operation_timeout=10, request_timeout=15)[topic]
    try:
        future.result()
    except KafkaException as error:
        if error.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise


def publish_batch(producer, messages, topic=TOPIC):
    delivery = []
    for key, value in messages:
        producer.produce(topic, key=key, value=value, callback=lambda error, message: delivery.append(error))
    if producer.flush(5) or delivery != [None] * len(messages):
        raise RuntimeError("Broker did not acknowledge the observation batch.")


def publish_to(producer, key, value, topic=TOPIC):
    publish_batch(producer, [(key, value)], topic)


def new_consumer(bootstrap, group, topics=(TOPIC,)):
    consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": group, "enable.auto.commit": False,
                         "enable.auto.offset.store": False, "auto.offset.reset": "earliest"})
    consumer.subscribe(list(topics))
    return consumer


def consume_one(db, consumer, timeout=1, before_commit=None, *, source_scope=None):
    message = consumer.poll(timeout)
    if message is None:
        return False
    if message.error():
        raise KafkaException(message.error())
    project_message(db, message.key(), message.value(), (message.topic(), message.partition(), message.offset()),
                    source_scope=source_scope)
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
    with psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row) as db:
        if args.job == "relay":
            producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True,
                                 "message.timeout.ms": 3000})
            while True:
                try:
                    changed = relay_batch(db, lambda messages: publish_batch(producer, messages),
                                          limit=1 if args.once else 32)
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
            scope = synthetic_stream_scope(os.environ)
            topics = (TOPIC, *SOURCE_TOPICS) if scope else (TOPIC,)
            for topic in topics[1:]:
                ensure_topic(bootstrap, topic)
            consumer = new_consumer(bootstrap, "fleet-observation-projector-v1", topics)
            try:
                while True:
                    consumed = consume_one(db, consumer, timeout=10 if args.once else 5, source_scope=scope)
                    worker_heartbeat(db, "observation-projector")
                    if args.once:
                        if not consumed:
                            raise RuntimeError("No observation arrived before the poll deadline.")
                        return
            finally:
                consumer.close()


if __name__ == "__main__":
    main()
