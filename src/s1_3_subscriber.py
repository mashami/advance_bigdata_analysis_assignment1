"""Section 1.3: MQTT subscriber -> energy_live.

Start this BEFORE the publisher:
    python src/s1_3_subscriber.py --truncate
Subscribes to energy/meters/# at QoS 1, parses each JSON message and inserts its
eight fields into energy_live. Stops after --idle seconds without messages (once
at least one message has arrived) and prints its counts.

Fault found in the first pilot run (kept for the report):
  version 1 inserted and committed each row inside on_message. That blocks the
  MQTT network loop, so the subscriber drained ~200 msg/s while the publisher
  sent 2,000 in ~1.4 s. EMQX's per-session queue (mqtt.max_mqueue_len = 1000)
  overflowed and the broker discarded 792 messages
  (`emqx ctl broker metrics` -> delivery.dropped.queue_full = 792).
Fix (version 2, this file): on_message only parses and hands the row to a
  queue; a separate writer thread inserts in batches and commits. Use
  --mode per-message to reproduce the original fault.
"""
import argparse
import json
import logging
import os
import queue
import threading
import time

import paho.mqtt.client as mqtt

from common import LOGS, RESULTS, cat_text, connect

FIELDS = ("meter_id", "event_time", "received_time", "power_kw", "voltage_v",
          "current_a", "frequency_hz", "energy_kwh")
INSERT = f"INSERT INTO energy_live ({', '.join(FIELDS)}) VALUES ({', '.join(['%s'] * 8)})"
TOPIC = "energy/meters/#"
BATCH = 200

log = logging.getLogger("subscriber")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truncate", action="store_true", help="empty energy_live first")
    parser.add_argument("--idle", type=float, default=15.0, help="stop after N idle seconds")
    parser.add_argument("--mode", choices=["batched", "per-message"], default="batched",
                        help="per-message reproduces the original (faulty) design")
    args = parser.parse_args()

    logging.basicConfig(filename=LOGS / "subscriber.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    db = connect()
    if args.truncate:
        db.execute("TRUNCATE energy_live")
        db.commit()
    start_rows = db.execute("SELECT count(*) FROM energy_live").fetchone()[0]
    db.commit()

    stats = {"received": 0, "redelivered_dup_flag": 0, "parse_errors": 0,
             "insert_errors": 0, "inserted_committed": 0}
    last_message = [None]
    subscribed = threading.Event()
    rows: queue.Queue = queue.Queue()

    def write(batch: list) -> None:
        try:
            with db.cursor() as cur:
                cur.executemany(INSERT, batch)
            db.commit()
            stats["inserted_committed"] += len(batch)
        except Exception as exc:
            db.rollback()
            stats["insert_errors"] += len(batch)
            log.error("insert of %d rows failed: %s", len(batch), exc)

    def writer() -> None:
        """Drain the queue; commit every BATCH rows or when the queue goes quiet."""
        batch = []
        while True:
            try:
                item = rows.get(timeout=0.2)
            except queue.Empty:
                item = None
            if item is not None and item is not STOP:
                batch.append(item)
            if batch and (len(batch) >= BATCH or item is None or item is STOP):
                write(batch)
                batch = []
            if item is STOP:
                return

    STOP = object()

    def on_connect(client, userdata, flags, reason_code, properties):
        log.info("connected rc=%s session_present=%s", reason_code, flags.session_present)
        client.subscribe(TOPIC, qos=1)

    def on_subscribe(client, userdata, mid, reason_codes, properties):
        log.info("subscribed %s granted=%s", TOPIC, reason_codes)
        subscribed.set()

    def on_message(client, userdata, msg):
        stats["received"] += 1
        last_message[0] = time.monotonic()
        if msg.dup:
            stats["redelivered_dup_flag"] += 1
            log.warning("DUP flag set (broker redelivery) topic=%s mid=%s", msg.topic, msg.mid)
        try:
            record = json.loads(msg.payload)
            values = tuple(record[f] for f in FIELDS)
            if msg.topic != f"energy/meters/{record['meter_id']}":
                raise ValueError(f"topic {msg.topic} does not match meter_id")
        except Exception as exc:
            stats["parse_errors"] += 1
            log.error("parse error topic=%s payload=%r error=%s", msg.topic, msg.payload[:200], exc)
            return
        if args.mode == "per-message":
            write([values])
        else:
            rows.put(values)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="auca-energy-subscriber",
                         clean_session=False)  # persistent session keeps QoS 1 messages queued
    client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
    client.connect(os.getenv("MQTT_HOST", "localhost"), int(os.getenv("MQTT_PORT", "1883")))
    client.loop_start()
    subscribed.wait(10)
    print(f"[{cat_text()}] subscriber ready on {TOPIC} (QoS 1, mode={args.mode}); "
          f"energy_live rows = {start_rows}")

    reported = 0
    try:
        while True:
            time.sleep(1)
            if stats["received"] - reported >= 500:
                reported = stats["received"]
                print(f"  received {reported:,}")
            if last_message[0] and time.monotonic() - last_message[0] > args.idle:
                break
    except KeyboardInterrupt:
        pass
    client.loop_stop()
    client.disconnect()
    rows.put(STOP)
    thread.join()

    stats["energy_live_rows"] = db.execute("SELECT count(*) FROM energy_live").fetchone()[0]
    stats["mode"] = args.mode
    stats["finished_cat"] = cat_text()
    db.close()
    (RESULTS / "pilot_subscriber.json").write_text(json.dumps(stats, indent=2))
    print(f"[{stats['finished_cat']}] subscriber finished")
    for key, value in stats.items():
        print(f"  {key:22s}: {value}")


if __name__ == "__main__":
    main()
