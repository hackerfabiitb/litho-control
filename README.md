# litho-control

Control for a maskless lithography setup: a DLP projector exposes the pattern,
a camera watches it, and an XYZ stage moves the sample.

| directory | hardware | status |
| --- | --- | --- |
| [`camera/`](camera/README.md) | Basler acA1920-40um (USB3, mono) | live view with DLP flicker removed; flicker measurement |
| [`projector/`](projector/README.md) | TI DLP471TEEVM over HDMI + USB | test patterns, camera-to-DMD calibration, DMD fault scan |
| `stage/` | XYZ stage | not started |

## Setup

Windows 11, Python 3.12.

1. Install the Basler **pylon SDK**. It ships the USB3 driver and runtime that
   `pypylon` binds to; without it, `import pypylon` fails.
2. Install TI's **DLP EVM GUI** for the projector's USB control interface.
3. Create the venv at the repo root and install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Optionally, activate the venv (`.\.venv\Scripts\Activate.ps1`; if PowerShell
blocks it, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy
RemoteSigned`). Every command in these READMEs runs from the repo root.

## Hardware check

```powershell
# projector on USB (must be powered on — it does not enumerate otherwise)
Get-PnpDevice -PresentOnly | Where-Object InstanceId -match 'VID_0451&PID_7540'
# projector on HDMI
Get-PnpDevice -Class Monitor -PresentOnly
# camera
.\.venv\Scripts\python.exe -c "from pypylon import pylon; print(pylon.TlFactory.GetInstance().EnumerateDevices()[0].GetModelName())"
```

## Debugging log

Newest first. The details are in each directory's README.

| date | area | issue | status |
| --- | --- | --- | --- |
| 2026-09-25 | projector | Four 128-mirror-column bands at DMD mirror columns 832–960, 1216–1344, 1472–1600 and 1728+ show wrong data (stuck on, noise, stale content), depending on the image. The mirrors themselves work, which points to the controller-to-DMD flex or DMD seating. [Details](projector/README.md#2026-09-25--vertical-stripes-on-the-projected-image--open) | open: reseat the flex, then run the GUI internal test patterns |
| 2026-09-25 | projector | Not detected on USB | fixed: the projector was switched off |
