"""Print the limit switch pins live, so switches can be checked by hand.

The xyz1 firmware reports the CNC Shield V3 endstop pins as
"LS D9=1 D10=1 D11=1 D12=1" at boot, on the LS command, and whenever a pin
changes (debounced 10 ms). Pins have pull-ups: a switch wired to GND reads
1 (open) when released and 0 (closed) when pressed; a normally-closed switch
is the other way round.

    python xyz_stage/limit_switches.py                 # until Ctrl+C
    python xyz_stage/limit_switches.py --duration 60 --log out.txt

Goes through ui/server.py when it is running (it owns COM4), otherwise
opens the port itself (stage_link.py).
"""

import argparse
import sys
import time

from stage_link import open_link

# Shield header each pin serves. Which axis a switch really belongs to is
# found by homing.py (the wiring may not follow the header labels).
NAMES = {"D9": "X hdr", "D10": "Y hdr", "D11": "Z hdr", "D12": "Z hdr (GRBL 1.1)"}


def parse(line):
    """'LS D9=1 D10=0 ...' -> {'D9': 1, 'D10': 0, ...}"""
    out = {}
    for item in line.split()[1:]:
        pin, _, level = item.partition("=")
        out[pin] = int(level)
    return out


class Printer:
    def __init__(self, log_path=None):
        self.last = None
        self.t0 = time.time()
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None

    def show(self, levels):
        stamp = f"{time.time() - self.t0:7.2f}s"
        cells, changed = [], []
        for pin, level in levels.items():
            state = "open  " if level else "CLOSED"
            mark = ""
            if self.last is not None and self.last.get(pin) != level:
                mark = " <<"
                changed.append(f"{pin} ({NAMES.get(pin, '?')}) -> {state.strip()}")
            cells.append(f"{pin}={level} {state}{mark}")
        text = f"{stamp}  " + "   ".join(cells)
        if changed:
            text += "    changed: " + ", ".join(changed)
        print(text, flush=True)
        if self.log:
            self.log.write(text + "\n")
            self.log.flush()
        self.last = levels


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--duration", type=float, default=0, help="seconds (0 = until Ctrl+C)")
    p.add_argument("--log", default=None, help="also append the output to this file")
    args = p.parse_args()

    link = open_link()
    print(f"Limit switches ({link.description}): 1 = open, 0 = CLOSED (switch to "
          "GND). Toggle each one; changes are marked <<.  Ctrl+C to stop.")
    printer = Printer(args.log)
    deadline = time.time() + args.duration if args.duration else None
    link.send("LS")
    try:
        while not deadline or time.time() < deadline:
            try:
                line = link.wait_for(lambda l: l.startswith("LS "), timeout=1.0)
            except TimeoutError:
                continue
            printer.show(parse(line))
    except KeyboardInterrupt:
        pass
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
