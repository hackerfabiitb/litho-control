# camera — Basler deflicker

Drop black frames from a Basler acA1920-40um (USB3, mono) viewing a flickering
light source (the DLP projector).

Setup (pylon SDK, venv) is in the [top-level README](../README.md). All commands
below run from the repo root.

Check that the camera is visible:

```powershell
.\.venv\Scripts\python.exe -c "from pypylon import pylon; print(pylon.TlFactory.GetInstance().EnumerateDevices()[0].GetModelName())"
# -> acA1920-40um
```

Only one process can hold a USB3 Basler camera at a time — **close pylon Viewer
before running these scripts**, or you get
`Device is exclusively opened by another client`.

## Recommended settings (live view of the projector)

The same feed, with the same settings and keep rule (`deflicker.py`), is the
CAMERA pane of the control UI ([`ui/`](../ui/README.md)), next to a mirror of
the projector. Use either the UI or this standalone viewer, not both: they
can't share the camera.

```powershell
.\camera\run.ps1
# which runs:
.\.venv\Scripts\python.exe camera\deflicker_live.py --full --scale 0.5 --exposure 400 --keep-frac 0.8 --max-hold 0.5
```

| flag | why |
| --- | --- |
| `--full` | full sensor, 1936×1216 |
| no `--fps` | uncapped (41 fps). Capped rates of 240/n (40, 30, 24, 20, 15…) can lock into the projector's dark gap for seconds |
| `--exposure 400` | bright desktop content clips on 28 % of pixels at 1000 µs, 5 % at 400 µs, none at 200 µs. Lower it if bright areas still clip; raise it if the image is too dark |
| `--keep-frac 0.8` | keep frames at least 80 % as bright as the recent brightest. The rule is relative, so it follows scene and exposure changes with no numbers to tune; `[` / `]` adjust it by 0.02 |
| `--max-hold 0.5` | never hold one frame on screen longer than 0.5 s (the default) |

No `--threshold-low` or `--threshold-high`: `--keep-frac` replaces them.

Measured live on the desktop image, 20 s each:

| exposure | display updates | longest time a frame stayed up | clipped pixels |
| --- | --- | --- | --- |
| 1000 µs | 24/s | 98 ms | 28 % |
| **400 µs** | **23/s** | **98 ms** | **5 %** |
| 200 µs | 19/s | 292 ms | 0 % |

Frames that pass `--keep-frac 0.8` vary by about 7 % in brightness at 400 µs.
To remove that too, use a whole-period exposure (below), which needs less
light.

## Why the camera sees flicker (measured 2026-09-25)

The projector doesn't give off steady light, and the camera's short exposure
samples it. `projector/flicker.py waveform` measured the light over time: a
16-row AOI at 920 fps with a 34 µs exposure, timed by the camera's hardware
timestamps.

- **The light repeats at 240.00 Hz**, which is 4 × the 60 Hz HDMI input, and
  locked to it. Each input frame has four identical 4.167 ms sub-frames.
- **Each sub-frame starts with a 1.25 ms dark gap** (about 30 % of the time).
  After that come a few shorter dips and one brief bright spike. Plot:
  [`projector/docs/2026-09-25_illumination_waveform.png`](../projector/docs/2026-09-25_illumination_waveform.png).
- **What decides a frame's brightness** is where its exposure starts within
  the 4.167 ms cycle. A 1000 µs exposure that starts in the gap comes out
  black. The waveform predicts 26.8 % black frames at 1000 µs, and
  `capture_frames.py` measured 26.3 %.

**The frame rate matters as a stroboscopic effect** (`flicker.py sweep`, 1000 µs,
8 s per rate):

| camera fps | relation to 240 Hz | dark frames | longest dark run | longest freeze |
| --- | --- | --- | --- | --- |
| 41.05 (max), 37, 27, 17 | not a divisor: the phase moves every frame | ~10 % | 1 frame | 49–118 ms |
| 40, 24, 15 | 240/n: phase-locked, in a lit phase this time | 0 % | 0 | — |
| **30, 20, 10** | 240/n: phase-locked **in the dark gap** | 30–40 % | 32–97 frames | **up to 3.3 s** |

At 240/n frame rates the camera catches the same point in the cycle every
frame. Which point it gets is luck, and it only changes slowly as the two
clocks drift, so a run can go dark for seconds. **Avoid 40, 30, 24, 20, 15,
12 and 10 fps.** Uncapped (41.05 fps full-frame) is fine.

**The keep band can't fix it.** With the desktop projected at 1000 µs, frame
brightness is a continuous spread, not two clusters:
- 26 % of frames are black (the exposure fell in the gap).
- 59 % "blown": these saw the full light, but the projector is so bright that
  they clip.
- The 15 % left in the keep band are the frames that only partly overlapped
  the gap, which are the least consistent ones.

**The real fix is an exposure of whole periods**: 4167, 8333 or 16667 µs. Then
every frame integrates the same light whatever its phase. The waveform
predicts flicker falling to 0 % at those exposures, from 51 % at 1000 µs. It
also makes the frame rate irrelevant, and nothing needs dropping.

That hasn't been tested yet, because the light is too strong. Even a black
image (stray light plus the stuck-on DMD bands) reaches about 28 counts per
100 µs, so everything saturates well before 4 ms. **Reduce the light about
10×** (lower LED current in the DLP GUI, stop down the lens, or add an ND
filter), then check with:

```powershell
.\.venv\Scripts\python.exe projector\flicker.py sweep --exposure 1000 4167 8333 16667
```

## 1. Measure the flicker

```powershell
.\.venv\Scripts\python.exe camera\capture_frames.py --num 120
```

Grabs 120 frames into RAM (so PNG encoding can't cause dropped frames), then writes:

- `camera/frames/frame_00007_black_mean001.23.png` — every frame, labelled
  `black`/`blown`/`lit` with its mean intensity in the filename
- `camera/frames/frame_stats.csv` — per-frame `mean`, `p99`, `max`, `sat_frac`,
  `is_black`, `is_blown`, timestamp

and prints a histogram of mean intensity, the cluster centres, the **fraction of
frames that are black and blown out**, and the longest consecutive dropped run.

Useful flags:

| flag | effect |
| --- | --- |
| `--exposure 5000` | pin exposure to 5000 µs and disable auto exposure |
| `--gain 6` | pin gain (dB) |
| `--fps 30` | cap the acquisition rate |
| `--save black` | write only the black frames (also `blown`, `dropped`, `none`) |

## Black and blown-out frames

A frame is kept when its mean intensity lands inside the band
`--threshold-low .. --threshold-high`: below the low cutoff it is a black flicker
frame, above the high cutoff it is blown out (fully white). Both default to auto
and both scripts take the same flags.

| flag | effect |
| --- | --- |
| `--threshold-low 107` | black cutoff (`--threshold` is kept as an alias) |
| `--threshold-high 206` | blown-out cutoff |
| `--no-high` | never drop bright frames, whatever the auto split says |

`low` is the 2-means split of the per-frame means. `high` is found by
re-splitting only the non-black frames, and is applied **only when the upper
cluster is genuinely saturated** — most of its pixels pegged within 2% of full
scale, while the lower cluster is not. Without that guard a run containing no
blown frames would have its brightest good frames discarded; with it, a merely
bright scene keeps every frame and the script reports "no saturated cluster
found".

Measured here at `--exposure 500`, all three populations show up cleanly:

```
  dark cluster centre   3.42
  lit cluster centre    159.03
  blown cluster centre  253.53
  suggested low  cutoff 107.00
  suggested high cutoff 206.28

  BLACK FRAMES: 29 / 40 = 72.5%
  BLOWN FRAMES:  6 / 40 = 15.0%
  USABLE:        5 / 40 = 12.5%
```

## Resolution / AOI

The pylon Viewer resolution box is just the GenICam `Width`/`Height`/`OffsetX`/
`OffsetY` nodes, so both scripts expose them (same flags on each):

| flag | effect |
| --- | --- |
| `--width 640 --height 480` | area of interest, centred by default |
| `--offset-x 320 --offset-y 200` | put the AOI somewhere other than the centre |
| `--binning 2` | combine 2x2 pixels — quarter the data, better SNR |
| `--binning-mode Sum` | brighten while binning (default `Average` keeps the intensity scale, which matters because the black-frame threshold *is* an intensity) |
| `--full` | full sensor, offsets zeroed, binning reset to 1 |

Measured on this camera (`acA1920-40um`):

| AOI | max frame rate |
| --- | --- |
| 1936x1216 (full) | 41.1 fps |
| 640x480 | 99.2 fps |

Reading out fewer rows is what buys the speed, so `--height` matters far more
than `--width`. A faster frame rate also means finer time resolution on the
flicker, which makes the black/lit split cleaner.

Two things worth knowing:

- The real sensor is **1936x1216**; 1920x1200 is the nominal spec figure. `--full`
  gives you the former.
- **The camera keeps these settings between runs** — they live in the camera, not
  the script. Run `--binning 2` once and every later run stays binned until you
  pass `--full` or `--binning 1`, or power-cycle the camera. The scripts warn when
  they find leftover binning. Values are clamped to the camera's range and snapped
  to its increment (width steps by 4), and any adjustment is printed.

Auto thresholding is a 1-D 2-means split of the per-frame mean intensity. If the
distribution isn't clearly bimodal the script says so — that usually means the
exposure is long enough to average over the flicker, so shorten it.

## 2. Live deflickered view

```powershell
.\.venv\Scripts\python.exe camera\deflicker_live.py --scale 0.5
# or, with the settings tuned for the projector:
.\camera\run.ps1
```

Scores each frame, drops the ones outside the keep band, and holds the last
frame on screen so the view is steady instead of strobing. Without
`--threshold` it calibrates on the first 60 frames (`--calib`).

**Max hold.** A frame is never held for more than `--max-hold` seconds
(default 0.5). After that, the next frame is shown even if it's dark or blown
out, and it's labelled `FORCED: BLACK` / `FORCED: BLOWN`. The view keeps
updating at least twice a second even when the thresholds drop almost
everything. `--max-hold 0` restores the old behaviour of holding
indefinitely. Forced frames are counted separately and aren't written to
`--record`.

**Relative keep rule.** `--keep-frac 0.8` keeps a frame when its mean is at
least 0.8 × the 95th percentile of the last ~2 s of frame means. With the
projector, frame brightness is a continuous spread from black up to "saw the
full light". The top of that spread is the good level, and a fixed cutoff
stops fitting as soon as the scene or exposure changes. This mode replaces
`--threshold-low` and skips calibration; `r` relearns the recent level.
There's no high cutoff unless `--threshold-high` is given.

`--duration 10` quits after 10 s, for unattended tests. The exit summary gives
the display update rate and the longest time any frame stayed on screen.

The frame rate is uncapped unless `--fps` is given. The camera remembers a cap
from earlier runs (pylon Viewer, `flicker.py`), so the script clears it on
open. Don't pass `--fps` values of 240/n; see
[Why the camera sees flicker](#why-the-camera-sees-flicker-measured-2026-09-25).

| key | action |
| --- | --- |
| `q` / `Esc` | quit |
| `r` | recalibrate (only the cutoffs you did not pin on the command line) |
| `[` / `]` | lower / raise the **low** cutoff by 1 (with `--keep-frac`: the fraction by 0.02) |
| `{` / `}` | lower / raise the **high** cutoff by 1 (shift + `[` / `]`) |
| `space` | toggle deflicker off/on to compare against the raw flicker |
| `s` | save a snapshot PNG |

The overlay shows the current mean against the keep band, kept/total, recent drop
percentage, separate black, blown and forced counts, and input vs output frame
rate. A
dropped frame is labelled `DROPPED: BLACK` or `DROPPED: BLOWN`, and if the auto
split found only one brightness population the overlay says so rather than
silently throwing away half the good frames.

`--scale` sets the *initial* window size only. The window is freely resizable
and the image keeps its aspect ratio, letterboxed with black bars — note that
this is done in code, not by `cv2.WINDOW_KEEPRATIO`, which is `0` (a no-op) and
leaves the Windows HighGUI backend stretching the image to fill the window.

`--record out.avi` writes the deflickered (kept-only) stream to an MJPG file.

## Notes

- Grabbing uses `GrabStrategy_OneByOne` for the capture step (every frame counts)
  and `GrabStrategy_LatestImageOnly` for the live view (stay current, never lag).
- Dropping frames makes the output rate uneven — if a downstream consumer needs a
  constant rate, hold the last good frame instead of dropping (that is what the
  live display does visually).
- If the flicker is periodic and you would rather *avoid* black frames than drop
  them, the camera's hardware trigger (`TriggerMode=On`, `TriggerSource=Line1`)
  synced to the light source is the better fix; this project is the software
  fallback.
