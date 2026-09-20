"""scrub3d/devconsole -- a developer-only view of everything the backend does.

    python scrub3d/devconsole/app.py              # the console, http://127.0.0.1:8077
    python scrub3d/devconsole/runner.py main.py   # any scrub3d script, instrumented
    python scrub3d/devconsole/check.py            # the console's own self-test

NOT THE PROJECTOR AND NOT THE OPERATOR VIEW
--------------------------------------------
`web/` is what a room sees and is somebody else's work; Rerun is what the operator
watches. This is for whoever is changing the code: what ran, in what order, how long
each stage took, what it decided and why a gate said no.

THE OVERVIEW
------------
The first tab puts the rig in live 3D, in Rerun's own web viewer, next to
every data process drawn as one diagram. A running job reaches the 3D because
live3d.py reads the recording the job saves while the file grows, and pipes it
to a Rerun server the console owns: the job gains no code, thread or process,
and a stalled viewer cannot hold it up. The diagram's arrows are written down
in flow.py, and `probes.py --check` holds them to the code.

IT CHANGES NO PIPELINE CODE
---------------------------
Two sources, both outside the pipeline:

  inventory.py   the code itself, read as an AST, so "everything that is
                 implemented" is generated rather than typed and cannot drift
  runner.py      any scrub3d script, run in a subprocess with probes wrapped
                 around its call boundaries, writing a run directory

The console process never imports a pipeline module. It reads run directories,
the AST and the filesystem, and it starts jobs. A pipeline bug therefore cannot
take the console down, and the console cannot initialise CUDA or open the camera
behind a running job's back.

IT NEVER COMMANDS HARDWARE
--------------------------
There is no arm button and no estop button: a web page is not a safety device,
and the governor is the only route to an arm. Inside a console job, opening a
serial port raises unless the run was started with --allow-hardware, which the
console never passes.
"""
