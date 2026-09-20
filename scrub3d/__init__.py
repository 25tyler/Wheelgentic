"""scrub3d -- depth reconstruction and placement-agnostic multi-arm scrubbing.

A SEPARATE SUBSYSTEM, NOT A PATCH. Nothing in this package modifies anything in
py/, web/, tests/, run.sh or config.json. The existing single-arm demo is
untouched and remains the ultimate fallback; `git status` on this worktree must
show changes under scrub3d/ and nowhere else.

Where we must interoperate with the existing arm driver we do it by SUBCLASSING
and INJECTION rather than by editing:

  - py/arm.py:118 is `port = port or find_port()`, so passing an explicit port
    bypasses the macOS-only /dev/cu.* discovery entirely.
  - Arm.set_target is a plain method, so armlink.GovernedArm can run the
    governor's checks first and only then delegate. The module-level BOX is
    NOT a wider backstop, as this file used to say: it is narrower than the
    arm's reach in x and it CLAMPS rather than refuses, so a point outside it
    is refused by armlink before BOX can silently move it.
  - The existing test suite already builds Arm via __new__ to bypass the serial
    port, so the same trick gives us a hardware-free fake on Windows without
    touching tests/fake_roarm.py's POSIX-only pty.

We also never run py/scrubbot.py, which means its unconditional
signal.SIGHUP -- nonexistent on Windows -- never executes. Do not "fix" that
file; we simply do not use it.
"""

# The package, grouped by what each module is for. Listed rather than
# star-imported: importing scrub3d should cost nothing, and several of these
# pull in torch, mediapipe or pyrealsense2 the moment they load.
__all__ = [
    # the world, and frames from it
    "frames", "record", "rsfeed",
    # shape, measured once
    "sapiens", "pose", "girth", "scan", "fetch_models",
    # pose, every frame
    "track",
    # the body, and how it looks
    "bodymodel", "anatomy", "skin", "shell", "bodystore",
    # the arms
    "kinematics", "armmesh", "collide",
    # who scrubs what, and how
    "partition", "place_arms", "control",
    # moving, touching, calibrating
    "adapt", "torque", "handeye",
    # consent, governor, the bridge to the arms, configuration, entry point
    "session", "fleet", "armlink", "rigconfig", "main",
    # the operator view
    "viz",
]
