# scrub3d/live/run_openyam.ps1 -- the live view driving the two OpenYAM arms.
#
# Run this ON THE LAPTOP (the computer the camera is plugged into), in PowerShell:
#
#     cd C:\Users\justi\Documents\thingy-scrub3d
#     powershell -ExecutionPolicy Bypass -File scrub3d\live\run_openyam.ps1
#
# NOT on the Spark. The Spark only runs the bridge (bash scrub3d/live/bridge.sh real aim),
# and a VS Code window connected to the Spark opens every one of its terminals there.
# Ctrl+C here draws the arms back and holds them with their power on.
#
# The numbers, and which way each one goes:
$env:SCRUB3D_ARM           = "openyam"
$env:SCRUB3D_DIMOS         = "10.189.59.208:7790"   # the bridge on the Spark
$env:SCRUB3D_PARTS         = "arms"                 # his arms only, not the torso
$env:SCRUB3D_SIDE_DEG      = "65"                   # only the outer side of each arm; smaller = narrower strip
$env:SCRUB3D_BODY_MM       = "30"                   # how near the robot's links may come to him
$env:SCRUB3D_SPONGE_R_MM   = "58"                   # bigger = sponge rides further off his skin (10 = about 1 cm)
$env:SCRUB3D_TOUCH_MM_S    = "300"                  # how fast the sponge closes on him
$env:SCRUB3D_NEAR_MM_S     = "350"                  # how fast it travels near him, off the skin
$env:SCRUB3D_SCRUB_MM_S    = "450"                  # how fast it goes along a stroke
$env:SCRUB3D_LEAD_MM       = "70"
$env:SCRUB3D_REAL_BODY_MM  = "-30"
$env:SCRUB3D_LIMB_TRIM_MM  = "10"
$env:SCRUB3D_LEFT_BACK_MM  = "0"                    # + = the LEFT sponge lands further back, - = further forward
$env:SCRUB3D_RIGHT_BACK_MM = "0"                    # the same for the RIGHT one
$env:SCRUB3D_LEFT_IN_MM    = "40"                   # + = the LEFT sponge lands further IN toward him (it was stopping short), - = further out
$env:SCRUB3D_RIGHT_IN_MM   = "0"                    # the same for the RIGHT one.  10 = 1 cm

Set-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
python -u scrub3d/live/live_body.py --drive dimos --upside-down --rig scrub3d/live/live_rig_openyam.json
