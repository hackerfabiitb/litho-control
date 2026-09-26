"""Home the stage axes on their limit switches.

Each axis has one limit switch; its other end is a fixed travel (12 mm) away.
Which Arduino pin each axis's switch is on, and which direction is towards
it, are found once interactively and cached in xyz_stage/homing.json. After
that, homing runs without questions.

    python xyz_stage/homing.py                # home all axes (discovers any
                                              # axis not in homing.json yet)
    python xyz_stage/homing.py --discover     # redo setup, e.g. after rewiring
    python xyz_stage/homing.py --axes Z       # only some axes

Discovery for an axis (your procedure):
  1. move it 100 steps (+). If a switch closes, that switch is this axis's
     and + is towards it;
  2. otherwise you say whether it moved towards or away from its switch (or
     repeat with a bigger move if you couldn't tell);
  3. it then seeks towards the switch; whichever pin closes is this axis's.

Homing an axis (also run at the end of discovery):
  fast seek to the switch -> back off 100 steps -> slow approach -> that
  trigger point is 0, soft limits 0..12 mm on the side away from the switch
  -> back off 50 steps so the switch isn't left pressed.

Moves use the firmware's SEEK command, which stops the instant a switch
closes. Ctrl+C aborts a move in progress.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time

from stage_link import open_link

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "homing.json")

TRAVEL_MM = 12.0
PROBE_STEPS = 100        # first discovery move
BIG_PROBE_STEPS = 400    # when the first move was too small to judge
RELEASE_CHUNK = 50       # back-off step while waiting for a switch to open
RELEASE_MAX_MM = 2.0     # a switch still closed after this is treated as stuck
BACKOFF_STEPS = 100      # beyond the release point, before the slow approach
SLOW_MAX_STEPS = 300     # slack for the slow approach on top of that distance
PARK_STEPS = 50          # left this far past the release point at the end
FAST_US = 800            # half step period: 1.6 ms/step, the UI default speed
SLOW_US = 3000           # 6 ms/step for the precise approach


class HomingError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# firmware helpers
# --------------------------------------------------------------------------- #

# Replies are checked strictly: switching the drivers used to drop characters
# from the next line (fixed in firmware with a quiet period), and a damaged
# "HIT D10" must never be read as another pin.
LS_RE = re.compile(r"^LS( D\d+=[01])+$")
POS_RE = re.compile(r"^POS (-?\d+) (-?\d+) (-?\d+)$")
SEEK_RE = re.compile(r"^SEEK ([XYZ]) (-?\d+) (HIT((?: D\d+)+)|NOHIT|ABORT)$")


def _query(link, cmd, pattern, tries=3):
    for _ in range(tries):
        link.drain()
        link.send(cmd)
        try:
            line = link.wait_for(lambda l: pattern.match(l) is not None, timeout=2)
            return pattern.match(line)
        except TimeoutError:
            continue
    raise HomingError(f"no clean reply to {cmd} after {tries} tries")


def closed_pins(link):
    """Pins whose switch is closed now, e.g. {'D10'}."""
    line = _query(link, "LS", LS_RE).group(0)
    return {item.split("=")[0] for item in line.split()[1:] if item.endswith("=0")}


def position(link, axis):
    return int(_query(link, "P", POS_RE).group("XYZ".index(axis) + 1))


def seek(link, axis, steps, delay_us=FAST_US):
    """SEEK: move up to `steps` (signed), stopping when a switch closes.
    Returns (steps moved, sorted list of pins that closed). Ctrl+C aborts.

    If the SEEK reply is damaged, the outcome is rebuilt from clean P / LS
    queries: the move is the position change, and the switch that stopped it
    is still pressed, so it shows up as newly closed."""
    start = position(link, axis)
    closed_before = closed_pins(link)
    link.drain()
    link.send(f"SEEK {axis} {int(steps)} {delay_us}")
    timeout = abs(steps) * 2 * delay_us * 1e-6 * 1.3 + 5
    reply = None
    try:
        end = time.time() + timeout
        while time.time() < end:
            try:
                line = link.wait_for(lambda l: True, timeout=max(0.1, end - time.time()))
            except TimeoutError:
                break
            m = SEEK_RE.match(line)
            if m and m.group(1) == axis:
                reply = m
            if line == "DONE":
                break
    except KeyboardInterrupt:
        link.send("!")                      # any byte stops the move
        time.sleep(0.5)
        raise
    if reply is None:
        time.sleep(0.2)
        moved = position(link, axis) - start
        hit = sorted(closed_pins(link) - closed_before)
        print(f"    (SEEK reply damaged or missing; from P/LS: moved {moved}, "
              f"hit {hit or 'nothing'})")
        return moved, hit
    if reply.group(3) == "ABORT":
        raise HomingError(f"{axis} move aborted (a command was sent to the stage "
                          "during homing)")
    return int(reply.group(2)), sorted(reply.group(4).split()) if reply.group(4) else []


def travel_steps(cfg):
    return int(round(cfg["travel_mm"] * cfg["steps_per_mm"]))


# --------------------------------------------------------------------------- #
# homing with a known configuration
# --------------------------------------------------------------------------- #

def home_axis(link, axis, cfg):
    pin, toward = cfg["pin"], cfg["toward"]
    full = travel_steps(cfg)
    print(f"  {axis}: homing on {pin}, switch is {'+' if toward > 0 else '-'}"
          f" side, travel {cfg['travel_mm']} mm = {full} steps")

    # Never seek towards a switch that is already closed: SEEK ignores
    # switches closed at the start and would drive further into it.
    if pin in closed_pins(link):
        release(link, axis, cfg)

    moved, hit = seek(link, axis, toward * (int(full * 1.2) + 200))
    _check_hit(axis, pin, hit, moved)
    hysteresis = release(link, axis, cfg)
    seek(link, axis, -toward * BACKOFF_STEPS)
    moved, hit = seek(link, axis, toward * (hysteresis + BACKOFF_STEPS + SLOW_MAX_STEPS),
                      SLOW_US)
    _check_hit(axis, pin, hit, moved)

    lo, hi = (0, full) if toward < 0 else (-full, 0)
    link.drain()
    link.send(f"HOMED {axis} {lo} {hi}")
    link.wait_for(lambda l: l == f"HOMED {axis}", timeout=5)
    release(link, axis, cfg)
    seek(link, axis, -toward * PARK_STEPS)
    spm = cfg["steps_per_mm"]
    print(f"  {axis}: zero at the {pin} trigger point, soft limits {lo}..{hi}. "
          f"The switch releases {hysteresis} steps ({hysteresis / spm:.2f} mm) "
          f"back from where it triggers; parked {PARK_STEPS} steps past that.")
    return hysteresis


def release(link, axis, cfg):
    """Back away from the axis's switch in RELEASE_CHUNK steps until it opens.
    Microswitches release well behind their trigger point (hysteresis), so a
    fixed back-off is not enough. Returns the steps it took."""
    pin, toward = cfg["pin"], cfg["toward"]
    limit = int(RELEASE_MAX_MM * cfg["steps_per_mm"])
    backed = 0
    while pin in closed_pins(link):
        if backed >= limit:
            raise HomingError(f"{axis}: {pin} still closed after backing off "
                              f"{backed} steps ({backed / cfg['steps_per_mm']:.2f} mm)"
                              " — is the switch stuck?")
        moved, _ = seek(link, axis, -toward * RELEASE_CHUNK)
        backed += abs(moved)
    return backed


def _check_hit(axis, pin, hit, moved):
    if hit == [pin]:
        return
    if not hit:
        raise HomingError(f"{axis}: moved {moved} steps without reaching {pin}. "
                          "Is the switch working, and is steps_per_mm right?")
    raise HomingError(f"{axis}: expected {pin} but {', '.join(hit)} closed — "
                      "the wiring or mechanics changed; run with --discover")


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #

def ask(prompt, choices):
    while True:
        ans = input(prompt).strip().lower()
        if ans in choices:
            return ans
        print(f"    please answer one of: {', '.join(choices)}")


def ask_steps_per_mm(axis, default):
    hint = f" [{default:g}]" if default else ""
    while True:
        ans = input(f"  Steps per mm for {axis}{hint} (motor steps/rev x microsteps "
                    "/ lead-screw mm per rev): ").strip()
        if not ans and default:
            return default
        try:
            value = float(ans)
            if value > 0:
                return value
        except ValueError:
            pass
        print("    enter a positive number")


def discover_axis(link, axis, previous, taken):
    print(f"\n=== {axis} ===")
    steps_per_mm = ask_steps_per_mm(axis, (previous or {}).get("steps_per_mm"))
    cfg = {"steps_per_mm": steps_per_mm, "travel_mm": TRAVEL_MM}
    max_steps = int(TRAVEL_MM * steps_per_mm * 1.2) + BIG_PROBE_STEPS + 200

    before = closed_pins(link)
    if before:
        print(f"  note: {', '.join(sorted(before))} already closed — if that is "
              f"{axis}'s switch, the first move will tell by releasing it.")
    input(f"  Press Enter to move {axis} by +{PROBE_STEPS} steps "
          "(watch which way it goes; Ctrl+C to quit)... ")

    probe = PROBE_STEPS
    while True:
        moved, hit = seek(link, axis, probe)
        released = before - closed_pins(link)
        sign = 1 if probe > 0 else -1
        if hit:
            pin, toward = _one(axis, hit), sign
            print(f"  {axis}: {pin} closed after {moved} steps -> + is towards it")
            break
        if len(released) == 1:
            pin, toward = released.pop(), -sign
            print(f"  {axis}: {pin} released -> it was on its switch, "
                  f"{'-' if toward < 0 else '+'} is towards it")
            break
        ans = ask(f"  {axis} moved {moved} steps and no switch closed. Did it move "
                  "(t)owards or (a)way from its switch, or (r)epeat with a bigger "
                  "move? [t/a/r] ", ("t", "a", "r"))
        if ans == "r":
            probe = sign * BIG_PROBE_STEPS
            before = closed_pins(link)
            continue
        toward = sign if ans == "t" else -sign
        print(f"  seeking {'+' if toward > 0 else '-'} for the switch "
              f"(up to {max_steps} steps)...")
        moved, hit = seek(link, axis, toward * max_steps)
        if not hit:
            raise HomingError(f"{axis}: moved {moved} steps without any switch "
                              "closing")
        pin = _one(axis, hit)
        print(f"  {axis}: {pin} closed after {moved} steps")
        break

    if pin in taken:
        raise HomingError(f"{axis}: {pin} is already {taken[pin]}'s switch")
    cfg.update({"pin": pin, "toward": toward})
    return cfg


def _one(axis, hit):
    if len(hit) != 1:
        raise HomingError(f"{axis}: several switches closed at once ({hit})")
    return hit[0]


# --------------------------------------------------------------------------- #

def load_config():
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh)
    return {"axes": {}}


def save_config(config):
    config["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(CONFIG, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--discover", action="store_true",
                   help="redo the interactive setup for the chosen axes")
    p.add_argument("--axes", nargs="+", default=["X", "Y", "Z"],
                   type=str.upper, choices=["X", "Y", "Z"])
    args = p.parse_args()

    config = load_config()
    link = open_link()
    print(f"stage {link.description}; config {CONFIG}")
    last_spm = None
    try:
        for axis in args.axes:
            known = config["axes"].get(axis)
            if known and not args.discover:
                known["hysteresis_steps"] = home_axis(link, axis, known)
                save_config(config)
                continue
            taken = {c["pin"]: a for a, c in config["axes"].items()
                     if a != axis and "pin" in c}
            # default steps/mm: this axis's saved value, else the last one typed
            previous = known or ({"steps_per_mm": last_spm} if last_spm else None)
            cfg = discover_axis(link, axis, previous, taken)
            config["axes"][axis] = cfg
            last_spm = cfg["steps_per_mm"]
            # Save what discovery found before homing, so a failure while
            # homing never means answering the questions again.
            save_config(config)
            print(f"  saved {axis} to {os.path.basename(CONFIG)}")
            cfg["hysteresis_steps"] = home_axis(link, axis, cfg)
            save_config(config)
    except HomingError as exc:
        print(f"\nSTOPPED: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nAborted.")
        return 1
    finally:
        link.close()
    print("\nHomed: " + ", ".join(args.axes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
