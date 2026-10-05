# Start the UI server (camera, projector, stage, page) in THIS terminal and
# open the UI in a Firefox window of its own once the server answers. The two
# live and die together:
#   Ctrl+C here (or the server exiting)  -> the UI window closes
#   closing the UI window                -> the server stops cleanly
# Arguments are passed to server.py, e.g.  .\ui\start.ps1 --monitor DISPLAY5
$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$server = Join-Path $PSScriptRoot "server.py"
$firefox = @(
    "$env:ProgramFiles\Mozilla Firefox\firefox.exe",
    "${env:ProgramFiles(x86)}\Mozilla Firefox\firefox.exe",
    "$env:LOCALAPPDATA\Mozilla Firefox\firefox.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $firefox) { throw "Firefox not found" }

# A profile of its own (-no-remote) gives the UI a separate Firefox instance,
# apart from your normal Firefox windows, so its lifetime is the window's.
# The window is recognised by this profile path in its command line, never
# by the PID that Start-Process returns: browsers may hand over to another
# process and exit (Edge's launcher did, which made the UI close at once).
$profileDir = Join-Path $env:LOCALAPPDATA "litho-control\ui-firefox"
New-Item -ItemType Directory -Force $profileDir | Out-Null
# No first-run pages, default-browser prompt, quit warnings or crash
# restore (the window may be force-closed).
@'
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.startup.page", 0);
user_pref("browser.warnOnQuit", false);
user_pref("browser.tabs.warnOnClose", false);
'@ | Set-Content -Encoding ascii (Join-Path $profileDir "user.js")

function Get-UiBrowser {
    Get-CimInstance Win32_Process -Filter "Name = 'firefox.exe'" |
        Where-Object { $_.CommandLine -like "*$profileDir*" }
}

function Close-UiBrowser {
    $procs = @(Get-UiBrowser)
    if (-not $procs) { return }
    # Ask the window to close, then make sure.
    $procs | ForEach-Object {
        try { [void](Get-Process -Id $_.ProcessId -ErrorAction Stop).CloseMainWindow() } catch {}
    }
    for ($i = 0; $i -lt 30 -and @(Get-UiBrowser).Count; $i++) { Start-Sleep -Milliseconds 100 }
    Get-UiBrowser | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Close-UiBrowser    # a window left over from an earlier run would swallow the launch

# The server blocks this terminal, so a background job waits for it to answer,
# opens the window, and stops the server once the window is gone. It talks
# to 127.0.0.1, not localhost: Windows PowerShell 5.1 tries IPv6 ::1 first and
# times out, since the server listens on IPv4 only.
$window = Start-Job -ArgumentList $firefox, $profileDir -ScriptBlock {
    param($firefox, $profileDir)
    $base = "http://127.0.0.1:8765"
    function Test-Window {
        [bool](Get-CimInstance Win32_Process -Filter "Name = 'firefox.exe'" |
               Where-Object { $_.CommandLine -like "*$profileDir*" })
    }
    $up = $false
    for ($i = 0; $i -lt 60 -and -not $up; $i++) {
        try { Invoke-WebRequest "$base/stage/status" -UseBasicParsing -TimeoutSec 1 | Out-Null; $up = $true }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $up) { return }    # the server failed to start; its error is in the terminal
    Start-Process $firefox -ArgumentList @(
        "-no-remote", "-profile", "`"$profileDir`"", "-new-window", "http://localhost:8765")
    for ($i = 0; $i -lt 50 -and -not (Test-Window); $i++) { Start-Sleep -Milliseconds 200 }
    # Poll (Start-Sleep can be interrupted; a job blocked in a .NET wait
    # can't be stopped, and Remove-Job below would hang).
    while (Test-Window) { Start-Sleep -Milliseconds 500 }
    try { Invoke-WebRequest "$base/shutdown" -Method Post -UseBasicParsing -TimeoutSec 5 | Out-Null } catch {}
}

try {
    & $py $server @args
} finally {
    Close-UiBrowser
    Remove-Job $window -Force -ErrorAction SilentlyContinue
}
