# Start the UI server (camera, projector, stage, page) in THIS terminal and
# open the UI in its own app window (Chrome, or Edge if Chrome is missing)
# once the server answers. The two live and die together:
#   Ctrl+C here (or the server exiting)  -> the UI window closes
#   closing the UI window                -> the server stops cleanly
# Arguments are passed to server.py, e.g.  .\ui\start.ps1 --monitor DISPLAY5
$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$server = Join-Path $PSScriptRoot "server.py"
$browser = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "Neither Chrome nor Edge found" }

# A profile of its own makes the browser start a separate process for the UI
# window, whose lifetime is the window's. (With the normal profile, the launch
# hands over to the already running browser and exits at once.)
$profileDir = Join-Path $env:LOCALAPPDATA "litho-control\ui-browser"

function Get-UiBrowser {
    Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe' OR Name = 'msedge.exe'" |
        Where-Object { $_.CommandLine -like "*$profileDir*" }
}

function Close-UiBrowser {
    $procs = @(Get-UiBrowser)
    if (-not $procs) { return }
    # Ask the window to close (the main process has no --type=), then make sure.
    $procs | Where-Object { $_.CommandLine -notlike "*--type=*" } | ForEach-Object {
        try { [void](Get-Process -Id $_.ProcessId -ErrorAction Stop).CloseMainWindow() } catch {}
    }
    for ($i = 0; $i -lt 30 -and @(Get-UiBrowser).Count; $i++) { Start-Sleep -Milliseconds 100 }
    Get-UiBrowser | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Close-UiBrowser    # a window left over from an earlier run would swallow the launch

# The server blocks this terminal, so a background job waits for it to answer,
# opens the window, and stops the server when the window is closed. It talks
# to 127.0.0.1, not localhost: Windows PowerShell 5.1 tries IPv6 ::1 first and
# times out, since the server listens on IPv4 only.
$window = Start-Job -ArgumentList $browser, $profileDir -ScriptBlock {
    param($browser, $profileDir)
    $base = "http://127.0.0.1:8765"
    $up = $false
    for ($i = 0; $i -lt 60 -and -not $up; $i++) {
        try { Invoke-WebRequest "$base/stage/status" -UseBasicParsing -TimeoutSec 1 | Out-Null; $up = $true }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $up) { return }    # the server failed to start; its error is in the terminal
    $p = Start-Process $browser -PassThru -ArgumentList @(
        "--app=http://localhost:8765", "--user-data-dir=`"$profileDir`"",
        "--no-first-run", "--no-default-browser-check", "--start-maximized")
    # Poll rather than $p.WaitForExit(): a job blocked inside a .NET call
    # can't be stopped, and Remove-Job below would then hang.
    while (-not $p.HasExited) { Start-Sleep -Milliseconds 300 }
    try { Invoke-WebRequest "$base/shutdown" -Method Post -UseBasicParsing -TimeoutSec 5 | Out-Null } catch {}
}

try {
    & $py $server @args
} finally {
    Close-UiBrowser
    Remove-Job $window -Force -ErrorAction SilentlyContinue
}
