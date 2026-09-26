"""Print the limit switch pins live, so switches can be checked by hand.

The xyz1 firmware reports the CNC Shield V3 endstop pins as
"LS D9=1 D10=1 D11=1 D12=1" at boot, on the LS command, and whenever a pin
changes (debounced 10 ms). Pins have pull-ups: a switch wired to GND reads
1 (open) when released and 0 (closed) when pressed; a normally-closed switch
is the other way round.

    python xyz_stage/limit_switches.py                 # until Ctrl+C
    python xyz_stage/limit_switches.py --duration 60 --log out.txt

Goes through ui/server.py when it is running (it owns COM4), otherwise
opens the port itself.
"""

import argparse
import json
import sys
import time
import urllib.request

SERVER = "http://127.0.0.1:8765"
# Which shield header each pin serves (both - and + of an axis are in parallel).
NAMES = {"D9": "X", "D10": "Y", "D11": "Z", "D12": "Z (GRBL 1.1 / some shields)"}


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
        cells = []
        changed = []
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


def via_server(printer, deadline):
    req = urllib.request.urlopen(SERVER + "/stage/events", timeout=30)
    # ask for the current state once the stream is open
    urllib.request.urlopen(urllib.request.Request(
        SERVER + "/stage/send", data=json.dumps({"line": "LS"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
    kind = None
    for raw in req:
        line = raw.decode(errors="replace").rstrip("\r\n")
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: ") and kind in ("line", "replay"):
            data = line[6:]
            if data.startswith("LS "):
                printer.show(parse(data))
        if deadline and time.time() > deadline:
            return


def direct(printer, deadline):
    import serial
    from find_arduino import BAUD, find_arduino
    port = find_arduino()
    print(f"(ui/server.py not running: opening {port} directly; the Uno resets)")
    with serial.Serial(port, BAUD, timeout=0.2) as s:
        ready = False
        while not deadline or time.time() < deadline:
            line = s.readline().decode(errors="replace").strip()
            if line == "READY" and not ready:
                ready = True
                s.write(b"LS\n")    # boot banner lines can be garbled; re-ask
            elif line.startswith("LS ") and ready:
                printer.show(parse(line))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--duration", type=float, default=0, help="seconds (0 = until Ctrl+C)")
    p.add_argument("--log", default=None, help="also append the output to this file")
    args = p.parse_args()

    print("Limit switches: 1 = open, 0 = CLOSED (switch to GND). "
          "Toggle each one; changes are marked <<.  Ctrl+C to stop.")
    printer = Printer(args.log)
    deadline = time.time() + args.duration if args.duration else None
    try:
        try:
            urllib.request.urlopen(SERVER + "/stage/status", timeout=2)
        except OSError:
            return direct(printer, deadline)
        via_server(printer, deadline)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
