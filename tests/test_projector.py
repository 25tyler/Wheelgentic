import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import watchdog; watchdog.arm(150)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve; serve.ensure()          # tests must not depend on a shared server
import asyncio, sys
BAD = []


def _ink_x(key, rect, m):
    """The x-range the element's WORDS actually paint, not its line box.

    A Range over the text node reports the inked box when it returns
    non-zero dimensions, which is why the page-side helper prefers it
    over getBoundingClientRect: #title (195px of text) measures 1855px
    wide as an element and "overlaps" #link in the opposite corner.

    DO NOT ESTIMATE THE WIDTH FROM THE CHARACTER COUNT. This read
    `min(w, fontSize * 0.62 * len(text))` on the theory that the face is
    monospace and the Range might still be a full-width line box. The
    face is letter-spaced: CLEANLINESS paints x 32..267 at 1080p where
    that formula predicts 32..179. Measured against painted pixels, the
    estimate was 88px too narrow at 1080p and ~71px at the other two
    framings -- so the collision band was smaller than the text and the
    guard could miss a real intrusion. The Range itself was right all
    along (x 32..270 against 267 painted, 3px of slack); the estimate
    was the only thing throwing that away.
    """
    if not rect:
        return None
    return (rect["x"], rect["x"] + rect["w"])
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True,args=["--use-angle=metal","--enable-unsafe-swiftshader"])
        # The keys that move the camera, in demo order. `r` at the end returns
        # to the wide shot, so the loop leaves the page as it found it.
        SHOT_KEYS = ["b", "s", "8", "9", "0", "r"]
        for w,h,name in [(1920,1080,"projector_1080p"),(1280,720,"projector_720p"),(1024,768,"projector_4x3")]:
            pg=await b.new_page(viewport={"width":w,"height":h})
            errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
            await pg.goto("http://localhost:8000/",wait_until="load")
            await pg.wait_for_timeout(2200)
            await pg.keyboard.press("Enter"); await pg.wait_for_timeout(1200)
            # EVERY SHOT, NOT JUST THE WIDE ONE. main.js's shot list moves the
            # camera on `b`, `s` and each mode key, and this file's own history
            # is why that has to be checked: the last reframe put the feet
            # through the cleanliness bar and the robot's base off the right
            # edge, and nothing but these measurements saw it.
            #
            # The keys are pressed in the order the demo presses them, so each
            # shot is measured from the shot before it rather than from a
            # cold start -- which is the only state the projector will ever
            # actually be in. 2.6s is comfortably past the 2.2s scrub move.
            for _k in SHOT_KEYS:
                await pg.keyboard.press(_k)
                await pg.wait_for_timeout(2600)
            m=await pg.evaluate("""async ()=>{
              // THREE, because the checks below measure 3D objects against the
              // HUD. main.js's fit() records two faults the FOV 42->26 reframe
              // introduced -- the feet hitting the cleanliness bar and the
              // robot's base plate running off the right edge -- and every
              // assertion in this file compared HUD elements against EACH
              // OTHER, so a 3D object touching the HUD was invisible to all of
              // them. Both faults were found by screenshot and neither had a
              // standing guard.
              const THREE = await import('three');
              const S = window.__wheelgentic;
              const r=n=>{const e=document.getElementById(n);
                if(!e)return null;
                // TEXT extent via a Range, NOT the element box. A block
                // element's box spans the full width, so #title (195px of
                // text) measured 1855px wide and "overlapped" #link in the
                // opposite corner. Verified: box overlap true, text overlap
                // false. Measuring boxes produces false alarms that train you
                // to ignore the check.
                const rg=document.createRange(); rg.selectNodeContents(e);
                const t=rg.getBoundingClientRect();
                const b=(t.width>0&&t.height>0)?t:e.getBoundingClientRect();
                return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),
                        h:Math.round(b.height),
                        // The WORDS, so a caller can bound the ink inside this
                        // line box. Without it _ink_x() returns None, the leg
                        // check short-circuits false, and the guard passes by
                        // measuring nothing.
                        text:(e.textContent||'').trim(),
                        fs:parseFloat(getComputedStyle(e).fontSize)};};
              const c=document.querySelector('canvas').getBoundingClientRect();
              // ---- 3D vs HUD ---------------------------------------------
              // A leg is a leaf BONE with no geometry of its own: the vertices
              // live in one shared SkinnedMesh. Measure the vertices the bone
              // OWNS, via skin weights -- the same source avatar.js uses to
              // place the splotches, so this test and the code agree on where
              // the limb is. Box3 on the bone returns a degenerate box and
              // every coordinate comes back NaN, which compares FALSE against
              // any threshold: the check would pass while measuring nothing.
              const toY=v3=>{const v=v3.clone().project(S&&S.camera);
                return (1-(v.y+1)/2)*innerHeight;};
              const legLowest=(bname)=>{
                if(!S||!S.avatar||!S.avatar.node[bname]) return null;
                let sk=null; S.scene.traverse(o=>{if(o.isSkinnedMesh&&!sk)sk=o;});
                if(!sk) return null;
                const bone=S.avatar.node[bname];
                const bi=sk.skeleton.bones.indexOf(bone);
                if(bi<0) return null;
                sk.updateWorldMatrix(true,false);
                const g=sk.geometry, pos=g.attributes.position;
                const si=g.attributes.skinIndex, sw=g.attributes.skinWeight;
                const v=new THREE.Vector3();
                let n=0, maxY=-1e9, minX=1e9, maxX=-1e9;
                for(let k=0;k<pos.count;k++){
                  let best=-1,bw=0;
                  for(let c2=0;c2<4;c2++){const wq=sw.getComponent(k,c2);
                    if(wq>bw){bw=wq;best=si.getComponent(k,c2);}}
                  if(best!==bi||bw<0.5) continue;
                  v.fromBufferAttribute(pos,k).applyMatrix4(sk.matrixWorld);
                  const pv=v.clone().project(S.camera);
                  const y=(1-(pv.y+1)/2)*innerHeight;
                  const x=(pv.x+1)/2*innerWidth;
                  if(!Number.isFinite(x)||!Number.isFinite(y)) continue;
                  n++; maxY=Math.max(maxY,y);
                  minX=Math.min(minX,x); maxX=Math.max(maxX,x);
                }
                return n ? {n, y1:Math.round(maxY),
                            x0:Math.round(minX), x1:Math.round(maxX)} : {n:0};
              };
              let robot=null;
              if(S&&S.robot&&S.robot.root){
                const bb=new THREE.Box3().setFromObject(S.robot.root);
                const pr=[bb.min,bb.max].map(q=>{const v=q.clone().project(S.camera);
                  return [(v.x+1)/2*innerWidth,(1-(v.y+1)/2)*innerHeight];});
                const xs=[pr[0][0],pr[1][0]], ys=[pr[0][1],pr[1][1]];
                robot={x0:Math.round(Math.min(...xs)),x1:Math.round(Math.max(...xs)),
                       y0:Math.round(Math.min(...ys)),y1:Math.round(Math.max(...ys)),
                       finite:xs.every(Number.isFinite)&&ys.every(Number.isFinite)};
              }
              return {pct:r('pct'),bar:r('bar'),title:r('title'),label:r('label'),
                      priv:r('privacy'),link:r('link'),cycle:r('cycle'),
                      legL:legLowest('leg-left'), legR:legLowest('leg-right'),
                      robot:robot, hasHandle:!!S,
                      canvas:{w:Math.round(c.width),h:Math.round(c.height)},
                      vw:innerWidth,vh:innerHeight};}""")
            over = []
            for k in ("pct","bar","title","label","priv","link"):
                e=m[k]
                if not e: over.append(f"{k}:MISSING"); continue
                if e["x"]<0 or e["y"]<0 or e["x"]+e["w"]>m["vw"]+1 or e["y"]+e["h"]>m["vh"]+1:
                    over.append(f"{k}@({e['x']},{e['y']},{e['w']}x{e['h']})")
            # OVERLAP CHECK. The offscreen test reported "none" at 1080p while
            # CLEANLINESS and 0% were visibly on top of each other. Bounding
            # boxes must not intersect.
            def rects(*ks):
                return [(k, m[k]) for k in ks if m[k]]
            ov = []
            # 'cycle' belongs here. It was the one HUD element left out of the
            # overlap set, so IDLE sitting on the privacy line was a question
            # no assertion could answer.
            rs = rects("pct","bar","title","label","priv","link","cycle")
            for i,(ka,a) in enumerate(rs):
                for kb,bb in rs[i+1:]:
                    if (a["x"] < bb["x"]+bb["w"] and bb["x"] < a["x"]+a["w"] and
                        a["y"] < bb["y"]+bb["h"] and bb["y"] < a["y"]+a["h"]):
                        ov.append(f"{ka}~{kb}")
            # ---- 3D vs HUD, the half this file never checked ---------------
            # BOTH AXES, ALWAYS. #label's text extent is a FULL-WIDTH line box
            # (x 22..1258 at 720p) while CLEANLINESS paints only at x 20..240,
            # so a y-only test reports the legs colliding with a label they
            # never share a column with. Measured: lowest shoe pixel 563,
            # topmost label ink 576 -- 13px of clear space.
            if not m.get("hasHandle"):
                over.append("no __wheelgentic handle: 3D checks measured nothing")
            for lk in ("legL","legR"):
                e = m.get(lk)
                if e is None:
                    over.append(f"{lk}:UNMEASURABLE"); continue
                if not e.get("n"):
                    over.append(f"{lk}: zero weighted vertices, probe is blind")
                    continue
                for hk in ("label","pct","bar"):
                    hb = m.get(hk)
                    if not hb: continue
                    ink = _ink_x(hk, hb, m)
                    if ink is None:
                        over.append(f"{hk}: no text, ink band unmeasurable")
                        continue
                    if (e["y1"] > hb["y"] and
                            e["x1"] > ink[0] and e["x0"] < ink[1]):
                        over.append(f"{lk} y1={e['y1']} into {hk} "
                                    f"y={hb['y']} ink x {ink[0]:.0f}..{ink[1]:.0f}")
            rb = m.get("robot")
            if rb is None:
                over.append("robot:UNMEASURABLE")
            elif not rb.get("finite"):
                over.append("robot box not finite, probe is blind")
            elif rb["x1"] > m["vw"]:
                over.append(f"robot base plate off the right edge: "
                            f"x1={rb['x1']} > vw={m['vw']}")
            print(f"{name} {w}x{h}:")
            print(f"   pct font {m['pct']['fs']}px  ({m['pct']['fs']/h*100:.1f}% of height)")
            print(f"   privacy font {m['priv']['fs']}px  ({m['priv']['fs']/h*100:.2f}% of height)")
            print(f"   bar {m['bar']['w']}x{m['bar']['h']}  canvas {m['canvas']['w']}x{m['canvas']['h']}")
            # PRINT WHAT THE 3D CHECKS MEASURED, not just their verdict.
            # "offscreen: none" reads identically whether the checks ran and
            # found nothing or never fired at all -- and three separate probes
            # for this landing passed by measuring nothing (Box3 on a bone
            # returns NaN; the rect helper had no `text` so the ink band was
            # None and the comparison short-circuited). A guard whose operands
            # are invisible cannot be audited.
            _lb = m.get("label") or {}
            _ink = _ink_x("label", _lb, m) if _lb else None
            _ll, _lr, _rb = m.get("legL"), m.get("legR"), m.get("robot")
            print(f"   legs verts {(_ll or {}).get('n')}/{(_lr or {}).get('n')}"
                  f"  lowest y {(_ll or {}).get('y1')}/{(_lr or {}).get('y1')}"
                  f"  x {(_ll or {}).get('x0')}..{(_ll or {}).get('x1')}")
            print(f"   label box y={_lb.get('y')} x={_lb.get('x')}..{_lb.get('x',0)+_lb.get('w',0)}"
                  f"  INK x {f'{_ink[0]:.0f}..{_ink[1]:.0f}' if _ink else None}"
                  f"  clearance {(_lb.get('y',0) - max((_ll or {}).get('y1',0), (_lr or {}).get('y1',0)))}px")
            print(f"   robot x {(_rb or {}).get('x0')}..{(_rb or {}).get('x1')}"
                  f"  right margin {m['vw'] - (_rb or {}).get('x1', m['vw'])}px"
                  f"  finite={(_rb or {}).get('finite')}")
            print(f"   offscreen: {over or 'none'}  overlap: {ov or 'none'}"
                  f"  errors: {errs or 'none'}")
            if over or ov or errs:
                BAD.append((name, over, ov, errs))
            # the counter must be a readable fraction of screen height
            if m['pct']['fs'] / h < 0.06:
                BAD.append((name, f"pct font only {m['pct']['fs']/h*100:.1f}% of height", [], []))
            await pg.screenshot(path=f"/tmp/shot_{name}.png")
            await pg.close()
        await b.close()
    if BAD:
        print("\n*** PROJECTOR LEGIBILITY FAILURES ***")
        for x in BAD: print("   ", x)
        sys.exit(1)
    print("\nPROJECTOR CHECKS PASSED (1080p / 720p / 4:3)")
asyncio.run(main())
