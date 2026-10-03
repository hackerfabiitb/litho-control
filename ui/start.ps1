# Start the UI server (camera, projector, stage, page) in THIS terminal and
# open the UI in Chrome, or Edge if Chrome is not installed, once the server
# answers. Ctrl+C stops the server and releases the camera and COM4.
# Arguments are passed to server.py, e.g.  .\ui\start.ps1 --monitor DISPLAY5
$py = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$server = Join-Path $PSScriptRoot "server.py"
$url = "http://localhost:8765"
$browser = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

# The server blocks this terminal, so a background job waits for it to answer
# and then opens the page. If the server fails to start, the job gives up
# after 30 s and the error stays visible here. It polls 127.0.0.1, not
# localhost: Windows PowerShell 5.1 tries IPv6 ::1 first and times out, since
# the server listens on IPv4 only. (Browsers fall back by themselves.)
$opener = Start-Job -ArgumentList $url, $browser -ScriptBlock {
    param($url, $browser)
    for ($i = 0; $i -lt 60; $i++) {
        try {
            Invoke-WebRequest "http://127.0.0.1:8765/stage/status" -UseBasicParsing -TimeoutSec 1 | Out-Null
            if ($browser) { Start-Process $browser -ArgumentList "--new-window", $url }
            else { Start-Process $url }
            return
        } catch { Start-Sleep -Milliseconds 500 }
    }
}

try {
    & $py $server @args
} finally {
    Remove-Job $opener -Force -ErrorAction SilentlyContinue
}
