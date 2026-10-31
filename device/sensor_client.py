#!/usr/bin/env python3
"""
AmanahTrack sensor client: door switch, vibration sensor, camera, heartbeat.

Runs on the Raspberry Pi 5 inside the donation box and replaces the old
Telegram script. It watches the sensors, takes a photo when something happens,
and posts signed events to the AmanahTrack backend, which pushes them to the
committee's app in real time.

It reuses the signing, camera and offline queue from box_client.py, so keep
both files in the same folder.

Modes:
  python sensor_client.py --simulate    no hardware, a scripted door and
                                        vibration sequence (to test the app)
  python sensor_client.py               real GPIO

Wiring and settings (environment variables, all optional):
  AMANAH_REED_PIN          BCM pin of the door reed switch      (default 17)
  AMANAH_VIB_PIN           BCM pin of the vibration sensor      (default 27)
  AMANAH_REED_OPEN_LEVEL   pin level that means "door open"     (default 1)
  AMANAH_VIB_ACTIVE_LEVEL  pin level while vibration is seen    (default 1)
  AMANAH_VIB_MIN_PULSES    pulses in the window to count it     (default 3)
  AMANAH_CAMERA            auto | off | fake                    (default auto)
  AMANAH_BUZZER            on | off                             (default on)
  AMANAH_BUZZER_PIN        BCM pin of the buzzer                (default 22)
  AMANAH_BUZZER_SECONDS    how long it sounds                   (default 3)
  AMANAH_BUZZER_VOLUME     PWM duty 1 to 100                    (default 50)

Use the SAME pins as your old Telegram script. The defaults above are only a
guess. The reed level depends on how you wired it, so check with --test-pins.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
import threading
import time
from datetime import datetime, timezone

import box_client as bc

REED_PIN = int(os.getenv("AMANAH_REED_PIN", 17))
VIB_PIN = int(os.getenv("AMANAH_VIB_PIN", 27))
REED_OPEN_LEVEL = int(os.getenv("AMANAH_REED_OPEN_LEVEL", 1))
VIB_ACTIVE_LEVEL = int(os.getenv("AMANAH_VIB_ACTIVE_LEVEL", 1))
VIB_MIN_PULSES = int(os.getenv("AMANAH_VIB_MIN_PULSES", 3))

BUZZER_ON = os.getenv("AMANAH_BUZZER", "on").lower() != "off"
BUZZER_PIN = int(os.getenv("AMANAH_BUZZER_PIN", 22))
BUZZER_SECONDS = float(os.getenv("AMANAH_BUZZER_SECONDS", 3))
BUZZER_VOLUME = float(os.getenv("AMANAH_BUZZER_VOLUME", 50))

POLL_HZ = 200              # how often the pins are read
DOOR_DEBOUNCE_S = 0.05     # reed contacts bounce; the level must hold this long
VIB_WINDOW_S = 2.0         # pulses are counted over this window
VIB_COOLDOWN_S = 10.0      # after reporting, stay quiet this long
HEARTBEAT_S = 30.0

_send_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def report(kind: str, *, detail: str = "", pulses: int | None = None, photo: bool = False) -> None:
    """Build an event (with a photo if asked) and deliver it, off the main thread.

    The camera takes about a second. Doing that on the sensing loop would make
    the box deaf while it photographs, so every report runs in its own thread.
    """

    def work() -> None:
        event = {
            "event_type": "telemetry",
            "kind": kind,
            "detail": detail,
            "pulses": pulses,
            "occurred_at": now_iso(),
        }
        if photo:
            data = bc.capture_photo()
            if data:
                event["photo_b64"] = base64.b64encode(data).decode()
                event["photo_sha256"] = hashlib.sha256(data).hexdigest()
        with _send_lock:  # the queue file must not be written by two threads
            delivered = bc.deliver(event)
        what = f"{kind}{' with photo' if 'photo_b64' in event else ''}"
        print(f"  sent {what}" if delivered else f"  queued {what} (will send when the server is reachable)")

    threading.Thread(target=work, daemon=True).start()


# ----------------------------------------------------------------- the pins


class Pins:
    """Reads the reed switch and the vibration sensor with lgpio."""

    def __init__(self) -> None:
        import lgpio  # imported here so --simulate needs nothing installed

        self.lg = lgpio
        self.chip = None
        for candidate in (4, 0):  # the Pi 5 header is on gpiochip4
            try:
                self.chip = lgpio.gpiochip_open(candidate)
                break
            except Exception:
                continue
        if self.chip is None:
            raise RuntimeError("Could not open a GPIO chip. Is lgpio installed?")
        # Pull-ups so an unconnected wire reads as a stable 1, not noise.
        lgpio.gpio_claim_input(self.chip, REED_PIN, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_input(self.chip, VIB_PIN, lgpio.SET_PULL_UP)
        lgpio.gpio_claim_output(self.chip, BUZZER_PIN, 0)
        self._buzzing = threading.Lock()

    def sound_buzzer(self) -> None:
        """Sound the alarm for a few seconds, without blocking the sensing loop."""
        if not BUZZER_ON or not self._buzzing.acquire(blocking=False):
            return  # off, or already sounding

        def work() -> None:
            try:
                print(f"  buzzer ON for {BUZZER_SECONDS:g}s")
                self.lg.tx_pwm(self.chip, BUZZER_PIN, 100, BUZZER_VOLUME)
                time.sleep(BUZZER_SECONDS)
            finally:
                self.lg.tx_pwm(self.chip, BUZZER_PIN, 100, 0)
                self.lg.gpio_write(self.chip, BUZZER_PIN, 0)
                print("  buzzer OFF")
                self._buzzing.release()

        threading.Thread(target=work, daemon=True).start()

    def door_open(self) -> bool:
        return self.lg.gpio_read(self.chip, REED_PIN) == REED_OPEN_LEVEL

    def vibrating(self) -> bool:
        return self.lg.gpio_read(self.chip, VIB_PIN) == VIB_ACTIVE_LEVEL


class FakePins:
    """A scripted box for testing the app without any wiring."""

    def __init__(self) -> None:
        self.t0 = time.time()
        # (seconds after start, door_open, vibrating)
        self.script = [
            (3, True, False),    # door opens
            (8, True, True),     # shaking while open (counting, not an alert)
            (8.3, True, False),
            (12, False, False),  # door closes
            (16, False, True),   # vibration while closed: critical
            (16.1, False, False),
            (16.2, False, True),
            (16.3, False, False),
            (16.4, False, True),
            (16.5, False, False),
        ]

    def _state(self):
        t = time.time() - self.t0
        door, vib = False, False
        for at, d, v in self.script:
            if t >= at:
                door, vib = d, v
        return door, vib

    def sound_buzzer(self) -> None:
        if BUZZER_ON:
            print("  (simulated buzzer)")

    def door_open(self) -> bool:
        return self._state()[0]

    def vibrating(self) -> bool:
        return self._state()[1]

    def finished(self) -> bool:
        return time.time() - self.t0 > 22


# ------------------------------------------------------------------- the loop


def run(pins, once: bool = False) -> None:
    print(f"buzzer {'on, pin ' + str(BUZZER_PIN) if BUZZER_ON else 'off'}")
    print(f"sensor client as {bc.SERIAL}, posting to {bc.API_URL}")
    door = pins.door_open()
    last_change = time.time()
    raw_door = door
    print(f"door starts {'OPEN' if door else 'closed'}")
    report("door_opened" if door else "door_closed", photo=door)

    edges: list[float] = []
    prev_vib = False
    quiet_until = 0.0
    next_beat = 0.0

    while True:
        t = time.time()

        # heartbeat
        if t >= next_beat:
            report("heartbeat")
            next_beat = t + HEARTBEAT_S

        # door, debounced: only trust a level once it has held for 50 ms
        level = pins.door_open()
        if level != raw_door:
            raw_door, last_change = level, t
        elif raw_door != door and t - last_change >= DOOR_DEBOUNCE_S:
            door = raw_door
            print("door OPENED" if door else "door closed")
            if door:
                pins.sound_buzzer()
            report("door_opened" if door else "door_closed", photo=door)

        # vibration: count rising edges inside a short window
        vib = pins.vibrating()
        if vib and not prev_vib:
            edges.append(t)
        prev_vib = vib
        edges = [e for e in edges if t - e <= VIB_WINDOW_S]
        if len(edges) >= VIB_MIN_PULSES and t >= quiet_until:
            n = len(edges)
            print(f"vibration burst: {n} pulses")
            if not door:
                pins.sound_buzzer()
            report("vibration", pulses=n, photo=not door, detail=f"{n} pulses in {VIB_WINDOW_S:.0f}s")
            edges.clear()
            quiet_until = t + VIB_COOLDOWN_S

        if once and pins.finished():
            time.sleep(1.5)  # let the sender threads finish
            return
        time.sleep(1 / POLL_HZ)


def test_pins() -> None:
    """Print the raw pin levels so you can see which level means what."""
    pins = Pins()
    print(f"reed pin {REED_PIN}, vibration pin {VIB_PIN}. Ctrl+C to stop.")
    print("Open and close the door, then tap the box, and watch the numbers.")
    try:
        while True:
            r = pins.lg.gpio_read(pins.chip, REED_PIN)
            v = pins.lg.gpio_read(pins.chip, VIB_PIN)
            print(f"\rreed={r}  vibration={v}   ", end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true", help="scripted door and vibration")
    parser.add_argument("--test-pins", action="store_true", help="print raw pin levels")
    args = parser.parse_args()

    if args.test_pins:
        test_pins()
        return 0

    if args.simulate and bc.CAMERA == "auto":
        bc.CAMERA = "fake"

    pins = FakePins() if args.simulate else Pins()
    try:
        run(pins, once=args.simulate)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
