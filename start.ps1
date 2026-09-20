# start.ps1 -- the whole application, on the laptop the camera is plugged into.
#
#     powershell -ExecutionPolicy Bypass -File start.ps1            # the real arms
#     powershell -ExecutionPolicy Bypass -File start.ps1 -Dry       # nothing real moves
#
# Two windows open: the robot backend (scrub3d/live/carebot.py) and the website
# (carechair/server.js). Then open http://127.0.0.1:5173 : press the microphone
# (or button A on the chair) and say "I'd like a shower", "stop", "bring me water",
# "help me eat", "time for my meds". The cartoon and the 3D view are on that page.
#
# BEFORE the real arms: the bridge must be up on the Spark (bash scrub3d/live/bridge.sh
# real), and carechair/.env must hold the Deepgram and Meta keys. Nothing here starts
# the bridge, and nothing moves until somebody asks for something.
param([switch]$Dry, [switch]$RightSideUp)

$root = $PSScriptRoot
if (-not (Test-Path "$root\carechair\.env")) {
    Write-Host "  carechair\.env is missing: copy your .env there (voice needs its keys)." -ForegroundColor Yellow
}
if (-not (Test-Path "$root\carechair\node_modules")) {
    Push-Location "$root\carechair"; npm ci --no-audit --no-fund; Pop-Location
}

$mode = if ($Dry) { "--dry" } else { "--real" }
$cam  = if ($RightSideUp -or $Dry) { "" } else { "--upside-down" }   # this rig's camera hangs upside down
Start-Process powershell -ArgumentList "-NoExit", "-Command",
    "cd '$root'; python -u scrub3d/live/carebot.py $mode $cam"

# The website, told where the robot backend is. What is set here wins over .env.
$env:ROBOT_MODE = "live"
$env:ROBOT_BACKEND_URL = "http://127.0.0.1:8770"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\carechair'; node server.js"

Start-Sleep -Seconds 3
Start-Process "http://127.0.0.1:5173"
