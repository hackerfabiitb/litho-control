"""Find the COM port the stage's Arduino is on.

Matches by USB vendor/product ID. Genuine Arduino boards (VID 0x2341 /
0x2A03) are preferred; common clone USB-serial chips (CH340, FTDI, CP210x)
are reported as candidates only, because other devices on this PC use them
too — COM3 is a CH343 adapter that is *not* the Arduino.

    python xyz_stage/find_arduino.py            # list and pick
    python xyz_stage/find_arduino.py --probe    # also check the firmware answers

Importable: `find_arduino()` returns the port name, e.g. "COM4".
"""

import argparse
import sys
import time

from serial.tools import list_ports

ARDUINO_VIDS = {0x2341, 0x2A03}                  # Arduino LLC / Arduino SRL
CLONE_IDS = {(0x1A86, 0x7523): "CH340", (0x0403, 0x6001): "FTDI FT232",
             (0x10C4, 0xEA60): "CP210x"}
BAUD = 115200                                    # matches xyz1.ino


def candidates():
    """(port, description, is_genuine) for every plausible Arduino port."""
    out = []
    for p in list_ports.comports():
        if p.vid in ARDUINO_VIDS:
            out.append((p.device, f"{p.description} [{p.vid:04X}:{p.pid:04X}]", True))
        elif (p.vid, p.pid) in CLONE_IDS:
            out.append((p.device, f"{p.description} [{CLONE_IDS[(p.vid, p.pid)]}]", False))
    return out


def find_arduino():
    """The Arduino's port. Raises if there is not exactly one genuine board
    (or, failing that, exactly one clone candidate)."""
    found = candidates()
    genuine = [c for c in found if c[2]]
    pick = genuine or found
    if len(pick) != 1:
        raise RuntimeError("expected one Arduino, found: "
                           + (", ".join(f"{d} ({desc})" for d, desc, _ in found)
                              or "none"))
    return pick[0][0]


def probe(port, wait=2.5):
    """Open the port (which resets an Uno), collect the boot banner, ask for
    the position. Returns the lines received. No motion commands are sent."""
    import serial
    lines = []
    with serial.Serial(port, BAUD, timeout=0.2) as s:
        end = time.time() + wait                  # bootloader + setup()
        while time.time() < end:
            line = s.readline().decode(errors="replace").strip()
            if line:
                lines.append(line)
        s.write(b"P\n")
        end = time.time() + 1.0
        while time.time() < end:
            line = s.readline().decode(errors="replace").strip()
            if line:
                lines.append(line)
    return lines


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--probe", action="store_true",
                   help="open the port and print what the firmware says "
                        "(resets the board; sends only the position query)")
    args = p.parse_args()

    print("serial ports:")
    for port in list_ports.comports():
        vidpid = f"{port.vid:04X}:{port.pid:04X}" if port.vid is not None else "-"
        print(f"  {port.device:6s} {vidpid:9s} {port.description}")

    try:
        port = find_arduino()
    except RuntimeError as exc:
        print(f"\n{exc}")
        return 1
    print(f"\nArduino: {port}")

    if args.probe:
        lines = probe(port)
        for line in lines:
            print(f"  < {line}")
        if "READY" in lines:
            print("xyz1 firmware is running")
        elif lines:
            print("board answers, but not with the xyz1 boot banner")
        else:
            print("no answer — firmware missing, or a different baud rate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
