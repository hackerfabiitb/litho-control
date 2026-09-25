# XYZ Stage Controller

A browser-based UI + Arduino firmware for controlling a 3-axis stepper motor stage over USB Serial.

---

## File Structure

```
xyz-stage/
├── README.md                  ← this file
├── stage_controller.html      ← open in Chrome/Edge to run the UI
└── xyz1/
    └── xyz1.ino               ← flash this to your Arduino
```

---

## Hardware

| Component | Detail |
|---|---|
| Microcontroller | Arduino (Uno / Mega / compatible) |
| Stepper drivers | A4988 or DRV8825 (active-LOW enable) |
| Baud rate | 115200 |

### Pin Assignments

| Signal | Pin |
|---|---|
| X STEP | 3 |
| X DIR | 6 |
| Y STEP | 2 |
| Y DIR | 5 |
| Z STEP | 4 |
| Z DIR | 7 |
| SYS ENABLE | 8 |

---

## Serial Command Protocol

All commands are newline-terminated (`\n`). Arduino responds with structured messages the UI parses.

### Commands (PC → Arduino)

| Command | Example | Description |
|---|---|---|
| `A x y z` | `A 1000 500 0` | Absolute move to position |
| `R dx dy dz` | `R 100 -50 0` | Relative move from current position |
| `J axis delta` | `J X 100` | Jog single axis by delta steps |
| `H` | `H` | Set current position as home (0,0,0) |
| `Z axis` | `Z X` / `Z ALL` | Zero one or all axes |
| `S delay` | `S 800` | Set step delay in µs (lower = faster) |
| `E` | `E` | Enable motors |
| `D` | `D` | Disable motors (free spin) |
| `P` | `P` | Query current position |
| `L` | `L` | Query axis limits |

### Responses (Arduino → PC)

| Response | Example | Description |
|---|---|---|
| `POS x y z` | `POS 100 200 0` | Current position after every move |
| `LIMITS xmin xmax ymin ymax zmin zmax` | `LIMITS 0 10000 0 10000 0 10000` | Axis limits (sent on connect) |
| `DONE` | `DONE` | Move completed |
| `HOME` | `HOME` | Home set |
| `ENABLED` / `DISABLED` | — | Motor state |
| `SPEED n` | `SPEED 800` | Confirmed speed change |
| `ERR ...` | `ERR unknown command` | Error message |
| `READY` | `READY` | Sent on boot — UI knows Arduino is alive |

---

## EEPROM Position Persistence

The Arduino saves X, Y, Z to EEPROM after every move (addresses 0–12). On power-up, it restores the last known position so the UI immediately shows the correct location without re-homing.

**EEPROM layout:**

| Address | Content |
|---|---|
| 0 | Magic byte (0xAB) — detects first boot |
| 1–4 | X position (long) |
| 5–8 | Y position (long) |
| 9–12 | Z position (long) |

---

## Software Limits

Defined as constants at the top of `xyz1.ino`:

```cpp
const long X_MIN =     0;
const long X_MAX = 10000;  // ← set this to your actual travel
const long Y_MIN =     0;
const long Y_MAX = 10000;  // ← set this to your actual travel
const long Z_MIN =     0;
const long Z_MAX = 10000;  // ← set this to your actual travel
```

All move commands are clamped to these limits in firmware. The UI also reads them on connect and displays them.

### Finding Your Limits (TODO)

1. Set speed slow: type `S 3000` in the raw command box
2. Zero all axes with the Zero All button
3. Jog one axis toward its physical end in large steps, then switch to 10-step increments near the end
4. Stop when motion sounds rough or the stage resists
5. Subtract a 200-step safety margin from the displayed position
6. Update `X_MAX` / `Y_MAX` / `Z_MAX` in `xyz1.ino` and reflash

---

## UI Features

- **Arrow keys** → jog X/Y
- **PgUp / PgDn** → jog Z
- Step size: 1 / 10 / 100 / 500 / 1000 or custom
- Speed slider (step delay 100–5000 µs)
- Go-to absolute position
- Go-to relative (single axis)
- Home and Zero per-axis or all
- Position bars scale to firmware limits
- Motor enable/disable toggle
- Raw command input
- Color-coded serial log

---

## TODO / Future Work

- [ ] Set actual `X_MAX`, `Y_MAX`, `Z_MAX` by jogging to physical limits
- [ ] Add endstop switches for repeatable homing (wired to Arduino digital pins)
- [ ] Simultaneous multi-axis motion (currently axes move sequentially)
- [ ] Acceleration/deceleration ramp to prevent missed steps at high speed
- [ ] Save named positions / waypoints in the UI
- [ ] Unit conversion (steps → mm or degrees) based on lead screw pitch / microstepping config

---

## Running the UI

1. Open `stage_controller.html` in **Chrome or Edge** (Web Serial API required — Firefox not supported)
2. Click **Connect** and select your Arduino's COM port
3. The Arduino sends position and limits immediately on connect

No server, no install — it's a plain HTML file.
