# xyz_stage — Arduino stepper stage

An Arduino Uno with a stepper motor shield drives three steppers (X, Y, Z).
The firmware and browser UI come from the older
[hackerfabiitb/litho-ui](https://github.com/hackerfabiitb/litho-ui) repo
(copied at commit `bda24e5`).

| file | what it is | origin |
| --- | --- | --- |
| `firmware/xyz1/xyz1.ino` | stage firmware: serial protocol, soft limits, EEPROM position memory | litho-ui |
| `stage_controller.html` | browser UI, talks to the Arduino directly over Web Serial | litho-ui |
| `camera_server.py` | MJPEG server that feeds the Basler camera into the UI (not used yet) | litho-ui |
| `start.bat` | original launcher: starts `camera_server.py` and Chrome | litho-ui |
| `litho-ui_README.md` | original README: full serial protocol, EEPROM layout, UI features | litho-ui |
| `XYZ_Stage_Controller_Manual.docx` | original user manual | litho-ui |
| `find_arduino.py` | finds the Arduino's COM port, and can check which firmware is on it | new |
| `start_ui.ps1` | opens the UI in Chrome, or Edge if Chrome is missing; no camera server | new |
| `firmware/backup/` | the Uno's flash before `xyz1` was loaded | new |

## Hardware

| item | detail |
| --- | --- |
| board | Arduino Uno (genuine, USB `2341:0043`) on **COM4** |
| other serial port | COM3 is a CH343 USB-serial adapter (`1A86:55D3`) — not the stage |
| drivers | A4988 / DRV8825 on the shield; enable is active LOW |
| pins (as used by `xyz1.ino`) | X step 3 / dir 6, Y step 2 / dir 5, Z step 4 / dir 7, enable 8 |
| baud | 115200 |

The pins follow the common CNC Shield V3 layout, with X and Y swapped: the
firmware's X uses the shield's Y slot and vice versa.

## Find the port

```powershell
.\.venv\Scripts\python.exe xyz_stage\find_arduino.py           # -> Arduino: COM4
.\.venv\Scripts\python.exe xyz_stage\find_arduino.py --probe   # also print the boot banner
```

`--probe` opens the port, which **resets the Uno**, then sends only `P` (a
position query). Nothing moves. From Python, use
`from find_arduino import find_arduino`.

## Run the UI

```powershell
.\xyz_stage\start_ui.ps1
```

Then click **Connect** and choose **COM4**. Web Serial only lets a person
pick the port, so no script can connect for you. Only one program can hold
COM4 at a time: disconnect in the UI before running `find_arduino.py
--probe` or flashing.

- **Camera panel:** shows "disconnected" unless `camera_server.py` is running.
  That's expected for now. The server needs the Basler camera, so it can't run
  alongside `camera\run.ps1`.
- **Projector window:** the UI opens `projector.html`, which isn't in
  litho-ui, so this button doesn't work.
- **Keys:** arrow keys jog X/Y, PgUp/PgDn jog Z. See `litho-ui_README.md`
  for the rest.

## Flash the firmware

The Arduino IDE 2.3.10 is installed per user via winget
(`ArduinoSA.IDE.stable`), with the `arduino:avr` 1.8.8 core. Its bundled
`arduino-cli` works from a terminal:

```powershell
$cli = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\ArduinoSA.IDE.stable_Microsoft.Winget.Source_8wekyb3d8bbwe\resources\app\lib\backend\resources\arduino-cli.exe"
& $cli compile --fqbn arduino:avr:uno xyz_stage\firmware\xyz1
& $cli upload  --fqbn arduino:avr:uno -p COM4 xyz_stage\firmware\xyz1
```

Or open `firmware\xyz1\xyz1.ino` in the Arduino IDE and upload it to the
Uno on COM4.

### Restore the previous sketch

Before `xyz1` was loaded, the Uno ran a different sketch that printed
"Variable Speed Controller Active". It took commands such as `S 200` for
steps/s and `X 500` to move. Its source isn't in this repo, but the flash
image is:

```powershell
$avr  = "$env:LOCALAPPDATA\Arduino15\packages\arduino\tools\avrdude\8.0.0-arduino1\bin\avrdude.exe"
$conf = "$env:LOCALAPPDATA\Arduino15\packages\arduino\tools\avrdude\8.0.0-arduino1\etc\avrdude.conf"
& $avr -C $conf -p atmega328p -c arduino -P COM4 -b 115200 -U flash:w:xyz_stage/firmware/backup/uno_before_xyz1_flash.hex:i
```

The EEPROM couldn't be backed up. The Uno's bootloader returns flash when
asked for EEPROM.

## Log

### 2026-09-25 — set up; `xyz1` flashed

- **Previous state:** the Uno was running the "Variable Speed Controller"
  sketch, not `xyz1`. Its flash is backed up (see above), and `xyz1` was
  flashed over it.
- **Boot banner after flashing:**
  `POS -17154 6848 8895`, `LIMITS 0 0 0 0 0 0`, `UNCALIBRATED`,
  `FOCUS 260`, `READY`. The EEPROM already held `xyz1` state, so the board
  ran `xyz1` at some earlier point.
- **Stored position is stale:** the other sketch ran in between and may have
  moved the motors, so that position likely doesn't match where the stage
  really is. Zero the axes (or recalibrate the endpoints) before relying on
  absolute positions.
- **No soft limits:** no axis is calibrated, so any move is allowed. Keep
  jog sizes small until the endpoints are marked.
- **Motor currents:** `xyz1` enables the motors (holding current) at boot.
