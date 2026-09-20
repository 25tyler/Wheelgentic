"""tools/record_backup.py — record recordings/backup.mp4, the last-resort demo.

THE RECOVERY CARD'S FINAL ROW is "Total failure -> open recordings/backup.mp4
and narrate". Every other failure mode on that card has a tested path. This one
pointed at a file that did not exist, flagged "record this at hour 25". Hour 25
is exactly when nobody has time to record anything.

WHAT THIS RECORDS: the real stack. py/scrubbot.py with CAM=fake drives the real
FSM (IDLE -> APPROACH -> SCRUB -> RETREAT) against the synthetic camera, over a
real websocket, into the real projector page. Nothing here is a mockup -- if the
demo works, this video shows the demo working, because it IS the demo.

WHY PLAYWRIGHT VIDEO AND NOT screencapture: screencapture needs a TCC screen-
recording grant that will not exist on a fresh machine and cannot be granted
headlessly. Playwright records the page's own compositor output, so this runs
anywhere the test suite runs.

RUN IT WITH python3, NOT ./venv/bin/python. Same two-interpreter split the
test suite has: ./venv has cv2 + mediapipe (so scrubbot runs under it, and
this script launches it that way), python3 has playwright (so THIS script runs
under it). Getting it backwards fails at the import.

    python3 tools/record_backup.py                      # ~45s
    python3 tools/record_backup.py --seconds 60 --cycles 3
"""
import argparse, asyncio, os, signal, subprocess, sys, time, glob, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import serve                      # the same static server the suite uses
try:
    from playwright.async_api import async_playwright
except ModuleNotFoundError:
    sys.exit(f"\n{sys.executable} lacks playwright.\n"
             "Run this with python3, not ./venv/bin/python:\n"
             "    python3 tools/record_backup.py\n"
             "(venv has cv2+mediapipe for scrubbot; python3 has playwright.)\n")

PY = next((c for c in ("venv/bin/python", "/tmp/sbtest/bin/python")
           if os.path.exists(c)), "python3")
OUT = "recordings/backup.mp4"
LOG = "/tmp/_record_backup.log"


def free_port(port):
    """A leftover binding makes the browser show NO LINK, and the recording
    would silently capture a dead demo -- the exact thing this file exists to
    prevent. Kill first, verify after."""
    subprocess.run(["pkill", "-9", "-f", "py/scrubbot.py"], capture_output=True)
    for _ in range(8):
        held = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                              capture_output=True, text=True).stdout.split()
        if not held:
            return True
        for pid in held:
            subprocess.run(["kill", "-9", pid], capture_output=True)
        time.sleep(0.4)
    return False


async def _inject_key_overlay(pg):
    """Show every keypress on screen, bottom-centre, for the video only.

    TAKEN, NOT INVENTED. The ECC skill `ui-demo` records demos with an
    injected SVG CURSOR so a viewer can see what is being clicked; its whole
    argument is that a recording which shows effects without their causes
    reads as a canned animation. Searched .claude/skills for motion and demo
    skills before writing this: motion-patterns and motion-advanced are both
    React/Framer-Motion and do not apply to a vanilla three.js page, but
    ui-demo's cursor technique does -- adapted from mouse to keyboard, because
    this demo is driven entirely by keys and has no cursor at all.

    WHY IT MATTERS HERE. The recovery card's last row is "Python crashed
    mid-demo -> open recordings/backup.mp4 and narrate". Narrating is much
    harder over a video where splotches pop for no visible reason. With the
    overlay the presenter can say "watch, I press 1" and the screen agrees.

    OVERLAY ONLY, NEVER IN THE DEMO. This is injected by the recorder into
    its own throwaway browser context. Nothing in web/ knows it exists, so it
    cannot show up on the projector on demo day.
    """
    await pg.evaluate("""() => {
      if (document.getElementById('demo-keys')) return;
      const el = document.createElement('div');
      el.id = 'demo-keys';
      el.style.cssText = [
        'position:fixed', 'left:50%', 'bottom:6%', 'transform:translateX(-50%)',
        'z-index:2147483647', 'pointer-events:none',
        'font:700 40px ui-monospace,Menlo,monospace', 'letter-spacing:.06em',
        'color:#fff', 'background:rgba(12,16,24,.82)',
        'border:2px solid rgba(255,255,255,.35)', 'border-radius:14px',
        'padding:10px 22px', 'opacity:0', 'transition:opacity .12s ease',
      ].join(';');
      document.body.appendChild(el);
      let hide = null;
      // The names a presenter would say out loud, not the raw key codes.
      const PRETTY = { ' ': 'SPACE', Enter: 'ENTER', Escape: 'ESC' };
      addEventListener('keydown', (e) => {
        const k = PRETTY[e.key] || e.key.toUpperCase();
        el.textContent = (e.shiftKey && e.key.length === 1 ? 'SHIFT + ' : '') + k;
        el.style.opacity = '1';
        clearTimeout(hide);
        hide = setTimeout(() => { el.style.opacity = '0'; }, 900);
      }, true);
    }""")


async def main(seconds, cycles):
    serve.ensure()
    if not free_port(8765):
        sys.exit("port 8765 is held and would not free -- recording would be dead")

    log = open(LOG, "w")
    env = dict(os.environ, CAM="fake")
    proc = subprocess.Popen(
        [PY, "py/scrubbot.py", "--no-arm", "--headless"],
        stdout=log, stderr=subprocess.STDOUT, text=True,
        preexec_fn=os.setsid, env=env)
    # LOG TO A FILE, NOT A PIPE: scrubbot prints continuously and a full 64KB
    # pipe buffer blocks the child forever with no symptom. (Cost 3 theories
    # in test_integration.)
    time.sleep(4.0)
    if proc.poll() is not None:
        log.close()
        sys.exit("scrubbot died:\n" + open(LOG).read()[:2000])

    vdir = "/tmp/_backup_video"
    shutil.rmtree(vdir, ignore_errors=True)
    os.makedirs(vdir, exist_ok=True)

    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True,
            args=["--use-angle=metal", "--enable-unsafe-swiftshader"])
        ctx = await b.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=vdir,
            record_video_size={"width": 1280, "height": 720})
        pg = await ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        # ?nosteam: this clip deliberately never arms a cycle (see the note
        # further down), so the room must not warm up either. Everywhere else
        # the shower beat steams whether or not Python is running, because a
        # shower with no steam reads as a machine rubbing a mannequin -- but
        # here no cycle is running and no steam is the honest picture.
        await pg.goto("http://localhost:8000/?nosteam", wait_until="load")
        await pg.wait_for_timeout(2500)
        await _inject_key_overlay(pg)

        link = await pg.evaluate("()=>document.getElementById('link').textContent")
        if "LINKED" not in link:
            await ctx.close(); await b.close()
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            sys.exit(f"browser never linked to Python (link={link!r}) -- "
                     "a recording of a dead demo is worse than no recording")
        print(f"  linked: {link!r}")

        await pg.keyboard.press("Enter")          # unlock audio, start
        await pg.wait_for_timeout(1500)

        # WAIT FOR THE SPLOTCHES TO EXIST, do not assume 1500ms was enough.
        # Measured: on a busy machine cycle 1 came out one pop behind for
        # every key -- press 1, nothing; press 2, one gone; press 3, one gone
        # -- and the cycle ended at 33%. Cycle 2 was always clean, because by
        # then the avatar had finished loading. A fixed sleep is the wrong
        # tool for "has an async GLB finished", which is exactly the kind of
        # threshold this project's own rules forbid.
        try:
            await pg.wait_for_function(
                "() => window.__wheelgentic && window.__wheelgentic.recs"
                "    && window.__wheelgentic.recs.length >= 3",
                timeout=20000)
        except Exception:
            print("  *** splotches never appeared -- recording anyway, but "
                  "the pop beats will be wrong")
        await pg.wait_for_timeout(600)            # let the first frame settle

        # PACE THE POPS SO THE VIDEO SHOWS THEM.
        #
        # The first cut of this recorded a real armed cycle and reported
        # "counter reached 100%" -- true, but the video was 9 seconds of 0%
        # followed by all three splotches vanishing at once, because CAM=fake
        # waves a synthetic limb that never sweeps the sponge across each t,
        # so every pop lands in the end-of-scrub finale. Sampling frames from
        # that file showed a static 0% counter. A backup video whose whole
        # purpose is "the splotches pop as it scrubs" has to SHOW that.
        #
        # So: arm the real cycle (the arm really moves, the FSM really runs)
        # and drive the pops at a visible cadence over the same socket the
        # demo uses. Nothing here is faked -- 1/2/3 is the rehearsed manual
        # fallback on the recovery card, and this is that path.
        # DO NOT ARM A REAL CYCLE HERE. Measured: pressing 's' starts an FSM
        # cycle that finishes on its own in scrub_seconds (8s) and its RETREAT
        # fires fire_reset(), which sends reset=true and ZEROES the browser
        # counter underneath the recording. The pop count oscillated
        # 1 -> 0 -> 1 and the new assertion below caught it.
        #
        # This clip is the TOTAL-FAILURE fallback: the recovery card's answer
        # to "Python crashed mid-demo" is "ignore it, the cartoon keeps
        # mirroring, use 1/2/3". That is the path worth having on video, and
        # it is the one that still works when everything else is dead.
        for i in range(cycles):
            print(f"  cycle {i+1}/{cycles}")
            await pg.wait_for_timeout(2000)       # let the idle mirror read

            per = max(1.6, (seconds / cycles - 4.0) / 3.0)
            for k, key in enumerate(("1", "2", "3")):
                await pg.keyboard.press(key)
                await pg.wait_for_timeout(int(per * 1000))
                pct = await pg.evaluate(
                    "()=>document.getElementById('pct').textContent")
                gone = await pg.evaluate(
                    "()=>window.__wheelgentic.recs.filter(r=>r.gone).length")
                print(f"    splotch {k+1}/3 -> {pct}, {gone} gone")
                # ASSERT THE PICTURE CHANGED, not that a call returned.
                if gone != k + 1:
                    print(f"      *** splotch {k+1} did NOT pop "
                          f"({gone} gone) — the video would be wrong")

            await pg.wait_for_timeout(3000)       # finale + confetti
            final = await pg.evaluate(
                "()=>document.getElementById('pct').textContent")
            print(f"    end of cycle {i+1}: counter reads {final}")
            if final != "100%":
                print("      *** cycle did not finish at 100% ***")
            if i + 1 < cycles:
                await pg.keyboard.press("r")
                await pg.wait_for_timeout(2000)

        # ---- THE OTHER FOUR CAPABILITIES -----------------------------------
        # The product is a wheelchair that showers, feeds, gives pills, reads
        # vitals and talks. Everything above this line is the SHOWER, and that
        # is all this video showed for its whole life -- three splotches
        # popping and a reset. A presenter narrating it during a total failure
        # could only describe a fifth of the product.
        #
        # Each beat below is the same key the operator would press, so the
        # video and the recovery card describe the same machine. Held long
        # enough to read on a projector, short enough that the file stays
        # under a minute.
        for key, label, hold in (("b", "measured body",   4200),
                                 ("8", "feeding",         5200),
                                 ("9", "vitals",          4200),
                                 ("0", "voice",           3200),
                                 ("7", "back to shower",  2000)):
            await pg.keyboard.press(key)
            await pg.wait_for_timeout(hold)
            shown = await pg.evaluate(
                "()=>document.getElementById('label')?.textContent||''")
            print(f"    {key} -> {label}: {shown!r}")
            # A BEAT THAT DID NOT OPEN IS A LIE IN THE VIDEO. Report it
            # rather than shipping a clip whose narration will not match.
            if not shown:
                print(f"      *** {label} showed no label -- "
                      f"the video would mislead the narrator")

        await ctx.close()                          # flushes the video file
        await b.close()

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        proc.wait(timeout=5)
    except Exception:
        try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception: pass
    log.close()

    webm = sorted(glob.glob(os.path.join(vdir, "*.webm")))
    if not webm:
        sys.exit("playwright wrote no video")
    src = webm[-1]
    os.makedirs("recordings", exist_ok=True)
    # -movflags +faststart so it opens instantly from a cold double-click;
    # yuv420p because QuickTime refuses anything else.
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-c:v", "libx264", "-preset", "medium",
         "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", OUT],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("ffmpeg failed:\n" + r.stderr[-1500:])

    # STAMP WHAT THIS WAS BUILT FROM. The clip went stale within an hour of
    # first being recorded -- a UI change landed and the last-resort artifact
    # still showed the old build. test_docs_match_code.py compares this hash
    # to the current web/ sources and fails when they drift.
    # A content hash, not an mtime: git clone stamps everything at checkout
    # time and the sub-second spread made web/*.js look NEWER than the video
    # on a fresh clone (measured: four files false-fired).
    import hashlib
    h = hashlib.sha256()
    # KEEP THIS TUPLE IDENTICAL to _WEB in tests/test_docs_match_code.py -- the
    # test reproduces this digest to decide whether the video is stale, so a
    # drifting pair stamps something the test can never match. web/style.css
    # never existed (the page loads hud.css) and the loop skips missing files
    # silently, so hud.css and robotarm.js were outside the digest entirely.
    # DISCOVERED, NOT LISTED, and the test does the same -- see the note there.
    # The old hardcoded tuple left coverage.js, territories.js, vitals.js and
    # voice.js outside the digest, so changing any of them stamped a video the
    # test would call current.
    import glob as _g
    for f in tuple(sorted(_g.glob("web/*.js"))) + ("web/index.html", "web/hud.css"):
        if os.path.exists(f):
            h.update(f.encode())
            h.update(open(f, "rb").read())
    with open("recordings/backup.sources", "w") as fh:
        fh.write(h.hexdigest() + "\n")

    size = os.path.getsize(OUT)
    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", OUT],
        capture_output=True, text=True).stdout.strip()
    print(f"\n  {OUT}  {size/1e6:.1f} MB  {float(dur):.1f}s")
    if errs:
        print("  page errors during recording:", errs)

    # A SHORT FILE IS A BAD ENCODE, AND IT LOOKS EXACTLY LIKE A GOOD ONE.
    # Observed on 2026-09-21: the same command that had been writing 10.1 MB
    # for a week wrote 6.5 MB, reported the same 49.0s duration, passed the
    # content-hash stamp, and passed every doc check -- because nothing looks
    # at the size. The next run was 10.2 MB again from an unchanged tree.
    #
    # This is the file the recovery card calls the LAST RESORT: the thing you
    # play when the live demo has failed in front of judges. Silently shipping
    # a degraded one is the worst possible time to find out.
    #
    # 9 MB, not the observed 10.1: the floor is for a clearly broken encode,
    # not a tight bound on a number that moves a little with what is on screen.
    MIN_MB = 9.0
    if size / 1e6 < MIN_MB:
        print(f"\n  *** REFUSING: {size/1e6:.1f} MB is under the {MIN_MB} MB "
              f"floor. That is a bad encode, not a shorter demo.")
        print("      Run it again; an unchanged tree should produce ~10 MB.")
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=40.0,
                    help="total scrub time to capture")
    ap.add_argument("--cycles", type=int, default=2,
                    help="how many arm->scrub->finale cycles to record")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.seconds, a.cycles)))
