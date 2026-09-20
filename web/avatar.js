// web/avatar.js — the cartoon. Kenney MINI Characters (CC0, www.kenney.nl).
//
// WHY THIS MODEL: a named node hierarchy
// (root > {leg-left, leg-right, torso > {arm-left, arm-right, head}}), so pose
// mirroring is "set .quaternion on a node you found by name" -- no retargeting
// library. The rig is SKINNED: the shipped GLB reports skins=2 and 32 authored
// animations, and the bones are leaves with no geometry of their own.
//
// THIS COMMENT USED TO SAY "skins:0 ... six RIGID mesh parts", VERIFIED. It was
// true of the BLOCKY pack this file originally loaded and survived the swap to
// the mini pack, where it was flatly false -- while limbLocalBox() thirty lines
// down exists precisely BECAUSE the mesh is skinned ("SKINNED MODELS HAVE NO
// MESH UNDER THE LIMB NODE"). A header a future reader trusts, contradicted by
// the code beneath it. Found by re-measuring every MEASURED/VERIFIED claim in
// web/; guarded now so a model swap cannot leave it stale again.
//
// That matters because Kalidokit — the #1 search result for "MediaPipe to
// avatar" — is DEPRECATED in its own README (npm frozen since Feb 2022,
// targets an API Google EOL'd in 2023) and has NO maintained successor. The
// landmarks->bone-rotation gap is currently unowned by any live library.
// This model sidesteps it entirely rather than compromising around it.
//
// 72 triangles. Ships 32 baked clips; 'idle' is the tracking-loss fallback.
// (32, not 27: 27 was TRUE of the blocky pack's character-a.glb and survived
// the swap exactly like the skins:0 claim above it -- same comment block, same
// cleanup, missed by eight lines. Ground truth is the GLB: every one of the 12
// shipped models reports 32 animations. Line 6 above, main.js:387 and
// test_browser_pose.py:175 all already said 32.)

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/GLTFLoader.js';

const REST  = new THREE.Vector3(0, -1, 0);   // Kenney limbs hang down -Y
const FWD   = new THREE.Vector3(0, 0, 1);   // torso lean axis
const PARTS = ['torso', 'head', 'arm-left', 'arm-right', 'leg-left', 'leg-right'];

// MediaPipe worldLandmarks are metres with +Y DOWN; three.js is +Y UP.
// Negate Y or the cartoon is upside down. Negate Z so it MIRRORS the person
// rather than facing away. Google has never documented this convention
// (mediapipe issue #3370 asked in 2022, closed unanswered), so there is a
// runtime assert below instead of a comment promising it is right.
const SCALE = 3.0;
const V = lm => new THREE.Vector3(lm.x * SCALE, -lm.y * SCALE, -lm.z * SCALE);

// Landmark indices (verified against the installed mediapipe package).
// MediaPipe delivers all 33 landmarks; these are the ones the cartoon uses.
// Knees and ankles were sitting unused while the legs stayed frozen -- Tyler's
// brief says "map the ENTIRE person", and a character whose legs never move
// while its arms mirror you reads as half-broken.
const LM = { L_SH: 11, R_SH: 12, L_EL: 13, R_EL: 14,
             L_WR: 15, R_WR: 16, L_HIP: 23, R_HIP: 24,
             L_KNEE: 25, R_KNEE: 26, L_ANK: 27, R_ANK: 28 };

export async function makeAvatar(scene) {
  // KENNEY MINI CHARACTERS (CC0). NOT hand-built, and not the blocky pack.
  //
  // I went through two wrong shapes before this. First Kenney's BLOCKY pack --
  // Minecraft: hard corners, slab limbs, cube head. Then I hand-sculpted a
  // blob out of capsules and lathes, which had the right silhouette but was
  // still me making something that already exists. Tyler's note, and his
  // original brief: "use as much as possible that is public online".
  //
  // This is Kenney's MINI pack: rounded chunky head, toy proportions, flat
  // palette colours, a real face -- the Fall Guys / PEAK family, drawn by an
  // actual artist. It ships:
  //   - 32 ANIMATIONS (idle, walk, die, emote-yes/no, wheelchair-*, ...)
  //   - a SKINNED rig, so limbs DEFORM rather than pivoting like hinges
  //   - the SAME node names the old model had (torso, arm-left, head, ...),
  //     so every spring, splotch and impulse kept working untouched
  //
  // The pack also ships canes, crutches, wheelchairs and defibrillators, and
  // the rig has wheelchair animations -- which is a gift for a demo whose
  // pitch is caregiving and bathing dignity.
  // WHICH CHARACTER. The Kenney mini pack ships 12 people; swapping is a
  // filename, and the rig/animation set is IDENTICAL across all of them, so
  // every spring, splotch and clip works on any of them unchanged.
  //
  // Why this is worth having on a demo table: the volunteer should be able to
  // see someone who looks a bit like them. It costs one keypress and it is the
  // difference between "a cartoon" and "your cartoon".
  // THE WHOLE CAST. 'v' cycles them and they share ONE rig, so every spring,
  // splotch and clip works on any of them unchanged. Keep in sync with
  // vendor.sh -- test_docs_match_code.py checks every name here has a file.
  //
  // THERE IS NO 13TH CHARACTER. I added 'character-male-a' here believing the
  // zip shipped a face we had never extracted, and reverted it: vendor.sh:62
  // unzips that exact file and RENAMES it to mini-character.glb. Measured --
  // md5 ae446c76df2ca77d86329f7bc09d38c0, 246916 bytes, both files identical.
  // Listing it would put the same face in the 'v' cycle twice. The pack ships
  // 12 characters, and all 12 are here.
  // THE ACCESSORIES. Eight props already vendored from the same Kenney pack
  // as the twelve characters, and until now not one of them was loaded.
  //
  // They matter more than decoration: the pitch is that this machine restores
  // dignity to people who need help with bathing, and a cartoon wearing
  // glasses or holding a cane is that person rather than a generic figure.
  // Tyler's standing rule about outfits and skin tones is the same argument --
  // a volunteer who sees themselves gets "that's me".
  //
  // Offsets are in HEAD-BONE-LOCAL space and derived from measurement, not
  // taste. The head bone is empty (the mesh is skinned), so it gives no size;
  // the head MESH is 0.454 wide by 0.432 tall with its top at y 0.776 while
  // the bone origin sits at y 0.343. That puts the eye line about 0.06 above
  // the bone and the face plane about 0.16 forward of it.
  //
  // `scale: 1` on the face props is not laziness -- glasses measure 0.330
  // wide against a 0.454 head, which is the proportion the pack's own artist
  // chose. Scaling them would break the match.
  const ACCESSORIES = [
    // MEASURED OFF THE HEAD MESH IN BONE-LOCAL SPACE, after a screenshot showed
    // the first guess sitting across the forehead and hair. The head mesh spans
    // y 0 to 0.432 above the bone with its face plane at z 0.210, so the eye
    // line is about 55% up at y 0.238 and anything at y 0.055 is on the chin --
    // which is exactly what the picture showed. The assertions all passed while
    // it looked wrong, which is why the screenshot is the acceptance test.
    // SUBTRACT EACH PROP'S OWN CENTRE OFFSET. The second screenshot still had
    // the glasses at the hairline with their temple arms poking out of the
    // skull, because a prop's geometry is not centred on its own origin:
    // measured, the glasses sit +0.048 ABOVE theirs and are 0.184 deep. So
    // placing the ORIGIN at the eye line puts the LENSES 0.048 higher, and a z
    // of 0.175 buried most of a 0.184-deep prop inside a head whose face plane
    // is at 0.210.
    //
    // Target the lens centre at the eye line (y 0.238) and the front face just
    // proud of the cheek (z 0.230), then work back through each prop's own
    // measured offset and half-depth.
    { file: 'aid-glasses',          at: 'head', pos: [0, 0.190, 0.140], scale: 1.0 },
    { file: 'aid-sunglasses',       at: 'head', pos: [0, 0.190, 0.140], scale: 1.0 },
    // A mask covers nose and mouth: below the eye line, and its measured
    // centre is pushed +0.128 forward of its own origin, so the origin goes
    // well BEHIND the face plane to land the mask on it.
    { file: 'aid-mask',             at: 'head', pos: [0, 0.150, 0.040], scale: 1.0 },
    // On the ear: out at the side of a 0.454-wide head (so x 0.227 is the
    // surface), level with the eyes, and its centre is +0.062 above its own
    // origin.
    { file: 'aid_hearing',          at: 'head', pos: [0.205, 0.176, -0.020], scale: 1.0 },
    // The walking aids hang off the torso, not the head, and they are PROPS
    // BESIDE the chair rather than in a hand: the person is seated, so a cane
    // held up reads as brandishing it. Parked at hip height, leaning.
    { file: 'aid-cane',             at: 'torso', pos: [0.20, -0.12, 0.10], scale: 1.0, tilt: 0.18 },
    { file: 'aid-cane-blind',       at: 'torso', pos: [0.20, -0.12, 0.10], scale: 1.0, tilt: 0.18 },
    { file: 'aid-cane-low-vision',  at: 'torso', pos: [0.20, -0.12, 0.10], scale: 1.0, tilt: 0.18 },
    { file: 'aid-crutch',           at: 'torso', pos: [0.22, -0.10, 0.06], scale: 1.0, tilt: 0.14 },
  ];

  const CAST = ['mini-character',
                'character-female-a', 'character-female-b', 'character-female-c',
                'character-female-d', 'character-female-e', 'character-female-f',
                'character-male-b', 'character-male-c', 'character-male-d',
                'character-male-e', 'character-male-f'];
  const pick = (() => {
    try {
      const saved = localStorage.getItem('wheelgentic.character');
      if (saved && CAST.includes(saved)) return saved;
    } catch (_) {}          // private windows throw on localStorage
    return CAST[0];
  })();
  const gltf = await new GLTFLoader().loadAsync(`./assets/${pick}.glb`);
  // SCALE AND FACING ARE MODEL-SPECIFIC. The mini pack is authored at a
  // different size than the blocky one and fronts +Z, not -Z, so the inherited
  // 1.2 scale + 180-degree root rotation left a small character showing its
  // back. Measured against the frame, not guessed.
  gltf.scene.scale.setScalar(2.6);
  scene.add(gltf.scene);

  let skinnedMesh = null;
  gltf.scene.traverse(o => { if (o.isSkinnedMesh && !skinnedMesh) skinnedMesh = o; });

  // ---- RECOLOURING: outfits and skin tones -------------------------------
  // Tyler: "make sure wherever you found the models you can change the
  // outfits, skin colors, etc to make it feel much more like a polished video
  // game." It is also the privacy pitch: a volunteer who sees THEIR skin tone
  // on the projector gets "that's me", not "that's a generic guy".
  //
  // HOW THE ATLAS IS BUILT, measured -- not assumed. All 12 characters share
  // ONE 512x512 texture, colormap.png, and both meshes share ONE material and
  // ONE map. It is not flat swatches: each region is a VERTICAL CEL RAMP of
  // 20-30 narrow bands, light at the top, dark at the bottom. That ramp is
  // what gives the toon shading its steps, so it has to survive any recolour.
  //
  // THE UV MAPPING IS UNFLIPPED. Calibrated against a known answer rather than
  // derived: the shirt is visibly green, green lives at image y256..383, and
  // uv(0.219, 0.525) resolves to img(112,268) = #5ac487 ONLY without a v-flip.
  // I got this backwards twice by reasoning about it; sampling a known colour
  // settled it in one read.
  //
  // THE REGIONS, read off the live model's own UV clusters:
  const PALETTE_REGIONS = {
    shirt:  [64, 128, 256, 384],     // #61cb8b -> #1b8469
    waist:  [160, 192, 256, 384],    // #ffd565 -> #ff952f
    shorts: [352, 384, 256, 384],    // #6794d9 -> #5756bd
    skin:   [416, 448, 384, 512],    // #b06041 -> #845442
    hair:   [32,  64,  384, 512],    // #464650 -> #353539
  };

  // HAND-PICKED PAIRS, NOT A HUE ROTATION. Rotating hue while pinning
  // lightness and saturation turned the green shirt OLIVE when I asked for
  // purple -- mid-tones land somewhere unintended and the result reads muddy.
  // Replacing the ramp between two chosen ends is predictable and keeps every
  // band boundary exactly where it was.
  const SKIN_TONES = [
    ['#f2bf99', '#b8794e'],   // the pack's own light tone
    ['#fad9bd', '#c9946e'],
    ['#e0a57e', '#9b5a41'],
    ['#b06041', '#845442'],   // the shipped default
    ['#8d5a3b', '#4a2c1c'],
    ['#6b4430', '#361f14'],
  ];
  const OUTFITS = [
    { shirt: ['#61cb8b', '#1b8469'], shorts: ['#6794d9', '#5756bd'] },  // shipped
    { shirt: ['#c98bf5', '#5b2e91'], shorts: ['#f2bf99', '#a8683f'] },
    { shirt: ['#f27a74', '#8e2a2a'], shorts: ['#4f5260', '#2b2d36'] },
    { shirt: ['#ffd061', '#c07d16'], shorts: ['#6b46c2', '#3d2374'] },
    { shirt: ['#d0e8ff', '#6d99db'], shorts: ['#cf534f', '#7d2420'] },
    { shirt: ['#38383d', '#1a1a1e'], shorts: ['#ffa639', '#b06a14'] },
  ];

  const hex = h => [parseInt(h.slice(1, 3), 16),
                    parseInt(h.slice(3, 5), 16),
                    parseInt(h.slice(5, 7), 16)];

  // The atlas is drawn into a canvas ONCE; every recolour repaints from the
  // pristine copy so repeated changes cannot compound.
  let paletteCanvas = null, paletteCtx = null, pristine = null, paletteTex = null;
  const material = skinnedMesh && skinnedMesh.material;

  function initPalette() {
    const img = material && material.map && material.map.image;
    if (!img || !img.width) return false;
    paletteCanvas = document.createElement('canvas');
    paletteCanvas.width = img.width; paletteCanvas.height = img.height;
    paletteCtx = paletteCanvas.getContext('2d', { willReadFrequently: true });
    paletteCtx.drawImage(img, 0, 0);
    pristine = paletteCtx.getImageData(0, 0, img.width, img.height);
    paletteTex = new THREE.CanvasTexture(paletteCanvas);
    // MATCH THE SOURCE TEXTURE'S SETTINGS or the character turns dark and
    // mirrored: an untagged canvas texture is treated as LINEAR, and three.js
    // flips Y on image textures but not on canvas ones.
    paletteTex.colorSpace = material.map.colorSpace;
    paletteTex.flipY = material.map.flipY;
    paletteTex.magFilter = material.map.magFilter;
    paletteTex.minFilter = material.map.minFilter;
    // CLONE THE MATERIAL TOO. One material serves both meshes AND is shared
    // through the cached GLTF, so assigning a new map in place would recolour
    // every character at once the moment 'v' swapped them.
    gltf.scene.traverse(o => {
      if (o.isMesh || o.isSkinnedMesh) {
        o.material = o.material.clone();
        o.material.map = paletteTex;
      }
    });
    return true;
  }

  /** Repaint one region's ramp between two hex ends, keeping its band steps. */
  function setRegion(region, topHex, botHex) {
    if (!paletteCtx && !initPalette()) return false;
    const r = PALETTE_REGIONS[region];
    if (!r) return false;
    const [x0, x1, y0, y1] = r;
    const W = paletteCanvas.width;
    const top = hex(topHex), bot = hex(botHex);
    const src = pristine.data;
    const img = paletteCtx.getImageData(x0, y0, x1 - x0, y1 - y0);
    const d = img.data;
    // Band boundaries come from the PRISTINE copy, so they never drift.
    const xc = (x0 + x1) >> 1;
    const bounds = []; let prev = null;
    for (let y = y0; y < y1; y++) {
      const i = (y * W + xc) * 4;
      const key = src[i] + ',' + src[i + 1] + ',' + src[i + 2];
      if (key !== prev) { bounds.push(y); prev = key; }
    }
    bounds.push(y1);
    const n = Math.max(bounds.length - 2, 1);
    for (let b = 0; b < bounds.length - 1; b++) {
      const t = Math.min(b / n, 1);
      const col = [0, 1, 2].map(k => Math.round(top[k] + (bot[k] - top[k]) * t));
      for (let y = bounds[b]; y < bounds[b + 1]; y++) {
        for (let x = x0; x < x1; x++) {
          const si = (y * W + x) * 4;
          // Leave the atlas's black background alone -- it is not a swatch.
          if (src[si] === 0 && src[si + 1] === 0 && src[si + 2] === 0) continue;
          const di = ((y - y0) * (x1 - x0) + (x - x0)) * 4;
          d[di] = col[0]; d[di + 1] = col[1]; d[di + 2] = col[2];
        }
      }
    }
    paletteCtx.putImageData(img, x0, y0);
    paletteTex.needsUpdate = true;
    return true;
  }

  let skinIdx = 3, outfitIdx = 0;      // the shipped defaults
  function nextSkin() {
    skinIdx = (skinIdx + 1) % SKIN_TONES.length;
    const [a, b] = SKIN_TONES[skinIdx];
    return setRegion('skin', a, b) ? skinIdx : -1;
  }
  function nextOutfit() {
    outfitIdx = (outfitIdx + 1) % OUTFITS.length;
    const o = OUTFITS[outfitIdx];
    const ok = setRegion('shirt', o.shirt[0], o.shirt[1]) &&
               setRegion('shorts', o.shorts[0], o.shorts[1]);
    return ok ? outfitIdx : -1;
  }

  const node = {};
  for (const n of PARTS) {
    node[n] = gltf.scene.getObjectByName(n);
    if (!node[n]) throw new Error(`missing node "${n}" — wrong model file?`);
  }

  const mixer = new THREE.AnimationMixer(gltf.scene);
  const idleClip = gltf.animations.find(a => a.name === 'idle');
  const idle = idleClip ? mixer.clipAction(idleClip) : null;

  // USE THE ANIMATIONS. The pack ships 32 professional clips and exactly ONE
  // was being played, as a tracking-loss fallback -- an animation library
  // bought and left in the box. Tyler's note was "VERY GOOD ANIMATIONS but
  // very simplistic artistically": the motion is the budget, and a character
  // that never reacts is the opposite of that.
  //
  // These are one-shots layered OVER the tracked pose, so they punctuate a
  // beat and get out of the way rather than fighting the mirroring.
  const clips = {};
  for (const a of gltf.animations) clips[a.name] = a;
  let oneShot = null;

  // SEATED MODE. `seated` is read by the spring loop, which hands the legs
  // over while it is true (see the ownership block there).
  //
  // Both numbers are MEASURED on the shipped mini pack, not guessed:
  //   standing root Y ............ 0.550   (FLOOR_Y, main.js)
  //   torso / legs world Y ....... 1.008
  //   head world Y ............... 1.441
  // so the body above the hip is 0.433 tall and the legs carry 0.458.
  //
  // SEAT_DROP is how far the root falls so the folded thighs land on a seat
  // instead of hanging in the air. A seated human's hip sits at roughly half
  // its standing leg length, and half of 0.458 is 0.229 -- rounded to 0.23.
  // SEAT_HIP is the hip angle: PI/2 is a right angle at the hip, which is what
  // sitting upright in a chair is.
  //
  // standY is captured on the FIRST setSeated(true), never at construction:
  // main.js sets `avatar.root.position.y = 0.55` at its line 240, well after
  // this factory returns, so reading it here would bank 0 and stand the
  // character up into the floor on release.
  let seated = false;
  let standY = null;
  const SEAT_DROP = 0.23;
  const SEAT_HIP  = Math.PI / 2;

  /**
   * Play a clip once. Named beats only -- 'emote-yes' at the finale,
   * 'emote-no' when a scrub aborts, 'die' as the total-failure gag.
   * Unknown names are ignored rather than thrown, because a missing clip must
   * never take the projector down mid-demo.
   */
  function playOnce(name, fade = 0.12) {
    const c = clips[name];
    // A CLIP NAME THAT DOES NOT EXIST DOES NOTHING, SILENTLY. Three call sites
    // in main.js ignore this return value (the 100% celebrate, the miss
    // reaction, the estop flinch), so a typo there is a beat that never fires
    // with nothing anywhere to say why. The names are bare strings crossing a
    // file boundary; all 7 currently resolve on all 12 models, checked against
    // the GLBs, but nothing keeps that true.
    //
    // warn, not error: a browser test collects console errors, and a missing
    // clip costs one reaction rather than the demo.
    if (!c) { console.warn(`no animation clip named '${name}'`); return false; }
    // STOP THE PREVIOUS ACTION OUTRIGHT, do not cross-fade it.
    // mixer.clipAction(clip) returns the SAME action object for the same clip,
    // so fadeOut(prev) followed by fadeIn(next) cancelled each other whenever
    // the two were the same clip -- and even across different clips the two
    // fades fought. Measured: the first 'e' moved the head by 2.000 and the
    // next two by 0.033 and 0.025, i.e. the second and third presses did
    // nothing visible.
    if (oneShot) { oneShot.stop(); oneShot.enabled = false; }
    oneShot = mixer.clipAction(c);
    oneShot.stop();                    // clear any leftover state on reuse
    oneShot.enabled = true;
    // reset() is redundant after stop() -- verified by deleting it and
    // watching the restart still work (action.time 0.176 -> 0.032). Kept
    // because three.js documents reset() as the way to rewind an action and a
    // future reader should not have to re-derive that stop() already did it.
    oneShot.reset();
    oneShot.setLoop(THREE.LoopOnce, 1);
    // HOLD THE LAST FRAME OF A FLOP, RELEASE AN EMOTE. An emote is a reaction
    // and must hand the body back to the springs; 'die' is a punchline and the
    // character has to STAY down. With clamp off for everything, the death gag
    // measured: torso deviation from upright 0.2926 at t+0.36s, then 0.0000 at
    // t+0.48s -- the very next sample. It teleported upright inside 120ms, so
    // the one gag whose whole joke is lying there flat had no beat to land in.
    oneShot.clampWhenFinished = (name === 'die');
    // SLOW THEM DOWN. These clips are authored for a game loop and run
    // 0.2-0.7s -- at full speed an emote is a flicker nobody reads from ten
    // feet. 0.55x turns a flicker into a beat without looking like slow motion.
    oneShot.timeScale = 0.55;
    oneShot.setEffectiveWeight(1);
    oneShot.fadeIn(fade).play();
    return true;
  }

  // ---- SEATED MODE: TRIED AND CUT ---------------------------------------
  // A person being bathed is more likely seated than standing, and the pack
  // ships a wheelchair, so this looked like free alignment with the caregiving
  // pitch. It is not, and the reason is worth recording so nobody re-attempts
  // it at hour 30.
  //
  // MEASURED: neither 'sit' nor 'wheelchair-sit' moves the root or the torso.
  //   standing        rootY 0.550  torsoY 1.008
  //   wheelchair-sit  rootY 0.550  torsoY 1.008
  //   sit             rootY 0.550  torsoY 1.008
  // (RE-MEASURED on the shipped mini pack. These read rootY 0.000 /
  //  torsoY 0.176 -- true of the BLOCKY pack this file first loaded, stale
  //  after the swap, same era as the "skins:0" header. The CONCLUSION was
  //  unaffected: all three still agree to 3dp, so the clips rotate limbs and
  //  never lower the body.)
  // They only ROTATE limbs. The character never lowers into a seat, so a chair
  // placed under it intersects the torso no matter how it is scaled -- I fitted
  // the chair to 0.75 of body height and aligned the ground planes and it still
  // read as a chair worn around the waist.
  //
  // Making it work needs the ROOT lowered by hand and the legs re-posed to
  // match a specific chair -- i.e. authoring a pose, which is exactly the
  // "build it yourself" this project keeps being told not to do. Kenney's own
  // demo seats these characters by placing them IN a chair mesh at authored
  // offsets, which is a per-chair constant nobody has measured.
  //
  // If the seated look matters more than the scrub, the cheap version is a
  // camera crop from the waist up -- not a chair.

  /** Names the page can choose from, so the caller is never guessing. */
  const animationNames = gltf.animations.map(a => a.name);

  // REST POSE. Without this the arms hang dead-vertical and visually MERGE
  // with the torso -- on screenshot the character reads as a bust, not a
  // person, and you cannot tell where an arm ends. A slight A-pose separates
  // the silhouette so the splotches are legible from ten feet.
  // MEASURED (not assumed): at rest the limb's world direction is
  // [-0.014, -1, 0.011] -- straight down to within the A-pose splay set just
  // above -- which confirms REST=(0,-1,0). A rotation of z=+0.5 sends
  // arm-left's tip to x=+0.467, i.e. OUTWARD, so positive z is away-from-body
  // for the LEFT arm and negative for the right. (Re-measured. This said
  // "exactly [0,-1,0]" and +0.479, overstating a figure that carries the
  // A-pose in it.)
  // NOTE: this is the REST pose only. aim() writes .quaternion directly, so
  // the moment a pose is tracked these rotations are replaced -- which is
  // correct (a tracked arm should go where the person's arm is), but it means
  // the A-pose only shapes the idle silhouette, not the tracked one.
  const AWAY = 0.26;                       // radians out from the body
  node['arm-left'].rotation.z  =  AWAY;
  node['arm-right'].rotation.z = -AWAY;

  const _d = new THREE.Vector3();
  let lastSeen = 0, idling = false, checkedOrientation = false;

  // ---- WEIGHT. The difference between a puppet and a character. ----------
  //
  // Before this, aim() set .quaternion straight to the tracked direction: the
  // limb WAS the answer, instantly, every frame. That reads as a mannequin on
  // strings -- it does exactly what it is told and nothing is funny about it.
  //
  // Gang Beasts / Fall Guys / PEAK are funny because the character is always
  // mid-argument with physics: it is TRYING to hit a pose and constantly
  // arriving late, overshooting, and sagging under its own mass. The gap
  // between intention and result IS the joke.
  //
  // So every limb now chases its target through a spring instead of snapping
  // to it. Same MediaPipe input, but the limb arrives late, overshoots, and
  // settles -- and an impulse (the sponge hitting the arm) can shove it.
  //
  // WHY NOT A REAL RAGDOLL: a rigid-body sim decides where the limb IS, so
  // the forearm can end up far from where MediaPipe says the person's arm is.
  // The splotches ride the arm, and the pitch is "it is tracking her ACTUAL
  // forearm, live" -- that beat dies if the limb wanders. Springs keep the
  // limb honest while still giving it mass.
  const SPRING = {
    // THE COMMENT THAT USED TO BE HERE WAS BACKWARDS, and it would have sent
    // the next person turning this knob the wrong way. It read: "Underdamped
    // ON PURPOSE. damp=1 is critical damping (no overshoot, no personality);
    // 0.72 lets it wobble past the target and come back, which is the entire
    // point... <1 overshoots. Higher = stiffer/deader."
    //
    // `damp` is a per-frame VELOCITY RETENTION multiplier, applied as
    // vel *= damp^(dt*60). At 60fps that is vel *= 0.72 EVERY FRAME -- 28% of
    // the velocity thrown away sixty times a second. That is heavy damping.
    // Higher is LOOSER, not stiffer: 1.0 would keep all velocity and ring
    // forever; 0 would stop the limb dead on the frame it was hit.
    //
    // MEASURED TWICE through the shipped spring, not reasoned about:
    //   tracking path -- 0% overshoot, monotonic rise, 2288ms to settle
    //   impulse path  -- 0 direction reversals at 2.2 / 2.5 / 4 / 6 / 9
    // The limb bulges toward the target and returns without ever crossing it.
    // There is no wobble anywhere in this rig today.
    // 0.72 -> 0.93, MEASURED ON BOTH PATHS through the shipped integrator:
    //
    //   impulse response (2.5 into the arm, 120 frames)
    //     damp   peak    wobbles   quiet by
    //     0.72   0.099      0       never      <- what shipped: no wobble at all
    //     0.85   0.182      1       frame 80
    //     0.90   0.243      2       frame 53
    //     0.93   0.296      3       frame 45   <- 3x the swing, over in 750ms
    //     0.97   0.397      3       frame 41
    //     0.99   0.456      5       frame 41   <- still moving at 2s, fights tracking
    //
    //   tracking path (the bar test_browser_pose.py sets: settle 90 frames,
    //   delta must exceed 0.15; it also must not keep drifting afterwards)
    //     0.72   delta 0.591   drift after 60 more frames 0.0979
    //     0.93   delta 0.674   drift 0.0146   <- converges TIGHTER than shipped
    //
    // So 0.93 buys a real comic wobble and costs nothing: the limb settles
    // closer to the tracked pose than 0.72 did, and stops moving sooner.
    k:    22.0,     // stiffness -- how hard it pulls toward the target
    damp: 0.93,     // per-frame velocity RETENTION. Higher = looser/wobblier.
    // Cap the correction per frame so a MediaPipe jump (a landmark flipping
    // sides, which it does) becomes a fast swing rather than a teleport.
    maxStep: 12.0,
  };

  // Per-part spring state: current orientation, and angular velocity as a
  // small-rotation vector. Quaternions do not subtract cleanly, so the spring
  // runs on the ANGLE-AXIS error between current and target, which is stable
  // through the antiparallel case setFromUnitVectors already handles.
  const spring = {};
  for (const n of PARTS) {
    spring[n] = {
      cur: node[n].quaternion.clone(),
      vel: new THREE.Vector3(),       // angular velocity, rad/s
      target: node[n].quaternion.clone(),
      stretch: 0,                     // squash-and-stretch, decays to 0
    };
  }

  const _qErr = new THREE.Quaternion();
  const _axis = new THREE.Vector3();
  const _dq   = new THREE.Quaternion();

  /** Advance one limb's spring by dt. Writes node[part].quaternion. */
  function stepSpring(part, dt) {
    const S = spring[part];
    // error = target * inverse(cur), as angle-axis
    _qErr.copy(S.cur).invert().premultiply(S.target);
    if (_qErr.w < 0) {              // shortest path -- avoid the long way round
      _qErr.set(-_qErr.x, -_qErr.y, -_qErr.z, -_qErr.w);
    }
    const sinHalf = Math.sqrt(Math.max(0, 1 - _qErr.w * _qErr.w));
    let angle = 2 * Math.atan2(sinHalf, _qErr.w);
    if (angle > Math.PI) angle -= 2 * Math.PI;
    if (sinHalf > 1e-6 && Number.isFinite(angle)) {
      _axis.set(_qErr.x, _qErr.y, _qErr.z).divideScalar(sinHalf);
      // v += k * error * dt, then damp
      S.vel.addScaledVector(_axis, SPRING.k * angle * dt);
    }
    S.vel.multiplyScalar(Math.pow(SPRING.damp, dt * 60));

    // integrate: cur = exp(vel*dt) * cur
    const w = S.vel.length();
    if (w > 1e-6) {
      const step = Math.min(w * dt, SPRING.maxStep * dt);
      _axis.copy(S.vel).divideScalar(w);
      _dq.setFromAxisAngle(_axis, step);
      S.cur.premultiply(_dq).normalize();
    }
    // NaN CANNOT BE ALLOWED TO PERSIST. A poisoned quaternion is written back
    // every frame forever and the limb vanishes silently -- the same trap
    // aim() guards against, now reachable through the integrator too.
    if (!Number.isFinite(S.cur.x + S.cur.y + S.cur.z + S.cur.w)) {
      S.cur.copy(S.target); S.vel.set(0, 0, 0);
    }
    node[part].quaternion.copy(S.cur);
  }

  /**
   * Shove a limb. THIS IS WHAT MAKES THE SCRUB READ AS HAPPENING TO SOMEONE.
   *
   * Without it the sponge touches the arm and the cartoon does nothing except
   * lose a splotch -- the scrub and the character are two unrelated things on
   * one screen. An impulse per contact makes the forearm jiggle and recover,
   * and because arms are CHILDREN OF THE TORSO in this model, shoving the
   * torso rocks the whole upper body for free.
   */
  function impulse(part, x, y, z) {
    const S = spring[part];
    if (S) S.vel.add(new THREE.Vector3(x, y, z));
  }

  /** Cartoon squash: compress along the limb, spring back. ~150ms. */
  function squash(part, amount) {
    const S = spring[part];
    if (S) S.stretch = Math.max(S.stretch, amount);
  }

  // TRAP 1 — DO NOT USE mesh.lookAt(b). lookAt aims the object's -Z axis;
  // Kenney's limbs are +/-Y aligned. lookAt lands the limb tip a FULL LIMB
  // LENGTH from the target. setFromUnitVectors handles the 180-degree
  // antiparallel singularity internally.
  function aim(part, a, b) {
    _d.subVectors(b, a);
    // NaN PASSES A lengthSq() GUARD. Every comparison against NaN is false, so
    // `NaN < 1e-8` is false and a NaN vector sails straight through into
    // setFromUnitVectors -- which poisons the quaternion PERMANENTLY, because
    // three.js keeps writing the NaN back every frame. The limb vanishes for
    // the rest of the demo with nothing logged. MediaPipe does emit NaN for
    // low-visibility landmarks.
    const n = _d.lengthSq();
    if (!(n > 1e-8)) return;               // false for NaN, 0 and negatives
    // SETS THE TARGET, NOT THE POSE. The spring closes the gap, so the limb
    // arrives late and overshoots instead of teleporting. stepSpring() is what
    // actually writes .quaternion now.
    spring[part].target.setFromUnitVectors(REST, _d.normalize());
  }

  // ---- SPLOTCHES: the trap that would break the demo's money shot ---------
  // TRAP 2 — DO NOT use THREE.DecalGeometry. Decals project against BIND-POSE
  // vertices, so a splotch stays FROZEN IN SPACE while the arm moves out from
  // under it. three.js issue #7926, open since 2016, no fix, no plan.
  //
  // TRAP 3 — DO NOT parent a sprite directly to a limb you scale: a child
  // under scale.set(1, len, 1) comes out SQUASHED and its position drifts
  // with limb length. Dirt that visibly changes shape as the arm moves reads
  // as a bug to judges — on the exact feature the demo is built around.
  // FIX: wrap in a GROUP whose scale stays identity.
  //
  // Sprites are auto-skipped by OutlineEffect (isCompatible() requires isMesh
  // + geometry.attributes.normal), so splotches need no opt-out.
  // THREE textures, not one -- see makeDirtTexture(). Built once at load and
  // handed out by splotch index, so each splotch keeps its own shape across
  // every reset (resetSplotches reuses the sprite objects rather than
  // rebuilding them, so the assignment is permanent).
  const dirtTex = [0, 1, 2].map(makeDirtTexture);
  const foamTex = [0, 1, 2].map(makeFoamTexture);
  const splotches = [];
  const _box = new THREE.Box3(), _sz = new THREE.Vector3(),
        _rel = new THREE.Matrix4(), _b2 = new THREE.Box3();
  // Scratch for faceSplotches(), allocated once: it runs per splotch per
  // frame, and three Vector3s plus a Matrix4 per frame is exactly the kind of
  // churn that shows up as jank on a projector.
  const _axisW = new THREE.Vector3(), _toCam = new THREE.Vector3(),
        _off = new THREE.Vector3(), _m3 = new THREE.Matrix4();

  /** Limb extents in the NODE'S OWN local space.
   *
   * MUST NOT use Box3.setFromObject(node) -- that returns the WORLD box,
   * which folds in every parent transform. Measured difference on arm-left:
   * world box reports origin-relative garbage, node-local reports
   * y from +0.1 down to -1.0, x/z width 0.4. Using the world box sized a
   * splotch sprite several times the limb width and produced one giant blob
   * covering the whole torso. Screenshot caught it; no console error did.
   */
  // Which way does a limb POINT in its own bone space? Measured, not assumed.
  // The rigid Kenney pack ran limbs down -Y (hence REST = (0,-1,0)); the mini
  // pack's arm bone runs along +X, so the old -Y formula put every splotch
  // down by the character's FEET. This is filled in by limbLocalBox().
  const limbAxis = {};

  function limbLocalBox(part) {
    const o = node[part];
    o.updateWorldMatrix(true, true);

    // SKINNED MODELS HAVE NO MESH UNDER THE LIMB NODE. The mini pack is
    // skinned: 'arm-left' is a leaf BONE and the geometry lives in one shared
    // SkinnedMesh elsewhere. Traversing the node for meshes found none, left
    // the Box3 empty, and returned len = |+Infinity| -- which put every
    // splotch holder at -Infinity and made their world positions NaN. The
    // splotches silently vanished and the counter never moved, no error.
    //
    // There is also no child bone to measure against (the skeleton is six
    // leaves: root, legs, torso, arms, head), so the limb's extent has to come
    // from the VERTICES THIS BONE OWNS.
    const sk = skinnedMesh;
    if (sk && sk.skeleton) {
      const bi = sk.skeleton.bones.indexOf(o);
      if (bi >= 0) {
        const cached = limbAxis[part];
        if (cached) return cached;
        const g = sk.geometry;
        const pos = g.attributes.position;
        const si = g.attributes.skinIndex, sw = g.attributes.skinWeight;
        // UPDATE THE SKINNED MESH TOO. o.updateWorldMatrix() refreshes the
        // BONE, but the vertices are transformed by sk.matrixWorld -- and on
        // the first call that is still identity/stale, so the measured bounds
        // came out in the wrong frame: offB read -0.37 for an arm whose real
        // y-range is [-0.121, +0.071]. The result is CACHED, so one stale
        // first call poisons every splotch for the life of the page.
        sk.updateWorldMatrix(true, false);
        const inv = o.matrixWorld.clone().invert();
        const v = new THREE.Vector3();
        const mn = new THREE.Vector3(1e9, 1e9, 1e9);
        const mx = new THREE.Vector3(-1e9, -1e9, -1e9);
        for (let k = 0; k < pos.count; k++) {
          let best = -1, bw = 0;
          for (let c = 0; c < 4; c++) {
            const w = sw.getComponent(k, c);
            if (w > bw) { bw = w; best = si.getComponent(k, c); }
          }
          if (best !== bi || bw < 0.5) continue;
          v.fromBufferAttribute(pos, k).applyMatrix4(sk.matrixWorld).applyMatrix4(inv);
          mn.min(v); mx.max(v);
        }
        if (mn.x < 1e8) {
          // The limb's long axis is whichever spans furthest.
          const d = new THREE.Vector3().subVectors(mx, mn);
          const along = (d.x >= d.y && d.x >= d.z) ? 'x' : (d.y >= d.z ? 'y' : 'z');
          const len = d[along];
          const wid = Math.max(...['x','y','z'].filter(a => a !== along).map(a => d[a]));
          const res = { len, wid, cx: 0, along,
                        from: mn[along], to: mx[along],
                        // centre on the other two axes so the sprite sits on
                        // the limb rather than its edge
                        offA: (mn.x + mx.x) / 2, offB: (mn.y + mx.y) / 2,
                        offC: (mn.z + mx.z) / 2,
                        // THE LIMB'S OWN HALF-DEPTH ON EACH AXIS. `wid` is the
                        // LARGER of the two cross-axis spans, so using it as a
                        // stand-off overshoots whenever the limb is not round.
                        // Measured on the shipped arm: x 0.298, y 0.187,
                        // z 0.221, so z's half-span is 0.111 while wid*0.6 is
                        // 0.133 -- the sprite stood off further than the arm
                        // is thick and landed on the torso.
                        halfX: (mx.x - mn.x) / 2,
                        halfY: (mx.y - mn.y) / 2,
                        halfZ: (mx.z - mn.z) / 2 };
          limbAxis[part] = res;
          return res;
        }
      }
    }

    // Rigid (non-skinned) fallback: the original mesh-bounds path.
    _box.makeEmpty();
    _rel.copy(o.matrixWorld).invert();
    o.traverse(m => {
      if (!m.isMesh) return;
      m.geometry.computeBoundingBox();
      _b2.copy(m.geometry.boundingBox);
      _b2.applyMatrix4(_rel.clone().multiply(m.matrixWorld));
      _box.union(_b2);
    });
    _box.getSize(_sz);
    const len = Math.abs(_box.min.y);
    return { len: Number.isFinite(len) && len > 1e-6 ? len : 0.5,
             wid: Math.max(_sz.x, _sz.z) || 0.2,
             cx: (_box.min.x + _box.max.x) / 2 || 0, along: 'y' };
  }



  function addSplotch(part, t /* 0 = shoulder-end, 1 = hand-end */) {
    const holder = new THREE.Group();       // identity scale, ALWAYS
    node[part].add(holder);
    // MEASURED, not guessed. Kenney's arm bounding box is 0.4 x 1.1 x 0.4
    // units. A hardcoded 1.6 offset (a plausible-looking number) put t=0.78
    // at y=-1.25 -- past the hand entirely, landing dirt on the SHORTS.
    // Caught only by screenshotting the running page; every syntax and
    // console check passed. Keep splotches inside [0.20, 0.85] of the limb
    // so they read as "on the forearm" rather than at a joint. (This said
    // [0.15, 0.85] while the comment four lines below said [0.20, 0.85] and
    // BOTH code paths use 0.20 + t*0.65 -- the code settles it.)
    // THE PARAGRAPH BELOW DESCRIBES THE RIGID PACK — THE `else` FALLBACK, NOT
    // THE SHIPPED PATH. Re-checked: the shipped model is the skinned
    // mini pack (skins=2), whose arm bone runs along +X, so limbLocalBox()
    // measures the axis instead of assuming one (see the note at limbAxis, which
    // records the same split). Kept because the -Y branch below is still live:
    // limbLocalBox's rigid return supplies `along:'y'` and NO `from`, and the
    // branch test gates on `L.from`, so a rigid pack lands here.
    // MEASURED node-local ON THE RIGID PACK: arm runs y=+0.1 .. y=-1.0
    // (len 1.0), width 0.4. The pivot is at the SHOULDER, so the limb extends
    // in -Y — which is why REST is (0,-1,0). Keep splotches inside
    // [0.20, 0.85] of the limb so they sit on the forearm and not on a joint.
    const { len, wid, cx } = limbLocalBox(part);
    // The limb mesh is NOT centred on its pivot: arm-left's geometry runs
    // x from 0.0 to +0.4 (centre +0.2), arm-right from -0.4 to 0.0. Placing
    // the sprite at local x=0 puts it on the INNER edge, against the torso --
    // measured in screen space as x=688 while the arm spans x=[692,755],
    // i.e. 4px short of the limb every time. Offset to the mesh's own centre
    // and stand off along +Z only slightly.
    // STAND OFF TOWARD THE CAMERA. This was +Z, which faced the camera until
    // the root was rotated 180 degrees to show the character's FRONT -- after
    // that the splotches sat at world z=-0.14 with the arm at z=+0.13, i.e.
    // BEHIND the limb, invisible. All three sprites reported visible, in-scene
    // and not-gone the whole time; only their world positions showed it.
    // PLACE ALONG THE LIMB'S MEASURED AXIS. This was hardcoded to -Y, which
    // is right for the rigid pack (limbs hang down) and wrong for the skinned
    // mini pack, whose arm bone runs along +X -- every splotch landed down by
    // the character's FEET. limbLocalBox() now reports which axis the limb
    // actually spans and where it starts and ends.
    const L = limbLocalBox(part);
    if (L.along && L.from !== undefined) {
      const u = L.from + (0.20 + t * 0.65) * (L.to - L.from);
      holder.position.set(L.offA, L.offB, L.offC);
      holder.position[L.along] = u;
      // STAND OFF BY THE LIMB'S OWN DEPTH, not by `wid`. This was
      // `L.wid * 0.6`, and `wid` is the LARGER of the two cross-axis spans --
      // on the shipped arm that is the y span, so the z push was 0.133 against
      // a limb whose z half-span is only 0.111. The sprite sat further from
      // the bone axis than the arm is thick.
      //
      // WHAT THAT LOOKED LIKE: dirt on the torso and the left thigh, on all
      // twelve characters. Raycast through each splotch's own screen position
      // with the sprites hidden, and ask which bone owns the triangle it hits:
      // eleven of twelve returned `torso` or `leg-left`, not `arm-left`.
      //
      // WHY NOBODY SAW IT: the placement test measures perpendicular distance
      // in SCREEN space, and from the old near-head-on camera this offset
      // projected to 1-3px and passed. Swinging the wide shot round to show
      // the wheelchair turned the same offset into 14-15px and the test went
      // red. The bug is older than the camera change; the camera change is
      // only what made it visible. Do not "fix" this by widening the test.
      //
      // THE HOLDER SITS ON THE LIMB'S AXIS. No fixed stand-off here: the
      // sprite is pushed toward the CAMERA every frame instead, by
      // faceSplotches() below. See the note there for why a constant local
      // offset cannot work.
    } else {
      holder.position.set(cx, -(0.20 + t * 0.65) * len, -wid * 0.55);
    }
    const s = new THREE.Sprite(new THREE.SpriteMaterial({
      // Index by this splotch's position: splotches.push(rec) happens below,
      // AFTER the sprite is built, so length here is this splotch's own index.
      // Round-robin so a 4th splotch (UV mode can place more) reuses shape 0
      // rather than reading past the end.
      map: dirtTex[splotches.length % dirtTex.length],
      transparent: true, depthWrite: false }));
    // Sized to the LIMB, not an arbitrary scalar: a 0.45 sprite on a
    // 0.4-wide arm spills over both edges and reads as floating.
    // 0.78 -> 0.32, RE-SOLVED AGAINST THE NEW FRAMING. MEASURED at 1080p:
    // the three splotches span only 58px of screen while each sprite was 57px
    // tall, so they formed ONE contiguous brown mass -- 22,756 dirt pixels in a
    // 260x205 union with ZERO column gaps. Not a shape problem: I gave each
    // splotch its own silhouette first (three distinct textures, verified) and
    // the mass did not break up, because at 29px spacing each outline falls
    // INSIDE its neighbour and only the union's boundary is ever visible.
    //
    // "UNDER THE SPACING GIVES A GAP" IS THE WRONG RULE. It read: a gap needs
    // the sprite under the 29px neighbour spacing, i.e. wid*0.39 or less, and
    // 0.32 gives 23px with 6px of clear arm. MEASURED at the three framings
    // test_projector.py runs -- px/world-unit is 385.4 / 256.9 / 211.2, never
    // the flat 227 the sizing doc assumed, so 0.32 paints 26.9 / 18.0 / 14.8 px
    // against spacing 29 / 19 / 16 px. At 1080p 26.9 IS under 29, so the rule
    // predicts clear arm; sampling painted pixels (colour taken from the three
    // splotch centres) finds ONE contiguous run at every framing, zero column
    // gaps. These textures are LOBED: what they paint reaches past the box they
    // are sized by, so narrower-than-spacing was never sufficient. What
    // actually separates them is the per-splotch silhouette -- see the note at
    // the texture builder, which says outright that no combination of placement
    // and scale leaves a gap. The scale below is unchanged and still correct.
    //
    // Legibility does NOT drop by the same ratio: 0.78 was tuned when the
    // character filled 36%
    // of frame height, and the FOV 42->26 reframe now puts it at ~54%, so a 23px
    // splotch subtends about what a 35px one did before. The limb is 81px wide
    // on screen, so this also keeps dirt ON the arm rather than spilling over
    // both edges -- the original reason 0.78 was picked.
    s.scale.setScalar(wid * 0.32);
    holder.add(s);
    // FOAM RIDES THE SAME HOLDER. holder is already parented to node[part] and
    // positioned along the limb's measured axis, so this inherits placement for
    // free -- the reason the sizing doc chose it over sprite.getWorldPosition(),
    // which on the degraded stub (no holder) would pile every foam sprite at the
    // hardcoded (0, 1.5, 0) instead of failing silent.
    //
    // SIZED, not guessed: the splotch is wid*0.32 = 20.4 px at this framing, so
    // foam at 1.15x is 23 px -- inside the 10-31 px band the sizing pass set, and
    // well clear of the 7.6 px anticipation that was reverted as invisible.
    // Slightly LARGER than the splotch so it reads as covering it.
    const foam = new THREE.Sprite(new THREE.SpriteMaterial({
      map: foamTex[splotches.length % foamTex.length],
      transparent: true, depthWrite: false, opacity: 0 }));
    foam.scale.setScalar(wid * 0.32 * 1.15);
    // NO FIXED BIAS HERE ANY MORE. This was `foam.position.z += 0.004`, which
    // faceSplotches() now overwrites every frame with a copy(), so the line
    // was dead. The foam gets the dirt's camera-ward offset times 1.03
    // instead, which puts it in front of the splotch from EVERY angle rather
    // than only from the one bone-local +Z used to face.
    holder.add(foam);
    // `standoff` is how far toward the camera the sprite is lifted off the
    // limb's axis each frame -- the limb's own half-depth, so the dirt clears
    // the near surface without ever leaving the silhouette.
    const rec = { sprite: s, holder, part, t, gone: false,
                  // THE TWO CROSS AXES, chosen from the MEASURED long axis
                  // rather than hardcoded to Y and Z. On the shipped arm the
                  // limb runs along x, so y and z are the cross axes and the
                  // hardcoded version happened to be right -- but a rig whose
                  // limb runs along y would have had its own half-LENGTH used
                  // as a stand-off, flinging the dirt clear of the body.
                  standoff: (() => {
                    const half = { x: L.halfX, y: L.halfY, z: L.halfZ };
                    const cross = ['x', 'y', 'z'].filter(a => a !== L.along)
                                                 .map(a => half[a] || 0);
                    return Math.max(...cross) || wid * 0.5;
                  })(),
                  baseScale: wid * 0.32, foam, foamBase: wid * 0.32 * 1.15 };
    splotches.push(rec);
    return rec;
  }

  /** WHERE IS t ON THIS LIMB, in world space? -> THREE.Vector3, or null.
   *
   *  THE SPLOTCHES' OWN MAPPING, LIFTED OUT SO THERE IS ONLY ONE. addSplotch
   *  above places dirt at `L.from + (0.20 + t*0.65) * (L.to - L.from)` along
   *  the limb's MEASURED long axis, and the robot arm now has to put its
   *  sponge at the same t so it visibly travels the forearm. Two copies of
   *  that expression would drift the first time anyone retunes the 0.20/0.65
   *  inset, and the symptom would be a sponge scrubbing next to the dirt
   *  rather than on it -- visible from ten feet and invisible in a diff.
   *
   *  The inset is not cosmetic and is why this cannot be a plain lerp: t=0 is
   *  the SHOULDER end of the bone but dirt (and the sponge) belong on the
   *  forearm, so the usable span is the middle 65% of the limb.
   *
   *  A scratch vector is not reused here: callers hold the result across
   *  frames, and the one place that does not (the arm's per-frame solve) does
   *  its own copy. */
  function limbPointAt(part, t, out) {
    if (!node[part]) return null;
    const L = limbLocalBox(part);
    const v = out || new THREE.Vector3();
    if (L.along && L.from !== undefined) {
      v.set(L.offA, L.offB, L.offC);
      v[L.along] = L.from + (0.20 + t * 0.65) * (L.to - L.from);
    } else {
      // The rigid pack's branch, identical to addSplotch's `else`.
      v.set(L.cx, -(0.20 + t * 0.65) * L.len, -L.wid * 0.55);
    }
    node[part].updateWorldMatrix(true, false);
    return v.applyMatrix4(node[part].matrixWorld);
  }

  /** Suds build-up: foam opacity/scale rise with cleanliness (0..1).
   *
   *  WHY A LEVEL AND NOT AN EVENT: the demo's dead air is the several seconds of
   *  rubbing BETWEEN pops, which sudsAt already covers with transients. What the
   *  frame lacks is any persistent sign that progress accumulated. A level driven
   *  by cleaned/total shows that without competing with the pop.
   *
   *  Foam grows on the splotches still PRESENT, which is what a judge sees: the
   *  remaining dirt gets progressively soapier as the arm works. A popped splotch
   *  takes its foam with it (same holder, same visibility).
   */
  function setSuds(level) {
    const L = Math.min(1, Math.max(0, Number(level) || 0));
    for (const r of splotches) {
      if (!r.foam) continue;              // stub avatar has no foam sprite
      r.foam.material.opacity = 0.85 * L;
      r.foam.scale.setScalar(r.foamBase * (0.72 + 0.28 * L));
      r.foam.visible = L > 0.02 && !r.gone;
    }
  }

  function resetSplotches() {
    for (const r of splotches) {
      // KILL the in-flight pop tween FIRST. gsap's onComplete sets
      // visible=false; if a reset lands mid-tween the orphaned callback fires
      // afterwards and re-hides a splotch you just restored -- and its
      // remaining ticks overwrite the restored scale. On stage that is a
      // splotch that vanishes by itself between demo runs.
      if (window.gsap) gsap.killTweensOf(r.sprite.scale);
      r.gone = false;
      r.sprite.visible = true;
      r.sprite.scale.setScalar(r.baseScale);
      // FOAM MUST RESET TOO. resetSplotches restores the dirt for the next demo
      // run; foam left behind would carry the previous run's cleanliness into a
      // fresh 0% -- the same shape as the orphaned-tween bug above, where state
      // from one run reappeared in the next.
      if (r.foam) {
        if (window.gsap) gsap.killTweensOf(r.foam.material);
        r.foam.material.opacity = 0;
        r.foam.scale.setScalar(r.foamBase);
        r.foam.visible = false;
      }
    }
  }

  /** Release a held pose and hand the body back to the springs.
   *  Only 'die' clamps (see playOnce), and clamping is a state nothing else
   *  clears: measured, 'r' left the character face-down at deviation 0.2978
   *  because resetAll() only ever touched splotches and the counter. 'r' is
   *  the key the recovery card documents as the reset, so an operator who
   *  triggers the gag and reaches for it must get their character back. */
  function standUp() {
    if (!oneShot) return false;
    oneShot.stop();
    oneShot.enabled = false;
    oneShot = null;
    return true;
  }

  /** Sit the character in a chair, or stand it back up.
   *
   *  The chair is NOT placed here. This only poses the body: the legs fold at
   *  the hip and the root drops to seat height. Whoever owns the scene places
   *  the wheelchair mesh, because the offset is per-chair and this file has no
   *  business knowing which chair is on stage.
   *
   *  Returns the world Y the seat surface must be at to meet the body, so the
   *  caller can position the chair against a measured number instead of
   *  eyeballing it -- the exact step the earlier attempt skipped when it
   *  "fitted the chair to 0.75 of body height" and got a chair worn like a
   *  belt.
   */
  function setSeated(on) {
    if (standY === null) standY = gltf.scene.position.y;   // see the note above
    seated = !!on;
    if (!seated) {
      node['leg-left'].rotation.set(0, 0, 0);
      node['leg-right'].rotation.set(0, 0, 0);
      gltf.scene.position.y = standY;
      return standY;
    }
    gltf.scene.position.y = standY - SEAT_DROP;
    return standY - SEAT_DROP;
  }

  // ---- ACCESSORY LOADING ------------------------------------------------
  // Lazy and cached: the props are loaded the first time one is asked for, so
  // a demo that never presses the key pays nothing, and cycling back to a prop
  // already seen costs no fetch. Same shape as the character cast, which also
  // loads on demand.
  const accLoaded = {};          // file -> Object3D, or null if it failed
  const accHolders = {};         // bone name -> identity-scale Group
  let accIdx = -1;               // -1 is "nothing worn", the default
  let accCurrent = null;

  /** Show accessory `i` and hide whatever was on. -1 wears nothing.
   *
   *  A MISSING PROP MUST NOT COST THE DEMO. Every load is guarded and a
   *  failure caches null, so a 404 on one file shows nothing for that step and
   *  the next press moves on -- rather than throwing inside a keydown handler,
   *  which on this page kills the whole keyboard.
   */
  let accGen = 0;                // which request is the current one

  async function wearAccessory(i) {
    // HIDE EVERY LOADED PROP, NOT JUST THE TRACKED ONE. This function awaits a
    // load, so two quick presses interleave: the second runs its hide while
    // the first is still awaiting, so `accCurrent` is still null and hides
    // nothing -- then BOTH awaits resolve and set their own prop visible.
    // Measured in a screenshot: two pairs of glasses at once, one on the eyes
    // and one floating above the head, while every assertion passed.
    for (const o of Object.values(accLoaded)) if (o) o.visible = false;
    accCurrent = null;
    accIdx = i;
    // A STALE LOAD MUST NOT WIN. Same reason: the older request's await can
    // resolve after the newer one already finished, and without this token it
    // would show the prop the operator has already pressed past.
    const gen = ++accGen;
    if (i < 0 || i >= ACCESSORIES.length) return null;
    const a = ACCESSORIES[i];
    if (accLoaded[a.file] === undefined) {
      try {
        const g = await new GLTFLoader().loadAsync(`./assets/${a.file}.glb`);
        const o = g.scene;
        o.scale.setScalar(a.scale ?? 1);
        o.position.set(a.pos[0], a.pos[1], a.pos[2]);
        if (a.tilt) o.rotation.z = a.tilt;
        // KEEP THE PACK'S OWN MATERIALS. The twelve characters keep theirs --
        // avatar.js never swaps a character material, it repaints the shared
        // texture atlas instead -- and the props were authored against the same
        // atlas by the same artist. Substituting a material here would make a
        // prop the one thing on the character shaded differently.
        o.traverse((m) => { if (m.isMesh) m.castShadow = true; });
        // AN IDENTITY-SCALE HOLDER, exactly as addSplotch uses. The spring
        // writes non-uniform scale onto the torso and head BONES on every
        // squash -- 0.10 on the torso for each splotch pop, visible about
        // 880ms -- and scale inherits, so a prop parented straight to the
        // bone bulges sideways and squashes down with it. The holder absorbs
        // the rotation (which the prop SHOULD follow) and keeps the scale at
        // identity (which it should not).
        const parent = node[a.at] || gltf.scene;
        let holder = accHolders[a.at];
        if (!holder) {
          holder = accHolders[a.at] = new THREE.Group();   // identity scale, ALWAYS
          parent.add(holder);
        }
        holder.add(o);
        accLoaded[a.file] = o;
      } catch (e) {
        console.warn('accessory failed, skipping:', a.file, e);
        accLoaded[a.file] = null;
      }
    }
    // Another press landed while this one was loading: that request owns the
    // screen now, so this one finishes silently rather than overriding it.
    if (gen !== accGen) return null;
    const o = accLoaded[a.file];
    if (o) { o.visible = true; accCurrent = o; }
    return o;
  }

  /** Cycle nothing -> each prop -> nothing. Mirrors nextSkin/nextOutfit. */
  function nextAccessory() {
    const n = ACCESSORIES.length;
    const next = accIdx + 1 > n - 1 ? -1 : accIdx + 1;
    wearAccessory(next);
    return next;
  }

  return {
    root: gltf.scene, node, splotches, addSplotch, resetSplotches, setSuds,
    /** The splotches' own placement mapping, for anything that has to meet
     *  them on the limb. See limbPointAt for why it is shared rather than
     *  copied. */
    limbPointAt,
    playOnce, animationNames, standUp, setSeated,
    /** Outfit + skin recolouring. See the RECOLOURING block above for how the
     *  atlas is laid out and why the ramp is replaced rather than hue-rotated. */
    nextSkin, nextOutfit, setRegion,
    /** The eight vendored accessibility props. See ACCESSORIES. */
    nextAccessory, wearAccessory, accessoryCount: ACCESSORIES.length,
    accessoryNames: ACCESSORIES.map((a) => a.file),
    skinCount: SKIN_TONES.length, outfitCount: OUTFITS.length,
    /** Read or set the spring constants, so the feel can be MEASURED through
     *  the shipped integrator instead of by editing this file between trials.
     *  Same reason startScrubChoreography and oneShotTime are exposed: a probe
     *  that reimplements the thing it is measuring proves nothing about the
     *  thing that ships. Call with no argument to read.
     *      springConst()                  -> {k, damp, maxStep}
     *      springConst({damp: 0.90})      -> sets damp, returns the new set  */
    springConst(next) {
      if (next && typeof next === 'object') Object.assign(SPRING, next);
      return { k: SPRING.k, damp: SPRING.damp, maxStep: SPRING.maxStep };
    },
    /** The running one-shot's playback head, so a test can see a RESTART.
     *  Comparing poses cannot: 120ms of a clip looks the same whether it just
     *  restarted or is simply progressing. */
    oneShotTime: () => (oneShot && oneShot.isRunning() ? +oneShot.time.toFixed(4) : null),
    /** The cast, and which one is showing -- so the page can cycle them. */
    cast: CAST, current: pick,

    /** call every frame; wl = worldLandmarks[0] or null */
    /** Lift every splotch off its limb's axis TOWARD THE CAMERA.
     *
     *  WHY THIS IS PER-FRAME AND NOT A CONSTANT OFFSET. The holder is
     *  parented to the bone, so any fixed local push rotates with the limb.
     *  It was `holder.position.z += wid * 0.6`, which meant "toward the
     *  viewer" only for one particular limb angle and one particular camera.
     *  Two things broke it: `wid` is the LARGER of the two cross-axis spans,
     *  so on the shipped arm the push was 0.133 against a limb only 0.111
     *  deep; and bone-local +Z stopped pointing at the camera.
     *
     *  WHAT THAT LOOKED LIKE: dirt on the torso and the left thigh, on all
     *  twelve characters. Cast a ray through each splotch's own screen
     *  position with the sprites hidden and ask which bone owns the triangle
     *  it hits -- every one returned `torso` or `leg-left`, never `arm-left`.
     *  Zeroing the push put all 36 back on the arm but buried two of three
     *  INSIDE it, 0.23 to 0.30 behind the near surface, so the dirt vanished.
     *
     *  Pushing along the camera vector is the only version that is correct
     *  for every limb angle and every shot, which now matters because the
     *  wide shot no longer looks at the character head on.
     *
     *  NOT a billboard rotation: sprites already face the camera. This moves
     *  the sprite's POSITION so the limb's own surface cannot depth-test it
     *  away (the material is depthWrite:false, but depthTest is still on).
     */
    faceSplotches(camera) {
      if (!camera) return;
      for (const r of splotches) {
        if (!r.holder || !r.sprite) continue;
        r.holder.getWorldPosition(_axisW);
        _toCam.copy(camera.position).sub(_axisW);
        const d = _toCam.length();
        if (!(d > 1e-6)) continue;            // camera sitting on the splotch
        // `standoff` IS A WORLD LENGTH. _toCam is a world vector, so scaling
        // it by standoff/d makes a world-space displacement of exactly
        // `standoff`. The inverse below converts it into the holder's frame,
        // and the holder's own matrix converts it back when the sprite is
        // drawn, so the round trip returns the same world length -- exactly,
        // including under the squash-and-stretch scale the torso applies.
        // Do NOT "correct" this by multiplying by the rig's world scale: the
        // inverse already carries it, and doing it twice overshoots.
        _toCam.multiplyScalar(r.standoff / d);
        // Back into the holder's frame, because the sprite is its child.
        _m3.copy(r.holder.matrixWorld).invert();
        // LINEAR PART ONLY. M = T*A, so M^-1 = [A^-1 | -A^-1 t]; zeroing the
        // translation column leaves [A^-1 | 0], which applyMatrix4 (implicit
        // w=1, adds elements 12/13/14) then treats as a pure rotate-and-scale.
        // Without this the offset would also be translated to the origin.
        _off.copy(_toCam).applyMatrix4(_m3.setPosition(0, 0, 0));
        r.sprite.position.copy(_off);
        if (r.foam) r.foam.position.copy(_off).multiplyScalar(1.03);
      }
    },

    update(wl, dt, now) {
      if (wl && wl.length > LM.R_HIP) {
        // ORIENTATION ASSERT — catches the upside-down bug on frame one
        // instead of at hour 20. Google never documented the axis convention,
        // so this checks it empirically: a standing person's shoulders must
        // be ABOVE their hips after conversion.
        if (!checkedOrientation) {
          const shY = (V(wl[LM.L_SH]).y + V(wl[LM.R_SH]).y) / 2;
          const hipY = (V(wl[LM.L_HIP]).y + V(wl[LM.R_HIP]).y) / 2;
          if (shY <= hipY) {
            console.error(
              `AVATAR UPSIDE DOWN: shoulderY=${shY.toFixed(3)} <= ` +
              `hipY=${hipY.toFixed(3)}. Flip the Y sign in V().`);
          } else {
            console.log(`avatar orientation OK (shoulderY ${shY.toFixed(2)} > ` +
                        `hipY ${hipY.toFixed(2)})`);
          }
          checkedOrientation = true;
        }

        lastSeen = now;
        if (idling) { idle?.fadeOut(0.25); idling = false; }
        // aim() is called AFTER mixer.update() at the bottom of this method --
        // see the comment there. Calling it here would be overwritten.
        // squash-and-stretch: the wobble that sells "goofy" over "broken"
        node.torso.scale.y = 1 + Math.sin(now * 8) * 0.06;
      } else if (!idling && now - lastSeen > 0.3) {
        // Tracking loss looks like an INTENTIONAL character beat, not a
        // frozen demo. Ten lines, and the cheapest stage insurance available.
        idle?.reset().fadeIn(0.25).play();
        idling = true;
      }

      // THE MIXER MUST RUN *BEFORE* aim(), NOT AFTER.
      // The idle clip animates the SAME node quaternions the pose sets, so
      // calling mixer.update() last overwrote every tracked limb on every
      // frame. Measured: swinging the injected forearm from vertical to
      // horizontal -- which the math says is a 0.707 quaternion change --
      // moved the rendered arm by 0.008. The cartoon would have played its
      // idle animation while a person waved at it, and nothing anywhere would
      // have errored. Invisible until a real human stood in front of it.
      //
      // Order is: advance the clip, THEN stamp the tracked pose over it.
      mixer.update(dt);
      if (wl && wl.length > LM.R_HIP) {
        aim('arm-right', V(wl[LM.R_EL]), V(wl[LM.R_WR]));
        aim('arm-left',  V(wl[LM.L_EL]), V(wl[LM.L_WR]));
        // LEGS TOO. Same aim() path, same NaN guard, same springs -- MediaPipe
        // was already sending knees and ankles and nothing consumed them. Costs
        // two lines and makes the mirroring read as the whole body.
        if (wl.length > LM.R_ANK) {
          aim('leg-right', V(wl[LM.R_KNEE]), V(wl[LM.R_ANK]));
          aim('leg-left',  V(wl[LM.L_KNEE]), V(wl[LM.L_ANK]));
        }
        // THE TORSO IS THE SECRET WEAPON. arm-left, arm-right and head are
        // its CHILDREN in this GLB, so any torso motion propagates into the
        // whole upper body for free -- three sticks become one loose object.
        // That inherited follow-through is the "I can feel the weight"
        // quality; without it the limbs wobble independently and it reads as
        // three broken parts rather than one heavy character.
        const shL = V(wl[LM.L_SH]), shR = V(wl[LM.R_SH]);
        _d.subVectors(shL, shR);
        if (_d.lengthSq() > 1e-8) {
          // lean the torso with the shoulder line, softly
          const lean = Math.atan2(_d.y, _d.x) * 0.35;
          spring.torso.target.setFromAxisAngle(FWD, lean);
        }
      }

      // dt can be huge on the first frame or after a tab is backgrounded, and
      // a huge dt turns the spring into an explosion. Clamp it.
      const h = Math.min(dt || 0.016, 0.05);

      // ALWAYS-ON BREATH. A character that is perfectly still between scrubs
      // looks paused, not alive -- Gang Beasts characters never stop teetering
      // and that is most of why they read as characters. Tiny, constant, and
      // it costs nothing.
      const t = now;
      // TUNED FOR A PROJECTOR, not for a monitor at arm's length. The first
      // pass used 0.05 and measured a torso drift of 0.001 over 1.8s --
      // technically alive, invisible from ten feet. These read across a room.
      spring.torso.vel.x += Math.sin(t * 1.7) * 0.55 * h;
      spring.torso.vel.z += Math.cos(t * 1.1) * 0.45 * h;
      spring.head.vel.z  += Math.sin(t * 2.3 + 1) * 0.70 * h;
      spring.head.vel.x  += Math.cos(t * 1.9)     * 0.50 * h;
      // THE ARMS MUST BREATHE TOO. The torso and head swayed while the arms
      // sat perfectly still: with no tracked pose their spring target never
      // changes, so they held their rest quaternion exactly. Correct spring
      // behaviour, wrong look -- a character whose arms are frozen while its
      // body sways reads as broken, not idle. Measured before this: arm-left
      // quaternion identical across 1.5s, to five decimals.
      // Offset phases so the two arms never move in lockstep.
      spring['arm-left'].vel.z  += Math.sin(t * 1.3)       * 0.42 * h;
      spring['arm-left'].vel.x  += Math.cos(t * 0.9 + 0.7) * 0.30 * h;
      spring['arm-right'].vel.z += Math.sin(t * 1.3 + 2.1) * 0.42 * h;
      spring['arm-right'].vel.x += Math.cos(t * 0.9 + 2.8) * 0.30 * h;

      // A PLAYING CLIP OWNS THE PARTS IT ANIMATES. stepSpring() writes
      // .quaternion on every part every frame, so it overwrote the mixer's
      // output and the one-shots were dead on arrival -- measured: 'emote-yes'
      // moved the head by 0.0042, i.e. the spring's own breath sway and
      // nothing else. This is the SAME trap the mixer-ordering comment below
      // documents, reached from the other side.
      //
      // While a one-shot runs, skip the spring for the TORSO/HEAD (the parts
      // that carry an emote) and let the clip through. The ARMS keep their
      // springs, because they must stay honest to the tracked person -- the
      // character celebrates from the waist up while its arms still mirror you.
      // SEATED MODE OWNS THE LEGS, exactly the way a running clip owns the
      // torso. The note above ("SEATED MODE: TRIED AND CUT") is correct that
      // no shipped clip seats this rig -- re-measured on the mini pack:
      // 'wheelchair-sit' moves the head 0.0030 and the legs and torso 0.0000,
      // so the clips rotate nothing that matters and the body never lowers.
      //
      // What that note missed is that the rig HAS addressable leg bones, and
      // the spring loop already has a first-class way to yield a part to
      // another writer. So seating is not "authoring a pose" -- it is two
      // rotations plus the same ownership handshake the one-shots use. The
      // first attempt wrote the rotations from outside and they vanished,
      // because stepSpring() rewrites .quaternion on every part every frame:
      // measured legY delta 0.0000 with the root visibly lowered, i.e. the
      // character sank through the floor with its legs still standing.
      //
      // The arms are DELIBERATELY not owned here. They must keep mirroring
      // the real person while she sits, which is the entire privacy pitch.
      const clipRunning = (oneShot && oneShot.isRunning());
      const clipOwns = (clipRunning || seated)
        ? (clipRunning ? new Set(['torso', 'head', 'leg-left', 'leg-right'])
                       : new Set(['leg-left', 'leg-right']))
        : null;
      if (seated && !clipRunning) {
        // Hip flexion toward the chair. Negative X folds the thigh FORWARD on
        // this rig (+X folded them backwards through the seat).
        node['leg-left'].rotation.set(-SEAT_HIP, 0, 0);
        node['leg-right'].rotation.set(-SEAT_HIP, 0, 0);
      }
      for (const n of PARTS) {
        if (clipOwns && clipOwns.has(n)) {
          // keep the spring's state in sync so there is no jump on release
          spring[n].cur.copy(node[n].quaternion);
          spring[n].vel.set(0, 0, 0);
          continue;
        }
        stepSpring(n, h);
      }

      // SQUASH AND STRETCH. Cartoon timing, not simulation: compress along the
      // limb's own axis and spring back over ~150ms. This is what sells a
      // splotch pop from thirty feet away on a projector.
      for (const n of PARTS) {
        const S = spring[n];
        if (S.stretch > 0.001) {
          // 0.02 -> 0.08. THE OLD COMMENT SAID "~150ms to nothing" AND WAS
          // WRONG: stepping the shipped expression from the pop's 0.18, decay
          // 0.02 stays visible (above 2% scale) for ~567ms and is fully gone
          // at ~1333ms. 0.08 holds it to ~883ms, which is the linger Gang
          // Beasts and Peak get their comedy from, and still clears well
          // before the next splotch pops ~2.5s later.
          S.stretch *= Math.pow(0.08, h);        // visible ~880ms, gone ~2.1s
          node[n].scale.set(1 + S.stretch * 0.5, 1 - S.stretch,
                            1 + S.stretch * 0.5);
        } else if (S.stretch !== 0) {
          S.stretch = 0;
          node[n].scale.set(1, 1, 1);
        }
      }
    },

    /** Shove a limb -- the sponge hitting the arm. See impulse(). */
    impulse,
    /** Cartoon compress-and-rebound on a limb. */
    squash,
  };
}

// Procedural dirt — no PNG to 404, no path to get wrong.
function makeDirtTexture(seed = 0) {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const x = c.getContext('2d');
  // HIGH CONTRAST, not "realistic dirt". A brown blob on Kenney's brown
  // sleeve is invisible on a projector from ten feet -- verified by
  // screenshot. This is the demo's money shot; it must read INSTANTLY. Dark
  // near-black core with a bright rim so it pops against any skin/clothing.
  // RE-TUNED FOR THE FLAT PALE ARM. These stops were chosen against Kenney's
  // dark brown sleeve; on the new #f6cd96 skin they rendered as washed-out
  // beige-on-beige and were barely readable -- the exact failure going flat was
  // meant to fix. Verified by screenshot, not by eye on the swatch.
  const g = x.createRadialGradient(64, 64, 6, 64, 64, 60);
  g.addColorStop(0,    'rgba(28,20,14,1)');
  g.addColorStop(0.55, 'rgba(46,32,20,0.98)');
  g.addColorStop(0.86, 'rgba(70,50,30,0.92)');
  g.addColorStop(1,    'rgba(70,50,30,0)');
  x.fillStyle = g;
  // Irregular blob, not a circle — a perfect circle reads as a UI element.
  //
  // PER-SPLOTCH SILHOUETTE. Every sprite used to share ONE texture object, so
  // three IDENTICAL images sat 28px apart on a 1080p projector and merged into
  // a single brown smudge -- the demo's money shot reading as one mark instead
  // of three dirty spots.
  //
  // Spatial separation cannot fix it, and I ran the whole matrix before
  // concluding that: inside the [0.20, 0.85] limb band (off the elbow and wrist
  // joints) NO combination of placement and scale leaves a gap, because the
  // sprites are ~57px tall and neighbours are ~28px apart. The only pairs that
  // reach a 0-2px gap need wid*0.42, a 31px splotch that is unreadable from ten
  // feet. Widening the PHYSICAL stroke would spread them, but py/scrubbot.py's
  // own note records that splotches outside the sponge's travel "could NEVER be
  // reached and the demo stalled at 33% with the operator faking the rest".
  //
  // Different SHAPES read as separate even when they overlap. Verified by
  // rendering the three seeds and measuring column coverage: the signatures
  // differ by 987-1549px, and by eye they are a lobed blob, a wide flat one and
  // a spiky one.
  const [f1, f2, a1, a2, rot] = [[3.1, 5.3, 9, 6, 0.0],
                                 [2.3, 4.1, 12, 5, 0.9],
                                 [4.7, 6.2, 7, 9, 1.9]][seed % 3];
  x.beginPath();
  for (let i = 0; i <= 24; i++) {
    const a = (i / 24) * Math.PI * 2 + rot;
    const r = 46 + Math.sin(a * f1) * a1 + Math.cos(a * f2) * a2;
    x[i ? 'lineTo' : 'moveTo'](64 + Math.cos(a) * r, 64 + Math.sin(a) * r);
  }
  x.closePath(); x.fill();
  const t = new THREE.CanvasTexture(c);
  // TAG THE COLOUR SPACE. An untagged texture is treated as LINEAR, so the
  // authored gradient stops above are not what reaches the screen -- the
  // renderer's output conversion brightens them. Measured on the character
  // crop: mean luminance of the affected pixels 16.6 untagged vs 14.7 tagged,
  // over ~24k pixels. Small, because the blob is near-black either way; the
  // audit that raised this predicted "washed-out beige", which the pixels
  // refute. Tagged anyway so the canvas renders as authored.
  t.colorSpace = THREE.SRGBColorSpace;
  t.needsUpdate = true;
  return t;
}

/** Foam for the suds build-up. Same procedural route as makeDirtTexture, and for
 *  the same reason: web/assets carries no reusable sprite, and a new texture FILE
 *  would sit OUTSIDE the six-file backup-video digest (main/avatar/juice/robotarm
 *  .js, index.html, hud.css) -- so a foam change would not mark the video stale.
 *  A canvas built here is inside that digest.
 *
 *  BRIGHT, not white. The arm is #f6cd96; pure white foam on a pale limb is the
 *  beige-on-beige failure makeDirtTexture's own comment records, inverted. Cool
 *  highlights (#d7f2ff, #bfe6ff) match sudsAt's palette so the transient bubbles
 *  and the persistent build-up read as the same substance.
 */
function makeFoamTexture(seed = 0) {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const x = c.getContext('2d');
  // Clustered bubbles, not one blob: foam reads as many small highlights. Three
  // seeds so neighbouring splotches do not share a silhouette -- the same
  // failure that made three identical dirt sprites merge into one smudge.
  const rings = [[[64, 62, 34], [44, 48, 20], [88, 52, 17], [58, 88, 22]],
                 [[62, 66, 32], [90, 60, 19], [40, 60, 22], [70, 40, 15]],
                 [[66, 60, 30], [46, 76, 21], [86, 78, 18], [56, 42, 16]]][seed % 3];
  for (const [cx, cy, r] of rings) {
    const g = x.createRadialGradient(cx - r * 0.3, cy - r * 0.3, 1, cx, cy, r);
    g.addColorStop(0,    'rgba(255,255,255,0.95)');
    g.addColorStop(0.45, 'rgba(215,242,255,0.72)');
    g.addColorStop(0.85, 'rgba(191,230,255,0.34)');
    g.addColorStop(1,    'rgba(191,230,255,0)');
    x.fillStyle = g;
    x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.fill();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.needsUpdate = true;
  return t;
}
