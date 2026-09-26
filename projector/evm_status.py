"""Decode DLPC6540 status replies copied from the DLP EVM GUI's Command Log.

The GUI shows raw bytes (Debug > Command Log, "Receive Data") but only a
few of the flags on its pages. The bit layouts here were read out of the
GUI's own command library (DLPComposer.Commands.DLPC654x.dll, GUI 3.2.0.7):
Command.ReadSystemStatus / ReadDmdInfo / ReadDmdErrorStatusRegister.

    python projector/evm_status.py system "14 0C 00 00 00 00 00 04 00 40 00 00 00 00 00"
    python projector/evm_status.py dmdinfo "14 22 00 94 00 0D 60 04 00 07 00 30 30 31 ..."
    python projector/evm_status.py dmderr "14 04 00 .. .. .. .."

The first 3 bytes of "Receive Data" are the reply header and are skipped.
"""

import argparse

# Read System Status (D4 06 00 00): 12 bytes = three little-endian 32-bit
# words: state, errors, and word 2 (only bit 0 is named).
SYSTEM_STATE = {          # word 0
    0: "CwSpinning", 1: "CwPhaselock", 2: "CwFreqlock", 3: "Lamplit",
    4: "MemTstPassed", 10: "FrameRateConvEn", 11: "SeqPhaselock",
    12: "SeqFreqlock", 13: "SeqSearch", 29: "ScpcalEnable",
    30: "VicalEnable", 31: "BccalEnable",
}
SYSTEM_ERRORS = {         # word 1
    0: "SequenceErr", 1: "PixclkOor", 2: "SyncvalStat",
    6: "UartPort0CommErr", 7: "UartPort1CommErr", 8: "UartPort2CommErr",
    9: "SspPort0CommErr", 10: "SspPort1CommErr", 11: "SspPort2CommErr",
    12: "I2CPort0CommErr", 13: "I2CPort1CommErr", 14: "I2CPort2CommErr",
    15: "DlpcInitErr", 16: "LampHwErr", 17: "LampPprftout",
    19: "NoFreqBinErr", 20: "Dlpa3005CommErr", 21: "UmcRefreshBwUnderflowErr",
    22: "DmdInitErr", 23: "DmdPwrDownErr", 24: "SrcdefNotpresent",
    25: "SeqbinNotpresent", 26: "ProductConfigurationFailed",
    27: "TemporalDitherMaskNotLoading",
}
SYSTEM_WORD2 = {0: "EepromInitFail"}
# Read Dmd Error Status Register: one little-endian 32-bit word.
DMD_ERRORS = {
    0: "HssiPktError", 1: "LsifPktError", 2: "ParkModeEnable",
    3: "HsifEnable", 4: "LsifParityError", 8: "MpsTimeoutMonitor",
}


def payload(text):
    data = bytes(int(b, 16) for b in text.replace(",", " ").split())
    return data[3:]


def word(data, i):
    return int.from_bytes(data[4 * i:4 * i + 4].ljust(4, b"\0"), "little")


def show(title, value, names):
    print(f"{title}: 0x{value:08X}")
    for bit, name in names.items():
        if value >> bit & 1:
            print(f"  bit {bit:2}  {name}")
    unknown = value & ~sum(1 << b for b in names)
    if unknown:
        print(f"  (bits not named by the GUI: 0x{unknown:08X})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kind", choices=["system", "dmdinfo", "dmderr"])
    ap.add_argument("receive_data", help="the GUI's Receive Data column, hex bytes")
    args = ap.parse_args()
    data = payload(args.receive_data)

    if args.kind == "system":
        show("state (word 0)", word(data, 0), SYSTEM_STATE)
        show("errors (word 1)", word(data, 1), SYSTEM_ERRORS)
        show("word 2", word(data, 2), SYSTEM_WORD2)
    elif args.kind == "dmdinfo":
        print(f"DmdId   0x{word(data, 0):08X}")
        print(f"FuseId  0x{word(data, 1):08X}")
        print(f"DmdName {data[9:17].decode('ascii', 'replace')!r}")
    else:
        value = word(data, 0)
        show("DMD error status", value & ~0xE0, DMD_ERRORS)
        print(f"  BsaStepDownCounter = {value >> 5 & 7}")


if __name__ == "__main__":
    main()
