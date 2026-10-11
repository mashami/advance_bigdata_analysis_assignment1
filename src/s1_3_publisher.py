"""Section 1.3: publish the first 2,000 generator readings over MQTT.

    python src/s1_3_publisher.py          (start the subscriber first)
Each dictionary becomes one JSON message on energy/meters/{meter_id},
QoS 1, retain=False. A message counts as acknowledged when the broker's
PUBACK triggers on_publish.
"""
import argparse
import itertools
import json
import logging
import os
import threading
import time

import paho.mqtt.client as mqtt

from common import LOGS, RESULTS, cat_text
import smart_meter_simulator as sim

log = logging.getLogger("publisher")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=2000)
    args = parser.parse_args()
    logging.basicConfig(filename=LOGS / "publisher.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    acked = set()
    lock = threading.Lock()

    def on_publish(client, userdata, mid, reason_code, properties):
        with lock:
            acked.add(mid)  # QoS 1: called when the broker's PUBACK arrives

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="auca-energy-publisher")
    client.on_publish = on_publish
    client.max_queued_messages_set(0)  # unlimited local queue, nothing dropped
    client.connect(os.getenv("MQTT_HOST", "localhost"), int(os.getenv("MQTT_PORT", "1883")))
    client.loop_start()

    started = cat_text()
    taken = published = failed = 0
    sent_mids = []
    for record in itertools.islice(sim.iter_readings(), args.count):
        taken += 1
        topic = f"energy/meters/{record['meter_id']}"
        info = client.publish(topic, json.dumps(record), qos=1, retain=False)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            published += 1
            sent_mids.append(info.mid)
        else:
            failed += 1
            log.error("publish failed rc=%s topic=%s", info.rc, topic)

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with lock:
            if len(acked) >= len(sent_mids):
                break
        time.sleep(0.1)
    client.loop_stop()
    client.disconnect()

    missing = sorted(set(sent_mids) - acked)
    if missing:
        log.error("%d messages without PUBACK, mids=%s", len(missing), missing[:50])
    result = {"started_cat": started, "finished_cat": cat_text(),
              "taken_from_generator": taken, "publish_calls_ok": published,
              "publish_failed": failed, "acknowledged_by_broker": len(acked)}
    (RESULTS / "pilot_publisher.json").write_text(json.dumps(result, indent=2))
    print(f"[{result['finished_cat']}] publisher finished")
    for key, value in result.items():
        print(f"  {key:24s}: {value}")


if __name__ == "__main__":
    main()
