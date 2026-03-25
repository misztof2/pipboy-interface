#!/usr/bin/env python3
import time
import subprocess
import serial
from serial.serialutil import SerialException

# -------------------------
# CONFIG
# -------------------------
PHONE = "192.168.0.114:5555"
SERIAL_PORT = "/dev/ttyUSB0"
BAUDRATE = 115200

POT_MIN = 0
POT_MAX = 1023

TABS = [
    ("STAT",  390, 45),
    ("INV",   505, 45),
    ("DATA",  615, 45),
    ("MAP",   750, 45),
    ("RADIO", 875, 45),
]
ZONES = len(TABS)

# --- Make it responsive & non-iterating ---
MOVE_THRESHOLD = 5        # wykrywa, że gałka jest kręcona
STOP_DELAY_SEC = 0.10     # po tylu sekundach bez ruchu robimy 1 tap finalny

# Responsiveness
MIN_TAP_INTERVAL_SEC = 0.08   # mniejsze niż wcześniej, bo tapujemy rzadziej

# Smoothing (szybsza odpowiedź)
EMA_ALPHA = 0.85              # było 0.55 -> dawało lag; 0.8–0.9 jest “snappy”

# Ignore tiny jitter
MIN_ADC_CHANGE = 2

RECONNECT_DELAY_SEC = 0.5


# -------------------------
def adb_tap(x: int, y: int) -> None:
    subprocess.run(
        ["adb", "-s", PHONE, "shell", "input", "tap", str(x), str(y)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v

def value_to_zone(v: int) -> int:
    span = POT_MAX - POT_MIN + 1
    idx = int((v - POT_MIN) * ZONES / span)
    if idx < 0:
        return 0
    if idx >= ZONES:
        return ZONES - 1
    return idx

def open_serial():
    while True:
        try:
            ser = serial.Serial(SERIAL_PORT, BAUDRATE, timeout=0.2)
            time.sleep(1.0)
            ser.reset_input_buffer()
            print(f"[serial] connected: {SERIAL_PORT} @ {BAUDRATE}")
            return ser
        except SerialException as e:
            print(f"[serial] open failed: {e}. retrying in {RECONNECT_DELAY_SEC}s")
            time.sleep(RECONNECT_DELAY_SEC)

def _parse_first_int(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    # CSV -> take first field only
    if "," in s:
        s = s.split(",", 1)[0].strip()
    digits = "".join(ch for ch in s if ch.isdigit() or ch == "-")
    if digits in ("", "-"):
        return None
    try:
        return int(digits)
    except ValueError:
        return None

def read_int_line(ser) -> int | None:
    try:
        line = ser.readline()
        if not line:
            return None
        s = line.decode(errors="ignore").strip()
        v = _parse_first_int(s)
        if v is None:
            return None
        return clamp(v, POT_MIN, POT_MAX)
    except (SerialException, OSError) as e:
        raise SerialException(str(e))

def main() -> None:
    ser = open_serial()

    ema = None
    last_raw = None

    current_zone = None
    last_tap_time = 0.0

    moving = False
    last_move_time = 0.0
    pending_zone = None

    print("Pip-Boy 1-pot tabs controller (tap-on-stop, no intermediate tabs)")
    print(f"ADB target: {PHONE}")
    print("Ctrl+C to exit\n")

    while True:
        try:
            raw = read_int_line(ser)
            now_m = time.monotonic()

            if raw is None:
                # jeśli brak danych, nadal możemy “domknąć” stop
                if moving and (now_m - last_move_time) >= STOP_DELAY_SEC and pending_zone is not None:
                    moving = False
                    if pending_zone != current_zone and (time.time() - last_tap_time) >= MIN_TAP_INTERVAL_SEC:
                        name, x, y = TABS[pending_zone]
                        adb_tap(x, y)
                        last_tap_time = time.time()
                        current_zone = pending_zone
                        print(f"TAP -> {pending_zone} ({name}) ema={int(ema) if ema is not None else None}")
                continue

            # jitter filter (zostaje)
            if last_raw is not None and abs(raw - last_raw) < MIN_ADC_CHANGE:
                # mimo to sprawdzamy stop w czasie
                if moving and (now_m - last_move_time) >= STOP_DELAY_SEC and pending_zone is not None:
                    moving = False
                    if pending_zone != current_zone and (time.time() - last_tap_time) >= MIN_TAP_INTERVAL_SEC:
                        name, x, y = TABS[pending_zone]
                        adb_tap(x, y)
                        last_tap_time = time.time()
                        current_zone = pending_zone
                        print(f"TAP -> {pending_zone} ({name}) ema={int(ema) if ema is not None else None}")
                continue

            # detect motion (Δ>=5)
            if last_raw is not None and abs(raw - last_raw) >= MOVE_THRESHOLD:
                moving = True
                last_move_time = now_m
            last_raw = raw

            # EMA smoothing (snappy)
            if ema is None:
                ema = float(raw)
            else:
                ema = (1.0 - EMA_ALPHA) * ema + EMA_ALPHA * raw

            pending_zone = value_to_zone(int(ema))

            # tap only when user stops
            if moving and (now_m - last_move_time) >= STOP_DELAY_SEC and pending_zone is not None:
                moving = False
                if pending_zone != current_zone and (time.time() - last_tap_time) >= MIN_TAP_INTERVAL_SEC:
                    name, x, y = TABS[pending_zone]
                    adb_tap(x, y)
                    last_tap_time = time.time()
                    current_zone = pending_zone
                    print(f"TAP -> {pending_zone} ({name}) raw={raw} ema={int(ema)} tap=({x},{y})")

        except SerialException as e:
            print(f"[serial] read failed: {e}. reconnecting...")
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(RECONNECT_DELAY_SEC)
            ser = open_serial()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExit.")
