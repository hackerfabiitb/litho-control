# ui — control UI and server

One page runs the whole setup: stage control, projector display and a
deflickered camera feed. The page and the live feeds come from one Python
process, `server.py`.

```powershell
.\ui\start.ps1
```

That starts the server in its own window and opens `http://localhost:8765` in
Chrome, or Edge if Chrome is missing. Close the server window to stop it and
release the camera.

Then:
- **Stage:** nothing to do. The server finds the Uno by its USB ID (with
  `xyz_stage/find_arduino.py`) and connects at startup. It retries every 3 s
  if the board is missing or unplugged. The top bar shows
  `connected · COM4`.
- **Projector:** click **Projector** to load an image and project it.

## Layout

| area | shows |
| --- | --- |
| **PROJECTOR** pane | a screen capture of the projector's display, 1280 px wide, about 5 fps. It shows what the projector is really showing, including the desktop or anything that pops up over the pattern |
| **CAMERA** pane | the deflickered camera feed at full resolution, 1936×1216. The header shows fps in → shown, the frame mean against the keep cutoff, and how many frames were forced |
| controls under the panes | exposure (µs), keep ≥ (fraction of the recent top), max hold (s); **Apply** changes them live |
| rest of the page | stage jog, calibration and focus, from litho-ui (see [xyz_stage](../xyz_stage/README.md)) |

## Why a Python server, not a JavaScript one

- **The camera:** it's only reachable through pypylon, so Python is needed
  either way. A Node server would just add a second process.
- **The stage:** a browser page can't open a COM port by itself; Web Serial
  needs a person to pick the port on each website. So the server owns the
  serial port and connects automatically, and the page talks to it over HTTP
  and server-sent events. Scripts can use the stage through the same
  endpoints while the UI is open.
- **The projector:** the server draws images on the projector itself, in a
  full-screen OpenCV window that is DPI-aware and runs at the native
  3840×2160 (`projector/display.py`). The litho-ui page tried a browser popup
  (`projector.html`, which never existed). A browser on the 150 %-scaled
  projector display would resample the image, which is not acceptable for
  masks.

## Projecting

In the **Projector** dialog:
- **Load an image** (PNG, JPG, BMP or TIFF), then click **Project Image**.
- **Fit modes:**
  - **1:1 pixels** (default): one image pixel per projector input pixel,
    centred and cropped if the image is too large. This is the mode for masks.
  - **Contain / Cover / Stretch:** scale the image. Enlarging uses
    nearest-neighbour, so binary masks stay binary.
- **Blank:** black, window kept.
- **Close:** removes the window; the projector shows the desktop again, and
  desktop light would expose resist.

The window only opens with the first Project or Blank. Starting the server
doesn't change what the projector shows.

## HTTP API

| endpoint | |
| --- | --- |
| `GET /` | the page (`index.html`) |
| `GET /stage/events` | server-sent events: `status` (JSON: `connected`, `port`, `idle_mode`, `error`), `replay` (cached state lines for a newly opened page: POS, LIMITS, CAL…, FOCUS, LS limit switches) and `line` (every line from the firmware) |
| `GET /stage/status` | the status JSON |
| `POST /stage/send` | `{"line": "J X 10"}`: one firmware command (protocol in `xyz_stage/litho-ui_README.md`) |
| `POST /stage/connect`, `POST /stage/disconnect` | take or release COM4. Connecting is the default; release it to flash firmware |
| `GET /stream` | deflickered camera, MJPEG |
| `GET /projector/stream` | projector screen mirror, MJPEG |
| `GET /config`, `POST /config` | camera settings and stats; POST any of `exposure_us`, `keep_frac`, `max_hold` |
| `GET /focus_score` | Laplacian variance of the latest shown camera frame |
| `GET /projector/status` | `state` (`closed` / `blank` / `image`), image name and size, fit |
| `POST /projector/image?fit=native&name=x.png` | body = image file |
| `POST /projector/fit` | `{"fit": "native" / "contain" / "cover" / "stretch"}` |
| `POST /projector/blank`, `POST /projector/close` | |

Projector calls return once the change is on screen.

## Stage

- **Motors between moves** (left column): **Release** (`D`, the default, quiet)
  or **Hold** (`E`, holding torque, the DRV8825s squeal).
  - **Every move is powered and counted in both modes.** The only difference
    is torque at rest, so use Hold if an axis drifts while released.
  - The buttons show the firmware's real mode. The server tracks it from the
    `ENABLED` / `DISABLED` replies, and from `READY`, since the Uno boots in
    Release.
- **Connecting resets the Uno.** It sits in its bootloader for about 1.5 s,
  and bytes sent then are read as bootloader commands. So the server sends
  nothing until the firmware prints `READY`. Then it sends one `P`, because
  the boot banner's first line often loses characters (seen:
  `POS -704 648 9038` for `POS -17004 6848 9038`).
- **A reloaded page** gets position, limits, calibration and focus from the
  server's cache, without querying the firmware.
- **Disconnect** (top right) releases COM4, e.g. for `arduino-cli upload`.
  **Connect** takes it back, which resets the Uno again.

## Things to know

- **The server holds the camera and COM4** while it runs. Stop it before running
  `camera\run.ps1`, `capture_frames.py` or the `projector\` scripts, and the
  other way round.
- **Camera defaults** match `camera\run.ps1`: full sensor, 400 µs,
  keep ≥ 0.8 × recent top, 0.5 s max hold. The same rule is used
  (`camera/deflicker.py`). Override them with
  `server.py --exposure --keep-frac --max-hold`.
- **Only one server at a time**, because it uses port 8765.

## Log

### 2026-09-25 — built

- **Replaces** litho-ui's file-based page, `camera_server.py` and `start.bat`.
  The page moved here from `xyz_stage/stage_controller.html`.
- **Tested with the server running:**
  - the camera ran at 41 fps in, 21–24 shown, with a longest hold of 98 ms;
  - `grid` projected at 1:1 appeared correctly in both panes;
  - fit changes, blank and close all worked;
  - a bad upload was rejected with 400;
  - a live exposure change worked;
  - a headless Edge screenshot of the page showed both panes live.
- **Not yet tested by hand:** stage control from the new URL.

### 2026-09-25 — stage auto-connect; Release/Hold selector

- **Serial moved into the server.** It connected to COM4 at startup with no
  clicks. A new page received the cached POS / LIMITS / UNCALIBRATED /
  FOCUS 260.
- **Garbled first line fixed.** The first POS after a reset came through
  garbled; the refresh `P` after `READY` now replaces it.
- **Idle mode** followed `E` → hold and `D` → release (no motion).
- **The Position readout cut off 5-digit values** (`-17004` shown as `-170`)
  because its column was 24 px wide in the litho-ui page. Fixed.
