"""Kafka output port: the outbox in Snowflake is sent as CloudEvents; a row is marked sent only on delivery."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

from dpcp.warehouse import Warehouse


class Producer(Protocol):
    def send(self, topic: str, key: str, value: bytes) -> bool: ...


class MemoryProducer:
    def __init__(self, fail_every: int = 0):
        self.topics: dict[str, list[tuple[str, dict]]] = {}
        self.log: list[tuple[str, dict]] = []  # every delivered event in send order, across topics
        self.fail_every = fail_every
        self.attempts = 0

    def send(self, topic: str, key: str, value: bytes) -> bool:
        self.attempts += 1
        if self.fail_every and self.attempts % self.fail_every == 0:
            return False
        event = json.loads(value)
        self.topics.setdefault(topic, []).append((key, event))
        self.log.append((topic, event))
        return True


class KafkaProducer:
    """confluent-kafka with idempotence; delivery is confirmed by flush() and the delivery report."""

    def __init__(self, config: dict):
        from confluent_kafka import Producer as _Producer

        self._p = _Producer({"enable.idempotence": True, "acks": "all", **config})

    def send(self, topic: str, key: str, value: bytes) -> bool:
        result: dict = {}
        self._p.produce(topic, key=key, value=value, on_delivery=lambda err, _m: result.update(err=err))
        self._p.flush(10)
        return "err" in result and result["err"] is None


class OutboxPublisher:
    def __init__(self, wh: Warehouse, producer: Producer, majors: dict[str, int]):
        self.wh = wh
        self.producer = producer
        self.majors = majors
        self.table = f"{wh.schema('platform_schema')}.OUTBOX"

    def publish(self, now: datetime) -> int:
        sent = 0
        for row in self.wh.rows(f"select * from {self.table} where SENT_AT is null order by CREATED_AT, EVENT_ID"):
            payload = json.loads(row["payload"])
            event = {
                "specversion": "1.0",
                "id": row["event_id"],
                "source": "data-product-control-plane",
                "type": f"dataproduct.{row['kind']}",
                "subject": row["product"],
                "time": row["created_at"].isoformat() + "Z",
                "datacontenttype": "application/json",
                "data": payload,
            }
            topic = f"dataproduct.{row['product']}.v{self.majors.get(row['product'], 1)}"
            if not self.producer.send(topic, row["product"], json.dumps(event).encode()):
                break  # keep order: try again on the next run
            self.wh.run(f"update {self.table} set SENT_AT = %s where EVENT_ID = %s", (now, row["event_id"]))
            sent += 1
        return sent
