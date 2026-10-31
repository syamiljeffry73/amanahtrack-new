#!/usr/bin/env python3
"""
AmanahTrack box client.

Runs on the Raspberry Pi inside the donation box. It watches a load cell under
the collection tray, detects each drop, works out what coins most likely
caused the weight change, and posts a signed event to the platform.

Honest limits, built into the design:

  * Weight identifies coins, not notes. A note weighs under a gram, which a
    cheap load cell cannot resolve. Notes are not valued here.
  * Some coins weigh the same. The old series 20 sen and the new series 50 sen
    are both 5.66 g. The estimator does not guess: it reports a confidence
    below 1.0 and the value it posts is the probability weighted average.
  * Every posted value is PROVISIONAL on the server. Only a witnessed count
    turns it into a balance.

Modes:
  python box_client.py --simulate          no hardware, fake coin drops
  python box_client.py --simulate --once   one scripted run, then exit (testing)
  python box_client.py --calibrate         real HX711, prints the scale factor
  python box_client.py                     real HX711, runs for real

Only the standard library is needed, except `lgpio` for the real HX711 reader.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import itertools
import json
import math
import os
import random
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from collections import deque
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

HERE = Path(__file__).resolve().parent

API_URL = os.getenv("AMANAH_API_URL", "http://localhost:8000")
SERIAL = os.getenv("AMANAH_DEVICE_SERIAL", "BOX-002")
SECRET = os.getenv("AMANAH_DEVICE_SECRET", "demo-secret-key-change-me")
QUEUE_FILE = Path(os.getenv("AMANAH_QUEUE_FILE", HERE / "queue.jsonl"))
# "auto" uses the Pi camera if rpicam-still exists, "off" skips photos,
# "fake" produces a synthetic frame (used by --simulate).
CAMERA = os.getenv("AMANAH_CAMERA", "auto")

# Detection tuning. All in grams.
DROP_THRESHOLD_G = 0.8     # a step bigger than this starts an event
SETTLE_STDEV_G = 0.06      # tray counts as settled when readings vary less
SETTLE_WINDOW = 6          # readings in the settle check
MIN_COIN_DELTA_G = 1.0     # smaller than the lightest coin: ignore as noise
EMPTIED_DELTA_G = -20.0    # a drop this big means the tray was emptied
SAMPLE_HZ = 10

# Estimator tuning.
SIGMA_READ_G = 0.05        # load cell noise after settling
SIGMA_COIN_G = 0.04        # coin to coin mass variation (wear, plating)
MAX_COINS_PER_EVENT = 5
COIN_COUNT_PRIOR = 0.4     # each extra coin in one event is less likely


# ----------------------------------------------------------------- coin table


def load_coin_table(path: Path = HERE / "coins.json") -> dict[str, tuple[float, int]]:
    """name -> (grams, value in sen). Measure your own coins and edit the file."""
    raw = json.loads(path.read_text())
    table = {}
    for name, spec in raw["coins"].items():
        table[name] = (float(spec["grams"]), int(spec["sen"]))
    return table


# ------------------------------------------------------------------ estimator


def explain_weight(delta_g: float, table: dict[str, tuple[float, int]]):
    """Work out what coins most plausibly produced this weight change.

    Returns (expected_value_sen, confidence, best_description) or None when
    nothing in the table explains the weight.

    Every combination of up to MAX_COINS_PER_EVENT coins is scored by how well
    its total mass matches, with a prior that favours fewer coins. Scores are
    grouped by total value. If two groups tie (the 5.66 g collision), the
    confidence drops to about 0.5 and the expected value sits between them.
    """

    names = list(table)
    mass_by_value: dict[int, float] = {}
    best = (0.0, "", 0)

    for n in range(1, MAX_COINS_PER_EVENT + 1):
        sigma = math.sqrt(SIGMA_READ_G**2 + n * SIGMA_COIN_G**2)
        prior = COIN_COUNT_PRIOR ** (n - 1)
        for combo in itertools.combinations_with_replacement(names, n):
            mass = sum(table[c][0] for c in combo)
            err = delta_g - mass
            if abs(err) > 4 * sigma:
                continue
            score = prior * math.exp(-0.5 * (err / sigma) ** 2)
            value = sum(table[c][1] for c in combo)
            mass_by_value[value] = mass_by_value.get(value, 0.0) + score
            if score > best[0]:
                best = (score, "+".join(combo), value)

    total = sum(mass_by_value.values())
    if total <= 0:
        return None

    expected = sum(v * m for v, m in mass_by_value.items()) / total
    confidence = max(mass_by_value.values()) / total
    return expected, confidence, best[1]


def sen_to_ringgit(sen: float) -> str:
    return str((Decimal(str(sen)) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP))


# -------------------------------------------------------------------- signing


def sign(serial: str, event_type: str, amount: str | None, secret: str) -> str:
    """Must match the server byte for byte.

    The server signs f"{serial}:{event_type}:{amount or ''}" where amount is
    the str() of a Decimal. Send the amount as a two decimal string and sign
    that same string, so both sides agree on the text.
    """
    message = f"{serial}:{event_type}:{amount or ''}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def sign_alert(serial: str, kind: str, photo_sha256: str | None, secret: str) -> str:
    """Alerts sign the SHA-256 of the photo, which binds the picture to this box."""
    message = f"{serial}:alert:{kind}:{photo_sha256 or ''}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


# --------------------------------------------------------------------- camera


def _fake_frame() -> bytes:
    """A tiny synthetic PNG so the whole alert path can be tested with no camera."""
    w, h = 320, 240
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            v = 28 + y // 8
            if 60 <= x < 260 and 80 <= y < 200:
                v = 70
            row += bytes((v, v + 8, v + 5))
        rows.append(bytes(row))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + chunk(b"IEND", b"")
    )


def capture_photo() -> bytes | None:
    """Take a still from the camera. Returns JPEG/PNG bytes, or None.

    Never raises: a broken camera must not stop an alert from being sent. The
    alert goes out without a photo and the committee sees that it has none.
    """
    if CAMERA == "off":
        return None
    if CAMERA == "fake":
        return _fake_frame()
    for tool in ("rpicam-still", "libcamera-still"):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "frame.jpg"
                subprocess.run(
                    [tool, "-n", "-t", "500", "--width", "1024", "--height", "768",
                     "-q", "80", "-o", str(out)],
                    check=True, capture_output=True, timeout=15,
                )
                data = out.read_bytes()
                if data:
                    return data
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            continue
    print("  camera unavailable, sending alert without a photo")
    return None


def make_alert(kind: str, detail: str) -> dict:
    photo = capture_photo()
    event = {"event_type": "alert", "kind": kind, "detail": detail}
    if photo:
        event["photo_b64"] = base64.b64encode(photo).decode()
        event["photo_sha256"] = hashlib.sha256(photo).hexdigest()
    return event


# --------------------------------------------------------------------- posting


class PostResult:
    OK = "ok"
    RETRY = "retry"     # network trouble or server error: keep and try later
    REJECTED = "rejected"  # server said no: retrying will not help


def post_event(event: dict) -> str:
    if event["event_type"] == "alert":
        url = f"{API_URL}/devices/alerts"
        body = {
            "serial": SERIAL,
            "kind": event["kind"],
            "detail": event.get("detail", ""),
            "photo_b64": event.get("photo_b64"),
            "photo_sha256": event.get("photo_sha256"),
        }
        signature = sign_alert(SERIAL, event["kind"], event.get("photo_sha256"), SECRET)
    elif event["event_type"] == "telemetry":
        url = f"{API_URL}/devices/telemetry"
        body = {
            "serial": SERIAL,
            "kind": event["kind"],
            "detail": event.get("detail", ""),
            "pulses": event.get("pulses"),
            "photo_b64": event.get("photo_b64"),
            "photo_sha256": event.get("photo_sha256"),
            "occurred_at": event.get("occurred_at"),
        }
        signature = sign_alert(SERIAL, event["kind"], event.get("photo_sha256"), SECRET)
    else:
        amount = event.get("estimated_amount")
        url = f"{API_URL}/devices/events"
        body = {
            "serial": SERIAL,
            "event_type": event["event_type"],
            "estimated_amount": amount,
            "confidence": event.get("confidence"),
        }
        signature = sign(SERIAL, event["event_type"], amount, SECRET)
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Device-Signature": signature},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as resp:
            return PostResult.OK if 200 <= resp.status < 300 else PostResult.RETRY
    except urllib.error.HTTPError as err:
        if err.code >= 500:
            return PostResult.RETRY
        print(f"  server rejected event ({err.code}): {err.read().decode()[:160]}")
        return PostResult.REJECTED
    except (urllib.error.URLError, TimeoutError, OSError):
        return PostResult.RETRY


def queue_event(event: dict) -> None:
    with QUEUE_FILE.open("a") as fh:
        fh.write(json.dumps(event) + "\n")


def flush_queue() -> int:
    """Send anything saved while offline. Returns how many are still waiting."""
    if not QUEUE_FILE.exists():
        return 0
    pending = [json.loads(l) for l in QUEUE_FILE.read_text().splitlines() if l.strip()]
    kept = []
    for i, event in enumerate(pending):
        outcome = post_event(event)
        if outcome == PostResult.RETRY:
            kept = pending[i:]
            break
    if kept:
        QUEUE_FILE.write_text("".join(json.dumps(e) + "\n" for e in kept))
    else:
        QUEUE_FILE.unlink(missing_ok=True)
    return len(kept)


def deliver(event: dict) -> bool:
    """Send an event, or queue it. Returns True if the server took it now."""
    if flush_queue() == 0 and post_event(event) in (PostResult.OK, PostResult.REJECTED):
        return True
    queue_event(event)
    print("  offline, saved to queue")
    return False


# ------------------------------------------------------------------ the scales


class SimulatedScale:
    """A fake tray. Feed it coin names and it produces a realistic signal."""

    def __init__(self, table, script):
        self.table = table
        self.script = deque(script)
        self.weight = 0.0
        self.target = 0.0
        self.ring = 0.0
        self.idle = 8

    def read(self) -> float:
        if self.idle > 0:
            self.idle -= 1
        elif self.script:
            item = self.script.popleft()
            if item == "EMPTY":
                self.target = 0.0
            elif item.startswith("+") and item.endswith("g"):
                self.target += float(item[1:-1])      # raw grams, e.g. a folded note
                self.ring = 1.0
            else:
                self.target += sum(self.table[c][0] for c in item.split("+"))
                self.ring = 2.5      # the impulse of a coin landing
            self.idle = 14
        self.ring *= 0.55
        self.weight += (self.target - self.weight) * 0.5
        return self.weight + self.ring * random.choice((-1, 1)) + random.gauss(0, 0.02)

    def exhausted(self) -> bool:
        return not self.script and self.idle == 0


class HX711Scale:
    """HX711 read directly from the Pi header with lgpio.

    NOT TESTED ON HARDWARE. Linux is not a real time system and the HX711
    drops into power down if its clock pin stays high for 60 microseconds, so
    some readings will be corrupt. This class rejects the obvious bad ones and
    takes a median of several reads. If readings are still unstable, read the
    HX711 with an ESP32 or Pico instead and post from there. See HARDWARE.md.
    """

    def __init__(self, dout: int, sck: int, scale: float, offset: float, chip: int | None = None):
        import lgpio  # imported here so --simulate needs nothing installed

        self.lgpio = lgpio
        self.chip = None
        for candidate in ([chip] if chip is not None else [4, 0]):
            try:
                self.chip = lgpio.gpiochip_open(candidate)
                break
            except Exception:
                continue
        if self.chip is None:
            raise RuntimeError("Could not open a GPIO chip. Is lgpio installed?")
        self.dout, self.sck = dout, sck
        lgpio.gpio_claim_input(self.chip, dout)
        lgpio.gpio_claim_output(self.chip, sck, 0)
        self.scale, self.offset = scale, offset

    def _raw(self) -> int | None:
        lg, chip = self.lgpio, self.chip
        deadline = time.time() + 1.0
        while lg.gpio_read(chip, self.dout) == 1:
            if time.time() > deadline:
                return None
            time.sleep(0.001)
        value = 0
        for _ in range(24):
            lg.gpio_write(chip, self.sck, 1)
            lg.gpio_write(chip, self.sck, 0)
            value = (value << 1) | lg.gpio_read(chip, self.dout)
        lg.gpio_write(chip, self.sck, 1)   # 25th pulse: channel A, gain 128
        lg.gpio_write(chip, self.sck, 0)
        if value in (0x7FFFFF, 0x800000, 0xFFFFFF):
            return None
        return value - (1 << 24) if value & 0x800000 else value

    def raw_median(self, n: int = 5) -> float:
        samples = [r for r in (self._raw() for _ in range(n + 4)) if r is not None]
        if len(samples) < 3:
            raise RuntimeError("HX711 returned no valid readings. Check wiring.")
        return statistics.median(samples)

    def read(self) -> float:
        return (self.raw_median() - self.offset) / self.scale

    def exhausted(self) -> bool:
        return False


# ------------------------------------------------------------------ detection


def watch(scale, table, once: bool = False) -> None:
    window: deque[float] = deque(maxlen=SETTLE_WINDOW)
    baseline = None
    pending = False
    print(f"watching tray as {SERIAL}, posting to {API_URL}")

    while True:
        w = scale.read()
        window.append(w)
        if len(window) < SETTLE_WINDOW:
            time.sleep(1 / SAMPLE_HZ if not once else 0)
            continue

        settled = statistics.pstdev(window) < SETTLE_STDEV_G
        level = statistics.fmean(window)

        if baseline is None and settled:
            baseline = level
            print(f"baseline {baseline:.2f} g")

        elif baseline is not None:
            if not pending and abs(w - baseline) > DROP_THRESHOLD_G:
                pending = True
            elif pending and settled:
                delta = level - baseline
                handle_change(delta, table)
                baseline = level
                pending = False

        if once and scale.exhausted() and not pending:
            return
        if not once:
            time.sleep(1 / SAMPLE_HZ)


def handle_change(delta: float, table) -> None:
    if delta <= EMPTIED_DELTA_G:
        print(f"tray emptied ({delta:.1f} g), count session expected")
        deliver(make_alert("tray_emptied", f"Weight dropped by {abs(delta):.1f} g."))
        return
    if delta < MIN_COIN_DELTA_G:
        return

    result = explain_weight(delta, table)
    if result is None:
        print(f"+{delta:.2f} g: no coin combination fits, probably a note or foreign object")
        deliver(
            make_alert(
                "unexplained_weight",
                f"+{delta:.2f} g does not match any coin combination. "
                "Could be a note or a foreign object.",
            )
        )
        return

    sen, confidence, label = result
    amount = sen_to_ringgit(sen)
    print(f"+{delta:.2f} g -> RM {amount}  (confidence {confidence:.2f}, best guess {label})")
    deliver(
        {
            "event_type": "insertion",
            "estimated_amount": amount,
            "confidence": f"{confidence:.3f}",
        }
    )


# ------------------------------------------------------------------- calibrate


def calibrate(args) -> None:
    scale = HX711Scale(args.dout, args.sck, 1.0, 0.0)
    input("Tray empty and still. Press Enter to read the zero point. ")
    offset = scale.raw_median(15)
    print(f"offset = {offset:.1f}")
    known = float(input("Place a known weight and type its grams (a 50 sen new series is 5.66): "))
    loaded = scale.raw_median(15)
    factor = (loaded - offset) / known
    print(f"scale  = {factor:.3f}")
    print("\nSet these in the environment before running:")
    print(f"  AMANAH_HX711_OFFSET={offset:.1f}")
    print(f"  AMANAH_HX711_SCALE={factor:.3f}")
    print("Then check with a different known weight. If it is off by more than")
    print("0.1 g, the load cell is not mounted rigidly enough.")


# ------------------------------------------------------------------------ main


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--once", action="store_true", help="scripted run then exit")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--dout", type=int, default=int(os.getenv("AMANAH_HX711_DOUT", 5)))
    parser.add_argument("--sck", type=int, default=int(os.getenv("AMANAH_HX711_SCK", 6)))
    args = parser.parse_args()

    if args.calibrate:
        calibrate(args)
        return 0

    table = load_coin_table()

    global CAMERA
    if args.simulate and CAMERA == "auto":
        CAMERA = "fake"

    if args.simulate:
        script = (
            ["10sen_new", "50sen_new", "20sen_old+10sen_new", "5sen_old", "+1.1g", "50sen_old", "EMPTY"]
            if args.once
            else [random.choice(list(table)) for _ in range(40)]
        )
        scale = SimulatedScale(table, script)
    else:
        scale = HX711Scale(
            args.dout,
            args.sck,
            float(os.getenv("AMANAH_HX711_SCALE", "1")),
            float(os.getenv("AMANAH_HX711_OFFSET", "0")),
        )

    try:
        watch(scale, table, once=args.once)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
