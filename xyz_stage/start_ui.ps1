# Open the stage controller UI in Chrome, or Edge if Chrome is not installed.
# Both support the Web Serial API it needs; Firefox does not. Unlike the
# original start.bat, this does not start camera_server.py.
$html = Join-Path $PSScriptRoot "stage_controller.html"
$browser = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "Neither Chrome nor Edge found" }
Start-Process $browser -ArgumentList "--new-window", "`"$html`""
