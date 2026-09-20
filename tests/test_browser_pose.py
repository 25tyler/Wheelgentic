"""tests/test_browser_pose.py — the CARTOON MIRRORS A PERSON.

The browser runs its own MediaPipe against its own camera. That path has never
been exercised: every previous browser test ran with no camera, so the avatar
only ever played its idle clip.

Chrome's --use-fake-device-for-media-stream feeds a synthetic moving pattern to
getUserMedia. It is not a person, so a pose may not be detected -- but the
loading, wiring and orientation-assert path IS exercised, and if a pose IS
found the limb quaternions must actually change.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import watchdog; watchdog.arm(150)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve; serve.ensure()          # tests must not depend on a shared server
import asyncio, sys
from playwright.async_api import async_playwright

BAD = []
def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else '*** FAIL':>9}  {label}" + (f"  [{detail}]" if detail else ""))
    if not cond: BAD.append(label)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True, args=[
            "--use-angle=metal", "--enable-unsafe-swiftshader",
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream"])
        pg = await b.new_page(viewport={"width": 1280, "height": 720},
                              permissions=["camera"])
        errs, logs = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)[:120]))
        pg.on("console", lambda m: logs.append(m.text))
        await pg.goto("http://localhost:8000/", wait_until="load")
        await pg.wait_for_timeout(2000)
        await pg.keyboard.press("Enter")          # unlocks audio + starts pose
        await pg.wait_for_timeout(6000)           # wasm + model load

        st = await pg.evaluate("""()=>{
          const v = document.getElementById('cam');
          return {
            videoReady: !!(v && v.readyState >= 2),
            videoSize: v ? [v.videoWidth, v.videoHeight] : null,
            hasStream: !!(v && v.srcObject),
          };}""")
        check("getUserMedia delivered a stream", st["hasStream"], str(st))
        check("video is playing", st["videoReady"], str(st["videoSize"]))

        poseUp = any("browser pose up" in l for l in logs)
        check("browser MediaPipe initialised", poseUp,
              "console: " + str([l[:50] for l in logs if 'pose' in l.lower()][:2]))

        # Did the landmarker actually run, and did the avatar move?
        moved = await pg.evaluate("""()=>new Promise(res=>{
          const S = window.__wheelgentic;
          if(!S || !S.avatar || !S.avatar.node['arm-left'])
            return res({err:'no avatar handle'});
          // SNAPSHOT THE VALUES, NOT A LIVE REFERENCE. `q` is the live
          // quaternion object; reading q.x into `a` and again into `b` 2.5s
          // later compared the object to ITSELF and reported delta=0 forever.
          // The probe printed a confident 0 the whole time the avatar was
          // moving perfectly well -- a dead instrument that looked like data.
          const q = S.avatar.node['arm-left'].quaternion;
          const a = [q.x, q.y, q.z, q.w].slice();
          setTimeout(()=>{
            const b=[q.x,q.y,q.z,q.w].slice();
            const d=Math.max(...a.map((v,i)=>Math.abs(v-b[i])));
            res({delta:d, before:a.map(v=>+v.toFixed(4)),
                 after:b.map(v=>+v.toFixed(4))});
          }, 2500);
        })""")
        print(f"    arm-left quaternion delta over 2.5s: {moved.get('delta')}")
        # ASSERT IT. This was print-only, so when it read 0 for 2.5 seconds
        # nothing failed. The idle sway alone should move the limb; a frozen
        # avatar in front of a live camera is exactly the failure this file
        # exists to catch.
        check("the avatar is ALIVE (limb moves over 2.5s)",
              (moved.get("delta") or 0) > 1e-4,
              f"delta={moved.get('delta')} — nothing moved at all")

        # The orientation assert must have RUN if any pose was seen.
        oriented = [l for l in logs if "orientation" in l.lower()
                    or "UPSIDE DOWN" in l]
        if oriented:
            print(f"    orientation check fired: {oriented[0][:80]}")
            check("avatar is NOT upside down",
                  not any("UPSIDE DOWN" in l for l in oriented),
                  "shoulders must be above hips after axis conversion")
        else:
            print("    no pose detected from the fake video device (expected:")
            print("    Chrome's synthetic stream is a rolling pattern, not a")
            print("    person). The LOADING path is still proven above.")

        # ---- PROVE THE MIRRORING with injected landmarks ------------------
        # Chrome's fake device is a rolling pattern, not a person, so no real
        # pose is found. Drive avatar.update() with landmarks shaped exactly
        # like MediaPipe's worldLandmarks (metres, hip-origin, +Y DOWN) and
        # assert the limb actually tracks them.
        print("\n  -- injected-landmark mirroring --")
        mir = await pg.evaluate("""()=>{
          const S=window.__wheelgentic, A=S.avatar;
          // 33 landmarks; only the ones avatar.js reads need to be right.
          const mk=(x,y,z)=>({x,y,z,visibility:0.99});
          const L=[]; for(let i=0;i<33;i++) L.push(mk(0,0,0));
          L[11]=mk(-0.18,-0.55,0);  L[12]=mk(0.18,-0.55,0);   // shoulders
          L[23]=mk(-0.12, 0.00,0);  L[24]=mk(0.12, 0.00,0);   // hips
          const out={};
          // LET THE SPRING SETTLE. The limb no longer snaps to the tracked
          // direction -- it chases it through an underdamped spring, so ONE
          // 16ms frame moves it about 0.004 and back-to-back update() calls
          // measured almost nothing. That is the physics working, not the
          // pose path breaking: the old test assumed instant snapping.
          //
          // Settling also tests something better than a single frame did --
          // that the spring CONVERGES on the tracked pose instead of
          // oscillating forever or drifting somewhere else.
          const settle = (t0) => { for (let i=0;i<90;i++) A.update(L,0.016,t0+i*0.016); };
          // pose A: left forearm hanging straight down
          L[13]=mk(-0.20,-0.30,0); L[15]=mk(-0.20,-0.05,0);
          settle(1.0);
          const qa=A.node['arm-left'].quaternion.clone();
          // pose B: same forearm swung out horizontally
          L[13]=mk(-0.20,-0.30,0); L[15]=mk(-0.45,-0.30,0);
          settle(3.0);
          const qb=A.node['arm-left'].quaternion.clone();
          out.delta = Math.max(Math.abs(qa.x-qb.x),Math.abs(qa.y-qb.y),
                               Math.abs(qa.z-qb.z),Math.abs(qa.w-qb.w));
          out.qa=[qa.x,qa.y,qa.z,qa.w].map(v=>+v.toFixed(3));
          out.qb=[qb.x,qb.y,qb.z,qb.w].map(v=>+v.toFixed(3));
          // where does the limb TIP end up in world space for each pose?
          return out;
        }""")
        print(f"    hanging  -> quaternion {mir.get('qa')}")
        print(f"    swung out-> quaternion {mir.get('qb')}")
        check("the cartoon's arm TRACKS the injected landmarks",
              mir.get("delta", 0) > 0.15,
              f"quaternion moved {mir.get('delta',0):.3f} between two poses")

        oriented2 = [l for l in logs if "orientation OK" in l or "UPSIDE DOWN" in l]
        check("orientation assert ran and passed (shoulders above hips)",
              any("orientation OK" in l for l in oriented2),
              str(oriented2[:1]) or "assert never fired")

        # ---- UV mode moves splotches to the DETECTED tracer positions ----
        # UV latches wherever the tracer actually landed, but the page places
        # splotches at fixed SPLOTCH_TS and matches within 0.07 -- so tracer
        # at e.g. 0.42 fired an event the page silently DROPPED and the
        # counter climbed while the splotch stayed on screen.
        print("\n  -- UV splotch placement --")
        uv = await pg.evaluate("""()=>new Promise(res=>{
          const S=window.__wheelgentic;
          const before=S.recs.map(r=>+r.t.toFixed(2));
          S.placeSplotches([0.28,0.42,0.71]);
          setTimeout(()=>{
            const after=S.recs.map(r=>+r.t.toFixed(2));
            S.popNearest(0.42);
            setTimeout(()=>res({before,after,
              pct:document.getElementById('pct').textContent,
              gone:S.recs.filter(x=>x.gone).length}),900);
          },400);
        })""")
        print(f"    {uv['before']} -> {uv['after']}")
        check("splotches move to the detected tracer positions",
              uv["after"] == [0.28, 0.42, 0.71], str(uv["after"]))
        check("a pop at a DETECTED position registers",
              uv["gone"] == 1 and uv["pct"] != "0%",
              f"{uv['pct']}, {uv['gone']}/3 gone")
        S_reset = await pg.evaluate("()=>{window.__wheelgentic.resetAll();return 1}")

        print("\n=== THE BAKED ANIMATIONS ACTUALLY PLAY ===")
        # The character GLB ships 32 authored clips and for a long time exactly ONE was
        # used, as a tracking-loss fallback -- an animation library bought and left in
        # the box. Wiring playOnce() was not enough: stepSpring() writes .quaternion on
        # every part every frame, so it overwrote the mixer's output and the one-shots
        # were DEAD ON ARRIVAL. Measured: 'emote-yes' moved the head by 0.0042, which
        # is the breath sway and nothing else.
        #
        # A running clip now owns the torso/head/legs while the ARMS keep their springs,
        # because the arms must stay honest to the tracked person -- the pitch is "it is
        # tracking her actual forearm", and a clip that stole the arms would break it.
        _an = await pg.evaluate("()=>window.__wheelgentic.avatar.animationNames||[]")
        check("the model ships its animation library", len(_an) >= 20, f"{len(_an)} clips")
        check("emote-yes is present (the finale beat)", "emote-yes" in _an)
        check("emote-no is present (the estop/abort beat)", "emote-no" in _an)

        _Q = ("()=>{let h=null;window.__wheelgentic.scene.traverse(o=>{if(o.name==='head')h=o;});"
              "return [h.quaternion.x,h.quaternion.y,h.quaternion.z,h.quaternion.w];}")
        _a = await pg.evaluate(_Q)
        await pg.evaluate("()=>window.__wheelgentic.avatar.playOnce('emote-yes')")
        await pg.wait_for_timeout(450)
        _b = await pg.evaluate(_Q)
        _d = max(abs(x - y) for x, y in zip(_a, _b))
        check("a one-shot clip MOVES the character", _d > 0.02,
              f"head delta {_d:.4f} — the springs are overwriting the mixer")

        # ...and the arms are still the person's arms while it plays.
        _arm = await pg.evaluate("""()=>{const A=window.__wheelgentic.avatar;
          const mk=(x,y,z)=>({x,y,z});
          const pose=(wr)=>{const L=[]; for(let i=0;i<33;i++) L.push(mk(0,0,0));
            L[11]=mk(-.2,.4,0);L[12]=mk(.2,.4,0);L[23]=mk(-.1,0,0);L[24]=mk(.1,0,0);
            L[13]=mk(-.2,.1,0);L[15]=wr; return L;};
          const settle=(L,t0)=>{for(let i=0;i<90;i++) A.update(L,0.016,t0+i*0.016);};
          const q=()=>A.node['arm-left'].quaternion.clone();
          const dist=(a,b)=>+Math.max(Math.abs(a.x-b.x),Math.abs(a.y-b.y),
                                      Math.abs(a.z-b.z),Math.abs(a.w-b.w)).toFixed(3);
          // How far does the arm SWING when the tracked wrist moves, with a
          // clip running the whole time? If the clip owns the arm, the tracked
          // change cannot reach it and the swing collapses toward zero.
          settle(pose(mk(-.2,-.2,0)), 1);
          A.playOnce('emote-yes');
          settle(pose(mk(-.2,-.2,0)), 5); const before=q();
          settle(pose(mk(-.5,.1,0)), 9);  const after=q();
          return {swing: dist(before, after)};}""")
        # NOT just "the arm moved" -- a clip animating the arm moves it too, and
        # that weaker assertion PASSED a plant that handed the clip the arms
        # outright. Compare the arm's settled pose against the same pose reached
        # with NO clip running: if the clip owns the arm, the two disagree.
        # NOT "the arm moved" -- a clip animating the arm moves it too, and that
        # weaker assertion PASSED a plant that handed the clip the arms outright.
        # Nor "how far from a clean reference" -- measured, the STOLEN case drifts
        # LESS (0.054) than correct code (0.114), so that metric is backwards.
        # What actually differs: can a CHANGE in the tracked wrist still swing
        # the arm while a clip runs? If the clip owns it, the swing collapses.
        check("a tracked-pose change still swings the arm while a clip plays",
              _arm["swing"] > 0.2,
              f"swing {_arm['swing']} — the clip is overriding the tracked limb")

        check("an unknown clip name is safe, not fatal",
              await pg.evaluate("()=>window.__wheelgentic.avatar.playOnce('no-such-clip')") is False,
              "a missing clip must never take the projector down")

        print("\n=== THE OPERATOR'S ANIMATION CONTROLS ===")
        # 'e' cycles reaction clips and 'd' is the death gag. These exist so the
        # operator can get a laugh on demand and prove the cartoon is a live rig
        # rather than a video -- which is the doubt a judge actually has.
        #
        # MEASURE THE WHOLE SKELETON, not the head. My first probe read the head
        # quaternion, so clips that animate an ARM or the ROOT ('interact-right',
        # 'jump') looked dead when they were fine.
        _SK = """()=>{const S=window.__wheelgentic;let sk=null;
          S.scene.traverse(o=>{if(o.isSkinnedMesh&&!sk)sk=o;});
          return sk.skeleton.bones.flatMap(b=>[b.quaternion.x,b.quaternion.y,
            b.quaternion.z,b.quaternion.w,b.position.x,b.position.y,b.position.z]);}"""
        for _clip in ("emote-yes", "emote-no", "interact-right", "pick-up",
                      "attack-kick-right", "jump", "die"):
            _a = await pg.evaluate(_SK)
            await pg.evaluate("(n)=>window.__wheelgentic.avatar.playOnce(n)", _clip)
            await pg.wait_for_timeout(300)
            _b = await pg.evaluate(_SK)
            _d = max(abs(x - y) for x, y in zip(_a, _b))
            check(f"clip '{_clip}' moves the skeleton", _d > 0.05, f"delta {_d:.3f}")
            await pg.wait_for_timeout(700)

        # playOnce ONLY WORKED ONCE: mixer.clipAction(clip) returns the SAME
        # action object, so fadeOut(prev)+fadeIn(next) cancelled out. Measured,
        # the first call moved the skeleton 2.000 and the next two 0.033/0.025.
        # Fire the SAME clip twice -- the second must still move it.
        # RAPID SUCCESSION is the failing condition, not "twice eventually".
        # A 900ms gap lets the first clip finish, so the cross-fade bug cannot
        # show -- that version of this test PASSED the plant. The real symptom
        # was pressing 'e' three times quickly and seeing nothing after the
        # first, so interrupt a RUNNING clip.
        # THE SAME CLIP, RE-FIRED MID-PLAY -- measured by the ACTION'S OWN
        # CLOCK, not by how much the pose changed.
        #
        # Three earlier versions of this check were decorative. Comparing poses
        # 120ms apart measured the clip's natural progression, so it read 0.66
        # whether or not the restart worked -- removing reset(), removing
        # setEffectiveWeight, and reverting to the broken cross-fade ALL still
        # "passed". A restart means action.time goes BACKWARDS; that is the
        # only thing that actually distinguishes it.
        _T = ("()=>{const S=window.__wheelgentic;"
              "return S.avatar.oneShotTime ? S.avatar.oneShotTime() : null;}")
        await pg.evaluate("()=>window.__wheelgentic.avatar.playOnce('die')")
        await pg.wait_for_timeout(320)
        _t1 = await pg.evaluate(_T)
        await pg.evaluate("()=>window.__wheelgentic.avatar.playOnce('die')")
        await pg.wait_for_timeout(60)
        _t2 = await pg.evaluate(_T)
        check("the SAME clip RESTARTS when re-fired (rapid 'e' presses)",
              _t1 is not None and _t2 is not None and _t2 < _t1,
              f"action.time went {_t1} -> {_t2}; a restart must go BACKWARDS")

        # CAPS LOCK MUST NOT DISABLE THE OPERATOR KEYS. With caps lock on,
        # e.key is 'D' and a lowercase comparison silently does nothing -- the
        # same trap that made shift+C dead on a real keyboard while Playwright's
        # synthetic events hid it. e.code is the PHYSICAL key and ignores both
        # caps lock and shift. Plant-verified: reverting one key to e.key ==='d'
        # makes Shift+D stop firing.
        for _key, _label in (("Shift+D", "the death gag"),
                             ("Shift+E", "the emote cycle")):
            _a = await pg.evaluate(_SK)
            await pg.keyboard.press(_key)
            await pg.wait_for_timeout(400)
            _b = await pg.evaluate(_SK)
            _d = max(abs(x - y) for x, y in zip(_a, _b))
            check(f"{_key} still fires {_label} (caps-lock case)", _d > 0.05,
                  f"delta {_d:.3f}"
                  + ("" if _d > 0.05 else " — DEAD with caps lock on"))
            await pg.wait_for_timeout(800)

        # THE DEATH GAG MUST STAY DOWN -- AND BE RECOVERABLE.
        # 'die' is the one clip whose whole joke is lying there flat. With
        # clampWhenFinished off for every one-shot it teleported upright the
        # frame the clip ended: torso deviation from upright measured 0.2926 at
        # t+0.36s and 0.0000 at t+0.48s, the very next sample. 120ms is not a
        # beat. So 'die' -- and ONLY 'die' -- clamps its last frame; an emote is
        # a reaction and must hand the body back to the springs.
        #
        # Clamping is a state nothing else cleared, and that is the half worth
        # guarding: 'r' is the key THE RECOVERY CARD documents as the reset, and
        # it left the character face-down at 0.2978 until resetAll() learned to
        # call standUp(). An operator who triggers the gag and reaches for the
        # documented reset must get their character back.
        _Q = ("()=>{const n=window.__wheelgentic.avatar.node.torso;"
              "n.updateWorldMatrix(true,false);"
              "const q=n.getWorldQuaternion(new "
              "(window.__wheelgentic.camera.quaternion.constructor)());"
              "return [q.x,q.y,q.z,q.w];}")
        await pg.evaluate("()=>window.__wheelgentic.avatar.standUp&&"
                          "window.__wheelgentic.avatar.standUp()")
        await pg.wait_for_timeout(900)
        _up = await pg.evaluate(_Q)

        def _dev(q):
            return round(1 - abs(sum(a * b for a, b in zip(_up, q))), 4)

        await pg.keyboard.press("d")
        await pg.wait_for_timeout(2600)        # well past the clip's end
        _flop = _dev(await pg.evaluate(_Q))
        check("'d' flops the character AND HOLDS the pose", _flop > 0.05,
              f"deviation {_flop:.4f}"
              + ("" if _flop > 0.05
                 else " — SNAPPED UPRIGHT; the gag has no beat to land in"))
        # Recovery, both documented ways. Without these the gag is a trap: a
        # permanently face-down character with no way back mid-demo.
        await pg.keyboard.press("e")
        await pg.wait_for_timeout(2600)
        _afterE = _dev(await pg.evaluate(_Q))
        check("an emote stands them back up", _afterE < 0.05,
              f"deviation {_afterE:.4f} — still face-down after 'e'")
        await pg.keyboard.press("d")
        await pg.wait_for_timeout(2600)
        _flop2 = _dev(await pg.evaluate(_Q))
        check("'d' still replays after a recovery", _flop2 > 0.05,
              f"deviation {_flop2:.4f}")
        await pg.keyboard.press("r")
        await pg.wait_for_timeout(2200)
        _afterR = _dev(await pg.evaluate(_Q))
        check("and 'r' -- the DOCUMENTED reset -- stands them up too",
              _afterR < 0.05,
              f"deviation {_afterR:.4f}"
              + ("" if _afterR < 0.05
                 else " — the recovery card's reset leaves them face-down"))
        # The clamp must be SELECTIVE. If every one-shot clamped, an emote
        # would freeze the character on its last frame and the springs would
        # never get the body back -- which the 'e' check above would still
        # pass, because 'e' also happens to end near upright.
        _av = open("web/avatar.js").read()
        check("only 'die' clamps, not every one-shot",
              "clampWhenFinished = (name === 'die')" in _av,
              "a blanket clamp freezes every emote on its last frame")

        # The cast is what 'v' cycles. One entry means the key does nothing.
        _cast = await pg.evaluate("()=>window.__wheelgentic.avatar.cast||[]")
        check("there is a cast to cycle", len(_cast) >= 2, f"{len(_cast)} characters")

        # 'v' MUST ACTUALLY ADVANCE, not merely have a handler. The docs guard
        # asserts `e.code === 'KeyV'` exists, which would pass if the body were
        # deleted -- the same vacuity that let the README key table drift.
        #
        # WAIT FOR A REAL DOCUMENT TURNOVER, NEVER A TIMEOUT. Four probes of
        # this key disagreed (7, 7, 1, 7 unique of 12) because each trusted
        # milliseconds: 'v' writes localStorage then reloads after 700ms, so a
        # second press landing inside that window recomputes indexOf(current)+1
        # from a STALE current and rewrites the value it just wrote. Stamp a
        # marker, press, and refuse to sample until the marker is GONE -- that
        # is proof of a new document rather than a guess about timing.
        _start = await pg.evaluate("()=>window.__wheelgentic.avatar.current")
        await pg.evaluate("()=>{window.__VMARK = 'x';}")
        await pg.keyboard.press("v")
        _turned = False
        for _ in range(60):
            await pg.wait_for_timeout(200)
            try:
                if not await pg.evaluate("()=>window.__VMARK === 'x'"):
                    _turned = True
                    break
            except Exception:
                _turned = True          # navigation tore down the context
                break
        check("'v' actually reloads the page", _turned,
              "no document turnover — the key did nothing")
        _after = None
        for _ in range(60):
            _after = await pg.evaluate("()=>window.__wheelgentic?.avatar?.current")
            if _after:
                break
            await pg.wait_for_timeout(200)
        check("'v' advances to a DIFFERENT character",
              _after is not None and _after != _start,
              f"{_start} -> {_after}")
        check("and it lands on a real cast member", _after in _cast, str(_after))
        # The next one in the list, not an arbitrary jump.
        check("it advances by exactly one",
              _after == _cast[(_cast.index(_start) + 1) % len(_cast)],
              f"expected {_cast[(_cast.index(_start)+1)%len(_cast)]}, got {_after}")

        check("no page errors during the pose path", not errs, str(errs[:2]))

        # The render loop must survive whatever the landmarker did.
        frames = await pg.evaluate("""()=>new Promise(r=>{
          const R=window.__wheelgentic.renderer; const a=R.info.render.frame;
          setTimeout(()=>r(R.info.render.frame-a),1000);})""")
        check("render loop alive with pose running", frames > 30,
              f"{frames} app frames/s")
        await pg.close(); await b.close()

asyncio.run(main())
print("\n" + "="*58)
if BAD:
    print(f"  *** {len(BAD)} FAILED: {BAD}"); sys.exit(1)
print("  BROWSER POSE PATH PASSED")
