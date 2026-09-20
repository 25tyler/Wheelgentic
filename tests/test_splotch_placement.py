import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import watchdog; watchdog.arm(120)   # SIGALRM: a daemon-thread
# watchdog once let a test run for 3h21m and block a whole suite.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serve; serve.ensure()          # tests must not depend on a shared server
import asyncio, json
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(headless=True,args=["--use-angle=metal","--enable-unsafe-swiftshader"])
        pg=await b.new_page(viewport={"width":1280,"height":720})
        pg.on("pageerror",lambda e:print("ERR:",e))

        # EVERY CHARACTER, not just the one that happens to load. 'v' cycles
        # the whole cast and they do NOT place splotches identically: the bones
        # are byte-identical but the SKIN is not, and limbLocalBox() measures
        # the vertices a bone owns.
        import re as _re
        _cast = _re.findall(r"'([^']+)'",
                            _re.search(r"const CAST = \[([^\]]+)\]",
                                       open("web/avatar.js").read()).group(1))
        _failures = []
        for _who in _cast:
            print(f"\n--- {_who} ---")
            await pg.goto("http://localhost:8000/",wait_until="load")
            await pg.evaluate("(c)=>localStorage.setItem('wheelgentic.character',c)", _who)
            await pg.reload(wait_until="load")
            await pg.wait_for_timeout(2800)
            out=await pg.evaluate("""async ()=>{
          const THREE = await import('three');
          const S=window.__wheelgentic; if(!S) return {err:'no handle'};
          const toScreen=(v3)=>{const v=v3.clone().project(S.camera);
            return [Math.round((v.x+1)/2*innerWidth), Math.round((1-(v.y+1)/2)*innerHeight)];};
          const box=(o)=>{const bb=new THREE.Box3().setFromObject(o);
            return {min:toScreen(bb.min), max:toScreen(bb.max)};};
          const r={};
          r.armLeft   = box(S.avatar.node['arm-left']);
          r.armRight  = box(S.avatar.node['arm-right']);
          r.torso     = box(S.avatar.node['torso']);
          // The limb's OWN centre line in screen space -- the bounding box
          // cannot describe a limb that hangs at an angle.
          {
            // WORKS ON A SKINNED RIG. This read arm.geometry.computeBoundingBox(),
            // which is fine for the rigid pack but throws on the Kenney mini
            // model: 'arm-left' is a leaf BONE with no geometry of its own and
            // the vertices live in one shared SkinnedMesh. Symptom was
            // "Cannot read properties of undefined (reading
            // 'computeBoundingBox')".
            //
            // Measure the limb from THE VERTICES THE BONE OWNS, via skin
            // weights -- the same source avatar.js uses to place the splotches,
            // so the test and the code agree on where the limb is.
            const arm=S.avatar.node['arm-left'];
            let sk=null; S.scene.traverse(o=>{if(o.isSkinnedMesh&&!sk)sk=o;});
            const bi = sk ? sk.skeleton.bones.indexOf(arm) : -1;
            let mn, mx;
            if (bi >= 0) {
              sk.updateWorldMatrix(true,false);
              const g=sk.geometry, pos=g.attributes.position;
              const si=g.attributes.skinIndex, sw=g.attributes.skinWeight;
              const inv=arm.matrixWorld.clone().invert();
              const v=new THREE.Vector3();
              mn=new THREE.Vector3(1e9,1e9,1e9); mx=new THREE.Vector3(-1e9,-1e9,-1e9);
              for(let k=0;k<pos.count;k++){
                let best=-1,bw=0;
                for(let c=0;c<4;c++){const w=sw.getComponent(k,c); if(w>bw){bw=w;best=si.getComponent(k,c);}}
                if(best!==bi||bw<0.5) continue;
                v.fromBufferAttribute(pos,k).applyMatrix4(sk.matrixWorld).applyMatrix4(inv);
                mn.min(v); mx.max(v);
              }
            } else {
              arm.geometry.computeBoundingBox();
              const bb=arm.geometry.boundingBox; mn=bb.min.clone(); mx=bb.max.clone();
            }
            // The limb's long axis is whichever spans furthest.
            const d=new THREE.Vector3().subVectors(mx,mn);
            const along = (d.x>=d.y&&d.x>=d.z)?'x':(d.y>=d.z?'y':'z');
            const mk=(val)=>{const o=new THREE.Object3D(); arm.add(o);
              o.position.set((mn.x+mx.x)/2,(mn.y+mx.y)/2,(mn.z+mx.z)/2);
              o.position[along]=val;
              o.updateWorldMatrix(true,false);
              const w=new THREE.Vector3(); o.getWorldPosition(w);
              arm.remove(o); return toScreen(w);};
            r.armAxis={top:mk(mn[along]), bot:mk(mx[along])};
          }
          r.splotches = S.recs.map(x=>{
            const w=new THREE.Vector3(); x.sprite.getWorldPosition(w);
            return {t:x.t, screen:toScreen(w)};
          });
          return r;
        }""")
            print(json.dumps(out,indent=1))
            # VERDICT — must ASSERT, not print. An earlier version only printed
            # and happily reported ON ARM: True with the placement bug PLANTED,
            # because (a) nothing failed the process and (b) a bare bounding-box
            # test is too loose: the A-pose widens the arm's screen box enough to
            # swallow a 4px error. Require a real MARGIN inside the limb.
            assert 'armLeft' in out, out
            a=out['armLeft']
            ax0,ax1=sorted([a['min'][0],a['max'][0]])
            ay0,ay1=sorted([a['min'][1],a['max'][1]])
            w = ax1-ax0
            # DISTANCE TO THE LIMB'S CENTRE LINE, not a vertical band.
            #
            # This used to require the splotch inside the middle 70% of the arm's
            # screen BOUNDING BOX. That box is axis-aligned and the arm HANGS AT AN
            # ANGLE -- measured, its centre line runs from x=517 at the shoulder to
            # x=476 at the hand -- so a straight vertical band cannot describe it.
            # Splotches sitting exactly ON the limb axis failed by 2px at the far
            # end, and the obvious "fix" (nudging the splotch run inwards) moved
            # them OFF the limb to satisfy a wrong test. The arm also swings under
            # the spring now, so any axis-aligned band is wrong at most angles.
            #
            # Project the limb's own endpoints and measure perpendicular distance.
            ax, ay = out['armAxis']['top']
            bx, by = out['armAxis']['bot']
            seg = ((bx-ax)**2 + (by-ay)**2) ** 0.5
            halfw = w * 0.5
            print(f"\narm-left axis ({ax},{ay}) -> ({bx},{by})  screen halfwidth {halfw:.0f}px")
            print(f"required: within {halfw*0.7:.0f}px of the limb's OWN centre line")
            bad=[]
            for sp in out['splotches']:
                x,y=sp['screen']
                # perpendicular distance from the point to the axis segment
                d = abs((bx-ax)*(ay-y) - (ax-x)*(by-ay)) / seg if seg else 1e9
                # and it must lie BETWEEN the endpoints, not past the hand.
                #
                # WHAT THIS TEST CANNOT SEE, so nobody trusts it for it:
                # error pointing STRAIGHT AT THE CAMERA. Both numbers below
                # are computed after projection, so a splotch lifted along
                # the view vector projects to the same pixel no matter how
                # far it is lifted. Plant-verified: tripling the stand-off
                # in avatar.js leaves every character green here. What goes
                # red is sideways error, which is the class that puts dirt
                # on the torso, and that plant does fail all twelve.
                # A depth mistake needs a ray cast, not this.
                #
                # THE WINDOW IS BACK TO 0.05. It was widened to 0.12 when
                # four of twelve characters came out at u = -0.06, and that
                # was read as two measurements of the same bone disagreeing
                # at its end. It was not. The splotches were genuinely off
                # the arm: the stand-off in avatar.js pushed them along the
                # bone's local +Z by more than the limb is thick, and a ray
                # cast through each one hit `torso` or `leg-left` on all
                # twelve characters.
                #
                # The old near head-on camera projected that offset to one
                # or two pixels, which is why widening the window looked
                # reasonable. Placement now measures u between 0.39 and
                # 0.60, nowhere near either bound, so the slack is not
                # paying for anything and a tight window catches a repeat.
                #
                # 0.05 still allows the real disagreement this guards
                # against: avatar.js places the splotch through
                # `0.20 + t * 0.65` of the bone's own vertex box, and this
                # projects that box's extremes to screen, and a box corner
                # is not a joint once the limb is at an angle.
                u = ((x-ax)*(bx-ax) + (y-ay)*(by-ay)) / (seg*seg) if seg else -1
                ok = d <= halfw*0.7 and -0.05 <= u <= 1.05
                print(f"  splotch t={sp['t']} at ({x},{y})  {d:.0f}px off-axis, "
                      f"u={u:.2f}  ON LIMB: {ok}")
                if not ok: bad.append((sp['t'],x,y))
            if bad:
                _failures.append((_who, bad))
                print(f"  *** {_who}: {len(bad)} splotch(es) OFF THE LIMB")
        await b.close()
        if _failures:
            raise SystemExit(
                f"*** {len(_failures)} character(s) place splotches off the "
                f"limb: {_failures} ***")
        print(f"PLACEMENT ASSERTIONS PASSED for all {len(_cast)} characters")
asyncio.run(main())
