# Start the UI server (camera, projector, page) in its own window and open the
# UI in Chrome, or Edge if Chrome is not installed. Close the server window
# (or Ctrl+C in it) to stop it and release the camera.
$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$server = Join-Path $PSScriptRoot "server.py"
Start-Process $py -ArgumentList "`"$server`""
Start-Sleep -Seconds 2
$browser = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "Neither Chrome nor Edge found (Web Serial needs one)" }
Start-Process $browser -ArgumentList "--new-window", "http://localhost:8765"
