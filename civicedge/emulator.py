"""Device fleet emulator. Each emulated device is a separate MQTT client with
its own credentials and TLS session, publishing the same JSON a real
ESP32-class node would. Replace this container with hardware and nothing
else in the stack changes."""
import json, math, os, random, ssl, threading, time
import paho.mqtt.client as mqtt
from .common import env, fleet, device_password

RATE = float(env("RATE_HZ", "1"))
EVENT_EVERY = float(env("EVENT_EVERY_S", "1.0"))
EVENT_LEN = float(env("EVENT_LEN_S", "4.5"))
random.seed(int(env("SEED", "7")))

class Device:
    def __init__(self, d, i):
        self.__dict__.update(d)
        self.seq = 0; self.event_until = 0; self.last_event = 0
        self.phase = random.random() * 6.28
        self.zone_bias = 1 + 0.08 * (int(d["zone"][1:]) % 5)
        self.ar = 0.0
        self.fill = random.uniform(5, 60)
        self.burst = 0
        self.offset = (i % 50) / 50.0 / RATE   # stagger publishes

    def value(self, t):
        if t < self.event_until:
            return {"air": random.uniform(280, 420), "water": random.uniform(30, 45),
                    "bin": random.uniform(88, 99)}[self.kind]
        if self.kind == "air":      # diurnal PM2.5 with AR(1) noise, ug/m3
            self.ar = 0.9 * self.ar + random.gauss(0, 4)
            base = 70 + 45 * math.sin(2 * math.pi * (t % 86400) / 86400 + self.phase * 0.1)
            return max(3, min(240, base * self.zone_bias + self.ar))
        if self.kind == "water":    # household flow: mostly zero, short draws, L/min
            if self.burst > 0:
                self.burst -= 1; return random.uniform(4, 14)
            if random.random() < 0.03:
                self.burst = random.randint(2, 20)
            return 0.0
        self.fill = self.fill + random.uniform(0, 0.02) if self.fill < 80 else random.uniform(5, 20)
        return self.fill

def main():
    secret = env("FLEET_SECRET", "change-me")
    host, port = env("BROKER_HOST", "broker"), int(env("BROKER_PORT", "8883"))
    ca = os.path.join(env("CERT_DIR", "/certs"), "ca.crt")
    devs = [Device(d, i) for i, d in enumerate(fleet())]
    field = {"air": "pm25", "water": "flow_lpm", "bin": "fill_pct"}
    for d in devs:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=d.id)
        c.username_pw_set(d.id, device_password(secret, d.id))
        c.tls_set(ca_certs=ca, cert_reqs=ssl.CERT_REQUIRED)
        c.reconnect_delay_set(1, 5)
        while True:
            try:
                c.connect(host, port, 60); break
            except OSError:
                time.sleep(1)
        c.loop_start(); d.c = c
    print(f"emulator: {len(devs)} devices at {RATE} Hz", flush=True)

    def injector():   # ground-truth exceedance episodes
        while True:
            time.sleep(EVENT_EVERY)
            t = time.time()
            idle = [d for d in devs if t - d.last_event > 20]
            if idle:
                d = random.choice(idle); d.event_until = t + EVENT_LEN / RATE; d.last_event = t
    if EVENT_EVERY > 0:
        threading.Thread(target=injector, daemon=True).start()

    def run(d):
        time.sleep(d.offset)
        nxt = time.time()
        while True:
            t = time.time(); d.seq += 1
            msg = {"id": d.id, "kind": d.kind, "zone": d.zone, "ts": round(t, 4), "seq": d.seq,
                   "v": {field[d.kind]: round(d.value(t), 2)}}
            d.c.publish(f"ce/{d.id}/telemetry", json.dumps(msg, separators=(",", ":")), qos=0)
            nxt += 1 / RATE
            time.sleep(max(0, nxt - time.time()))
    for d in devs:
        threading.Thread(target=run, args=(d,), daemon=True).start()
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
