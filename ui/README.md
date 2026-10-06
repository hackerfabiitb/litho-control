# ui — control UI and server

One page runs the whole setup: stage control, projector display and a
deflickered camera feed. The page and the live feeds come from one Python
process, `server.py`.

```powershell
.\ui\start.ps1
```

That runs the server **in the current terminal**, and opens the UI in a
**Firefox** window of its own as soon as the server answers. Errors and status stay visible in the terminal. The window and the
server live and die together:
- **Ctrl+C** in the terminal, or the server exiting for any reason, closes
  the window;
- **closing the window** stops the server cleanly (`POST /shutdown`), which
  releases the camera and COM4.

Arguments go to `server.py`, e.g. `.\ui\start.ps1 --monitor DISPLAY5`.

The window uses its own Firefox profile
(`%LOCALAPPDATA%\litho-control\ui-firefox`, started with `-no-remote`), so it
is a separate Firefox instance, apart from your normal Firefox windows.
`start.ps1` writes a `user.js` there that turns off first-run pages, the
default-browser prompt, quit warnings and crash restore. The script
recognises the window by that profile path in the process command line, not
by the process ID it launched. A UI window left over from an earlier run is
closed at startup. On first use, Firefox shows a Terms of Use bar once, and
it remembers the window size after you maximise it.

The server starts even if some hardware is missing. It prints each problem
once and keeps retrying every 3 s:

| missing | printed | fix |
| --- | --- | --- |
| Uno | `[stage] expected one Arduino, found: none` | plug in the Uno's USB |
| camera | `[camera] No Basler camera found…` | plug in the camera's USB3; close pylon Viewer and `camera\run.ps1` |
| projector display | `projector display not found (… found: \\.\DISPLAY1 …)` | turn the projector on. If it's plugged in but left out of the desktop ("PC screen only"), the server extends the desktop to it by itself (`DisplaySwitch.exe /extend`, i.e. Win+P → Extend; at most every 30 s; `--no-extend` turns this off). Meanwhile projector commands answer 409 with this reason |

Then:
- **Stage:** nothing to do. The server finds the Uno by its USB ID (with
  `xyz_stage/find_arduino.py`) and connects by itself. The top bar shows
  `connected · COM4`.
- **Projector:** click **Image…** (under the panes) to load an image and project it.

## Layout

| area | shows |
| --- | --- |
| **top bar** | "HackerFab IITB Litho Control", the local time as `YYYY-MM-DD_HH-MM-SS` (24 h), the **stage** status (`stage: connected · COM4` = the server's serial link to the Uno), **Release / Connect stage** |
| **left column** | **position**: one row per axis, `limit min [‹ position ›] limit max` ("—" for an axis without limits). ‹ › move one step; typing a position and pressing Enter moves that axis there (clamped to its limits). Then step size (presets, and a field showing the current step), step delay (µs per step, lower = faster), motors between moves, a state snapshot export, and the **serial log** with a raw-command box at the bottom |
| **PROJECTOR** pane | a screen capture of the projector's display, 1280 px wide, about 5 fps. It shows what the projector is really showing, including the desktop or anything that pops up over the pattern. The **mouse pointer** is drawn in when it's on the projector's screen (screen captures leave it out) |
| **CAMERA** pane | the deflickered camera feed at full resolution, 1936×1216. The header shows fps in → shown, the frame mean against the keep cutoff, and how many frames were forced |
| controls under the panes | exposure (µs, ‹ › ±50), keep ≥ (fraction of the top, ±0.02), max hold (s, ±0.1). **Deflicker: on/off** (off = every camera frame, flicker included). **Calibrate deflicker**: see below. **White**: projects `patterns/white_4K.jpg`. **Image…**: the projector dialog |
| under the panes | **Z focus**: set / go / autofocus sweep |

**Number fields** all work the same way (a grey box with ‹ › at its ends):
- ‹ › step the value and apply it at once;
- a typed value applies on Enter or when you click away;
- Escape reverts;
- ↑ / ↓ nudge the value while you're editing.

Text is white at 1.5× litho-ui's sizes, laid out for a 1920×1080 screen.
Digits are all one width, and changing numbers (camera stats, clock) are
padded to a fixed width, so the text around them doesn't shift.

**Keys** (anywhere outside a text field): ← → move X, ↑ ↓ move Y, PgUp/PgDn
move Z, one step each. On X, ← is +X (it moves the stage left), so it lights
the field's › arrow.

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

In the projector dialog (**Image…** under the panes):
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

## Deflicker calibration

The deflicker keeps a frame when its mean brightness is at least keep ×
the "top" (`camera/deflicker.py`). Uncalibrated, the top is the 95th
percentile of the last ~2 s of frames, so it follows the projected image: on
an all-white image the cutoff climbs with it.

**Calibrate deflicker** runs that same computation once and freezes it:
1. projects `patterns/calibrate_deflicker.png` (the Windows wallpaper: dark
   background, bright shapes);
2. takes the 95th percentile of 5 s of frame means;
3. puts back whatever the projector was showing.

From then on the cutoff is keep × that top, whatever is projected.
- **Saved** in `camera/deflicker_calibration.json` (git-ignored) and loaded at
  startup.
- **Exposure changes** scale the top in proportion. Recalibrate after
  changing the light (LED current, optics).
- **Back to the rolling rule:** delete the json and restart the server.

Measured 2026-10-05, 400 µs: the top was 15.2 from 205 frames, so the cutoff
was 12.2.
- **On white**, the cutoff stayed at 12.2 and the kept frames were saturated
  (mean 250).
- **The cost:** about 5 % of the raw frames on white are part-lit (seen: 14,
  31 and 94), above that cutoff, so they get through as an occasional dimmer
  frame. The rolling rule rejected them.

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
| `GET /config`, `POST /config` | camera settings and stats (including `deflicker` and `calibration`); POST any of `exposure_us`, `keep_frac`, `max_hold`, `deflicker` (true/false) |
| `POST /camera/calibrate` | deflicker calibration (above); takes ~6 s, answers with the stats, or 409 if the camera or projector is missing |
| `GET /focus_score` | Laplacian variance of the latest shown camera frame |
| `GET /projector/status` | `state` (`closed` / `blank` / `image`), image name and size, fit |
| `POST /projector/image?fit=native&name=x.png` | body = image file |
| `POST /projector/fit` | `{"fit": "native" / "contain" / "cover" / "stretch"}` |
| `POST /projector/blank`, `POST /projector/close` | |
| `POST /projector/pattern` | `{"name": "white_4K.jpg"}`: show an image from `patterns/` at 1:1 |
| `POST /shutdown` | stop the server cleanly, as Ctrl+C does. `start.ps1` sends it when the UI window is closed |

Projector calls return once the change is on screen.

## Stage

- **Homing** is done by `xyz_stage/homing.py` (see
  [xyz_stage](../xyz_stage/README.md#homing)). It goes through this server,
  and its moves show up in the page, including the new soft limits
  (`CAL X DONE 0 4800`, which is 12 mm). **Don't click anything that sends
  a stage command while it runs:** any byte aborts a homing move.
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
- **Release stage** (top right) releases COM4, e.g. for `arduino-cli upload`.
  The top bar then says `released · click Connect stage`, and the terminal
  prints `[stage] releasing COM4 …`. **Connect stage** takes it back, which
  resets the Uno again. (The camera's own Disconnect, in the camera header,
  only stops the page's video streams.)

## Things to know

- **The server holds the camera and COM4** while it runs. Stop it before running
  `camera\run.ps1`, `capture_frames.py` or the `projector\` scripts, and the
  other way round.
- **Camera defaults** match `camera\run.ps1`: full sensor, 400 µs,
  keep ≥ 0.8 × recent top, 0.5 s max hold. The same rule is used
  (`camera/deflicker.py`), with the top frozen once calibrated. Override them with
  `server.py --exposure --keep-frac --max-hold`.
- **Only one server at a time**, because it uses port 8765.

## Log

### 2026-10-06 — one number field everywhere; Projector button moved; "stage:" label

- **Number field:** one style, like a Blender number field, for the position
  of each axis, the step size, the step delay (it replaces the slider), the
  camera's exposure, keep and max hold, and autofocus range, step and settle.
  Behaviour is described under Layout.
- **Position fields:** typing a value and pressing Enter sends `A` for that
  axis.
- **Limit labels:** "limit min" / "limit max" headers sit above the limit
  columns.
- **Top bar:** the Projector button is gone. Its dialog (load image, fit,
  Blank, Close) opens from **Image…** next to White. The status reads
  `stage: …`.
- **Tested** with Selenium and headless Firefox against the running server:
  - keep ›/‹ gave 0.82 / 0.80 on the server;
  - exposure 450 + Enter applied, 999 + Escape reverted, and 400 + click away
    applied;
  - the step field followed the presets, and › un-highlighted them.

  The position arrows and typed positions weren't exercised, because they
  move the stage.

### 2026-10-06 — position rows with limits and ◀ ▶; jog, go-to and limits panels removed; title and clock

- **Position:** each axis is now one row, `low limit ◀ position ▶ high limit`,
  with no progress bar.
- **Removed as redundant:** the Jog control card, the Limits (effective)
  panel and its CALIBRATED badge, Go to absolute, Go to relative and Go to
  zero. Z Focus now takes the full width.
- **Top bar:** "HackerFab IITB Litho Control" and a `YYYY-MM-DD_HH-MM-SS`
  clock.
- **Fixed-width numbers:** tabular digits everywhere, and the camera stats
  are padded.
- **Saved State note** reworded.
- **Checked:** the page script passes `node --check`, and a headless Firefox
  screenshot against the running server showed the new layout.

### 2026-10-06 — "connected on COM4" in the terminal, "disconnected" in the UI

- **Cause:** COM4 had been released through the top bar's red **Disconnect**
  button (`POST /stage/disconnect`), which sits next to Projector and the
  camera's Disconnect. The server printed nothing on release, so the
  terminal's last stage line was still `connected on COM4`.
- **Ruled out:** live updates reaching Firefox. A Selenium-driven headless
  Firefox watched the top bar: it went from connected to disconnected within
  1 s of `/stage/disconnect`, and back to `connected · COM4` 1 s after
  `/stage/connect`.
- **Fixes:**
  - The server prints a line on release and on reconnect.
  - `/stage/status` has `released`, and the top bar says
    `released · click Connect stage`.
  - The button is labelled **Release stage** / **Connect stage**.
  - The page's initial text is `waiting for the server…` rather than
    `disconnected`.
  - The button's log line now reads the state before the request; it used to
    log "Searching for the Uno…" on a release.

### 2026-10-05 — deflicker calibration, deflicker on/off, White button; settings save on Enter or blur

- **Calibrate deflicker** freezes the keep rule's top, measured on
  `patterns/calibrate_deflicker.png`. `KeepFilter.fixed_top`, and
  `relative_cutoff` run once on the collected means.
- **Deflicker: on/off** (`KeepFilter.enabled`) and **White**
  (`POST /projector/pattern`).
- **Apply removed:** each camera field is sent on Enter or blur.
- **Tested on the hardware**, through the HTTP API:
  - calibrate took 6.2 s, used 205 frames, gave top 15.2 and cutoff 12.2,
    and restored the projector;
  - on white the cutoff stayed at 12.2, with 12 frames/s shown;
  - deflicker off showed 41 of 41 frames/s;
  - exposure 400 → 200 µs halved the cutoff (6.1), and it was restored at
    400.

  The page script passes `node --check`. The buttons weren't clicked in a
  browser.

### 2026-10-05 — UI closed right after opening (Edge launcher); Firefox; automatic Win+P → Extend

- **Symptom:** the UI window closed right after it opened.
- **Cause:** with a visible window, Edge's `msedge.exe` started the real
  browser as a child process and exited at once. The job was waiting on the
  PID it launched, took that exit as "window closed", and sent `/shutdown`.
  The server stopped, and the window was closed with it. The earlier tests
  used `--headless`, which doesn't hand over, so they missed this.
  Reproduced: launched PID exited after 0.5 s while 8 processes with the
  profile kept running.
- **Fixes:**
  - **Firefox** instead of Edge (`-no-remote -profile …\ui-firefox`).
  - The window is tracked by its profile path in the command line, so a
    launcher hand-off can't fool it.
  - **Tested with a real, visible window** in PowerShell 5.1 and 7:
    - it stays open (checked at 4, 8 and 12 s);
    - closing it (`CloseMainWindow`, as the X button does) stops the server
      in 1.3–1.6 s;
    - a real Ctrl+C closes it in 1.2–1.3 s.

    A screenshot showed the UI in one tab, with no welcome page.
- **Win+P → Extend** is automatic. `display.detached_displays()` lists
  outputs that have a monitor but aren't in the desktop, and
  `display.extend_desktop()` runs `DisplaySwitch.exe /extend`. The server
  does this when it can't find the projector and something is detached, at
  most every 30 s (`--no-extend` turns it off). With the projector switched
  off, its outputs list no monitor, so nothing gets switched and the screens
  don't flicker. Seen today: the projector was off (not on USB, no monitor),
  so `/extend` had nothing to extend to.

### 2026-10-03 — UI window and server close together; mouse pointer in the PROJECTOR pane

- **Lifetime.** `start.ps1` opens the UI with `--app` and a dedicated
  `--user-data-dir`, so the window is its own browser process. A background
  job watches that process and sends `POST /shutdown` when it exits. The
  script's `finally` closes the window whenever the server exits (Ctrl+C,
  `/shutdown`, a crash): it closes the main window first, then force-closes
  after 3 s.
  - **Bug found in testing:** the job originally waited with
    `$p.WaitForExit()`. PowerShell can't stop a job blocked in a .NET call,
    so `Remove-Job` hung and the window stayed open. It now polls with
    `Start-Sleep`, and the window is closed before the job is removed.
  - **Tested** in Windows PowerShell 5.1 and PowerShell 7, with a headless
    browser standing in for the window:
    - killing the browser stops the server ("shutdown requested");
    - `/shutdown` closes the browser and ends the script;
    - a real Ctrl+C, sent with `GenerateConsoleCtrlEvent` to a console
      started through WMI, stops everything in about 5 s.

    Shells launched from Claude's tool shell ignore Ctrl+C, so the first Ctrl+C
    test proved nothing.
- **Pointer.** `/projector/stream` draws an arrow at the mouse position
  (`GetCursorInfo`, physical pixels, matching the per-monitor-DPI-aware
  monitor rectangles) when it's inside the projector's rectangle. Checked by
  mirroring `DISPLAY1` (`--monitor DISPLAY1`): the arrow appeared at the
  mouse's position.

### 2026-10-03 — start.ps1 runs in the current terminal; server starts without the projector

- **Symptom:** `.\ui\start.ps1` opened a new window that printed an error and
  closed before it could be read.
- **Cause:** `server.py` exited at startup because `find_projector()` found
  no projector monitor. Windows could see the projector (USB, and the
  `DLP PICO 4K` monitor present as `DISPLAY5` on the NVIDIA card), but it
  wasn't attached to the desktop; only `\\.\DISPLAY1` (the Dell) was. The
  window came from `Start-Process`, which closes as soon as Python exits.
- **Fixes:**
  - `start.ps1` runs the server in the current terminal. A background job
    opens the browser once the server answers. The job polls `127.0.0.1`,
    because Windows PowerShell 5.1 tries `localhost` as IPv6 `::1` first and
    times out (the server is IPv4 only). Tested in PowerShell 5.1 and 7 with
    the browser stubbed out.
  - The server starts without the projector display. It looks for it again
    every 3 s and before each projector command, and meanwhile answers
    projector commands with 409 and the reason. The page shows "projector
    display not found" with the Win+P hint.
  - Stage and camera errors print once per new problem, not on every 3 s
    retry (the camera's used to repeat).
- **Seen in the test:** the Uno and the Basler camera weren't on USB either
  (no COM ports at all), so all three printed their "not found" lines.

### 2026-09-26 — litho-ui leftovers removed; log moved left; larger white text

- **Removed**, because they rewrite the position or the soft limits and so
  break homing (`homing.py` owns zero and the limits now):
  - the Calibration card (`CAL ENDP`: the midpoint of two jogged extremes
    became 0) and its Clear All (`CAL CLEAR`);
  - state Import (`CAL MANUAL`, which recentred every axis) and the
    browser-storage state that fed it. Export stays, as a record only;
  - **Set home** (`H`), **Zero** (`Z X`) and **Undo** (`UNDO`): they change
    the position counter without moving, so the limits stop matching the
    end stops.
- **Fixed:**
  - "Home (go to calibrated centre)" is now **Go to zero** (the homed end).
  - The speed slider sent `S` for every pixel of a drag, and restored a
    value the firmware never got. It now sends once on release and shows the
    firmware's `SPEED` reply.
  - Autofocus clamped Z to 0..0 when Z had no limits.
  - Two elements shared the id `calBadge`.
  - Dead CSS (the old calibration modal, screen picker, motor toggle) and
    "Web Serial" wording were removed.
- **Layout:** the serial log moved from the right column into the left one,
  under Saved State. Z Focus sits to the right of Jog. The text is white
  (it was #666/#444 grey) and 1.5× larger.
- **Checked:** `node --check` on the script, and a headless Edge screenshot
  at 1920×1080 against the running server. Everything fits without the left
  column scrolling.

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
