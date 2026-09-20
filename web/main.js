// web/main.js — the projector page's brain. No build step, no CDN, no framework.
import * as THREE from 'three';
import { OutlineEffect } from 'three/addons/OutlineEffect.js';
import { FilesetResolver, PoseLandmarker } from './vendor/vision_bundle.mjs';
import { GLTFLoader } from 'three/addons/GLTFLoader.js';
import { makeAvatar } from './avatar.js';
import { makeRobotArm, loadArmGeometry } from './robotarm.js';
import { makeTerritories } from './territories.js';
import { makeVoice, say } from './voice.js';
import { makeVitals } from './vitals.js';
import { makeCoverage } from './coverage.js';
import { initShapes, popSplotch, setClean, borrowPct, resetCounter, finale, unlockAudio,
         sprayAt,
         onCleanPct,
         sudsAt, play } from './juice.js';

// CANCEL THE BOOT WATCHDOG. index.html arms a 6s classic-script timer that
// reveals #bootfail ("run ./vendor.sh") when NO module evaluates. Reaching this
// line proves every bare-specifier import above resolved, which is the exact
// condition the watchdog exists to detect -- so it is cancelled here, before any
// other work, rather than at the end of a boot that loads a GLB and a pose model.
// Placed AFTER the imports on purpose: a missing `three` fails at line 2 and this
// never runs, which is what makes the notice appear.
clearTimeout(window.__bootTimer);

const scene = new THREE.Scene();
// FRAMING measured against the real model: Kenney's character bounding box is
// 1.6 x 2.7 x 0.8 units, scaled 1.2 -> ~3.24 units tall, origin at the feet.
// The first framing (lookAt y=1.4, dist 5.4) cropped the feet behind the HUD
// bar and put the head near the top edge. Aim at mid-torso and pull back.
const camera = new THREE.PerspectiveCamera(42, innerWidth / innerHeight, 0.1, 100);
const _sudsV = new THREE.Vector3();   // scratch for sudsAt(), see the stroke tick
// Three-quarter view. A dead-on camera flattens a blocky low-poly model and
// hides the very arm the splotches live on. Offsetting X turns the silhouette
// so the limb separates from the torso.
camera.position.set(2.6, 2.1, 6.6);
camera.lookAt(0, 1.75, 0);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
// What that setSize was last called with, so fit() -- which now runs on
// every frame of a camera move -- only redoes it when the window really
// changed. Declared here, beside the call that establishes it.
let _lastW = innerWidth, _lastH = innerHeight;
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
document.body.appendChild(renderer.domElement);

// Toon shading with ONE harsh light makes half the character solid black on a
// projector. Strong ambient/hemisphere is not optional.
scene.add(new THREE.HemisphereLight(0xffffff, 0x445566, 2.4));
const key = new THREE.DirectionalLight(0xffffff, 1.3);
key.position.set(2, 5, 3); scene.add(key);

// OutlineEffect ships in three/addons — no EffectComposer, no render targets.
// Thickness is SCREEN-space (the shader multiplies by pos.w), so it does NOT
// need retuning when you scale the model or move the camera. It renders the
// scene TWICE per frame (inverted hull); fine for 72 triangles.
const effect = new OutlineEffect(renderer, {
  defaultThickness: 0.006, defaultColor: [0, 0, 0], defaultKeepAlive: true });

// ---- THE ROOM ------------------------------------------------------------
// MEASURED at 1920x1080, the projector's real resolution: the character and
// the robot arm together covered 120,275 of 2,073,600 pixels. **94.2% of the
// projected frame was flat #0e1220 void**, and the subject was 38% of frame
// height. No test could see this; 490 green assertions had nothing to say
// about it. A judge ten feet back saw a small doll floating in a black
// rectangle.
//
// It was never a performance limit. The scene measured 9 draw calls and 777
// triangles at 72fps HEADLESS WITH NO GPU. The room below is affordable
// roughly a hundred times over.
//
// BUILT AT MODULE SCOPE, DELIBERATELY. The ramp and the robot arm used to be
// created inside the avatar's try block, so a GLB 404 (vendor.sh not run)
// took the whole scene with it. The room must survive that: if the character
// fails to load, the operator should still see a room with a robot in it and
// a readable error, not a void.
const ramp = new THREE.DataTexture(
  new Uint8Array([80, 160, 255]), 3, 1, THREE.RedFormat);
ramp.magFilter = ramp.minFilter = THREE.NearestFilter;
ramp.needsUpdate = true;

// FLOOR HEIGHT IS NOT ZERO. The avatar root sits at y=0.55 with scale 2.6, and
// its measured world bbox runs y 0.55 -> 2.30. The feet are at 0.55, so a
// floor at y=0 floats the character 0.55 units in the air -- which, on a flat
// toon render with no shadow, reads as "broken" rather than "hovering".
//
// I TRIED DROPPING THIS TO 0.10 to make room for the robot's pedestal and
// caught it with arithmetic before rendering: the avatar root stays at 0.55
// (solved to clear the ~19vh HUD band), so lowering the floor alone left the
// character FLOATING 0.45 units above it -- worse than the problem it fixed.
// The floor, the feet and the dirt cannot move independently; each is solved
// against something. So the floor stays, and the pedestal gets its mass a
// different way (see PLINTH below).
const FLOOR_Y = 0.55;

// ---- THE CHAIR ---------------------------------------------------------
// SOLVED FROM MEASURED WORLD BOUNDS, not eyeballed. My first pass guessed
// 1.35 by comparing the chair against the character's hip-to-head, which is
// the wrong reference: it produced a doll's chair buried inside the torso,
// visible in one screenshot.
//
// The numbers that decide it, read off the running page:
//   character, seated-posed, world height ....... 1.745   (0.320 -> 2.065)
//   character height above the floor ............ 1.515   (floor is 0.550)
//   chair GLB world height at scale 1.0 ......... 0.495
//   thigh bone sits above the root by ........... 0.458
//
// A real wheelchair's backrest reaches roughly mid-shoulder on its occupant,
// so target 0.62 of the seated height: 0.550 + 1.515*0.62 = 1.489, and
// (1.489 - 0.550) / 0.495 = 1.90.
//
// SEAT_FRAC is where the seat surface sits within the chair's own height --
// 0.42 on this model. The avatar's root then follows from it rather than
// being a second independent guess, so the two can never drift apart:
//   root Y = seat surface - thigh offset.
//
// CHAIR_YAW: a quarter turn. Seated legs fold along the body's own -Z, so
//   head-on they foreshorten into two pale discs and the pose reads as a
//   person standing in a bucket. Side-on, the fold is visible.
// SEAT_Z: the body sits back into the seat rather than on its front lip; the
//   chair's origin is its centre and the backrest is behind that.
const CHAIR_SCALE = 1.80;
const CHAIR_H     = 0.495;          // GLB world height at scale 1.0, measured
// SEAT_FRAC IS MEASURED OFF THE MESH, not assumed. Parsing the GLB's own
// vertex buffer gives a Y histogram with an EMPTY BAND at 0.309-0.371: that
// gap is the air above the seat and below the backrest top. The seat slab is
// the top of the lower cluster, 0.278, which is 0.56 of the chair's 0.495 --
// not the 0.42 a first pass guessed, which sank the body through the seat.
const SEAT_FRAC   = 0.56;           // seat surface, as a fraction of chair height
const THIGH_OFF   = 0.458;          // thigh bone above the avatar root, measured
// -0.6 rad, chosen by sweeping five yaws and reading the screenshots. Head
// on, the folded thighs foreshorten into two pale discs and the pose reads
// as someone standing in a bucket; a full quarter turn shows only her back.
// -0.6 is the three-quarter view: the face reads, the seated fold reads, the
// chair's wheel reads, and the scrubbed forearm still faces the arm.
const CHAIR_YAW   = -0.6;
const SEAT_Z      = -0.06;
// Where the measured body stands: screen-left of the chair, clear of the two
// left arms' plinths at x -1.55 and -1.20, and pulled forward so it is not
// hidden behind them.
const BODY_X = -2.45;
const BODY_Z =  0.55;

// Where the bowl waits and where it ends up. REST is ON THE SECOND ARM'S OWN
// MOUNTING PLATE -- there is no separate tray mesh, and this used to say
// "on the tray beside the second arm", which sent me looking for one. The
// plate is wide enough that the bowl reads as resting on a surface rather
// than floating; checked on a projector-sized frame at the moment feed opens.
// MOUTH is in front of the seated character's face, solved off the head
// bone's measured world position (1.481) rather than guessed.
const BOWL_REST  = { x: -1.05, y: 0.70, z: 0.35 };
const BOWL_MOUTH = { x: -0.30, y: 1.30, z: 0.42 };
const SEAT_SURFACE_Y = FLOOR_Y + CHAIR_H * CHAIR_SCALE * SEAT_FRAC;
const SEATED_ROOT_Y  = SEAT_SURFACE_Y - THIGH_OFF;

// A VERTICAL GRADIENT, NOT A FLAT FILL. The flat background made the top and
// bottom of the frame identical, so there was no horizon and nothing to
// suggest a space. Warm above, cool below, and the character's black outline
// separates from both.
{
  const c = document.createElement('canvas');
  c.width = 4; c.height = 256;
  const g = c.getContext('2d').createLinearGradient(0, 0, 0, 256);
  g.addColorStop(0.00, '#2b3566');   // warm-ish indigo overhead
  g.addColorStop(0.45, '#1a2142');
  g.addColorStop(1.00, '#0b0f1c');   // deep navy at the floor line
  const x = c.getContext('2d');
  x.fillStyle = g; x.fillRect(0, 0, 4, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;   // untagged canvas renders LINEAR
  scene.background = tex;
}

// THE ROBOT'S MOUNT. Narratively load-bearing, not decoration:
// OPEN-QUESTIONS.md §1 says the safe reading of the hackathon's bench-mount
// rule is "bench-mount the arm", so the screen should show the arm bolted to
// something rigid rather than floating.
//
// THIS WENT THROUGH TWO WRONG SHAPES BEFORE THE NUMBERS SETTLED IT, and both
// were caught by rendering rather than by reasoning:
//
// 1. A full-width table at the splotch height (top 1.26), so the forearm would
//    rest on it. Rendered, it was chest-high: the slab crossed the torso, cut
//    the character in half, and the legs showed underneath through the gap. A
//    person standing BEHIND a counter. At 5.2 units it also ran edge to edge,
//    so a brown slab owned the composition.
// 2. A shorter side table at a "nice" height of 0.98. That pushed the robot's
//    base up with it, and the sponge to ~1.75 against dirt at 1.33 -- waving
//    0.42 above the thing it is supposed to scrub.
//
// The constraint that decides it: the robot's base y=0.63 was SOLVED against
// the measured middle splotch (base 0.63 -> sponge 1.40 vs dirt 1.33, a
// verified 0.16 gap). Reaching the dirt is the money shot; what the plinth
// rests on is set dressing. So the mount comes DOWN to the solved base height.
//
// And at that height no table is geometrically possible: the feet sit at
// FLOOR_Y 0.55 and the base at 0.63, eight hundredths apart. My first attempt
// computed legH = (0.63 - 0.16) - 0.55 = -0.08 -- a NEGATIVE box dimension,
// which makes degenerate inverted geometry and buried the top under the floor.
// A low pedestal is what fits, and it is also what a real RoArm-M2-S clamps to.
// IT IS A PLINTH, NOT A TABLE -- forced by the numbers, not chosen for looks.
// The character's feet sit at FLOOR_Y 0.55 and the solved robot base is 0.63.
// Those are 0.08 apart, so NO table fits between them: my first attempt
// computed legH = (0.63 - 0.16) - 0.55 = -0.08, a NEGATIVE box dimension, which
// makes degenerate inverted geometry and buried the slab under the floor. A
// 2.6-wide top at that height also overlapped the body by 0.15 units, the exact
// thing moving it aside was meant to fix.
//
// A low pedestal is what fits, and a real RoArm-M2-S clamps to a small rigid
// base rather than to a dining table.
// BUILT DOWNWARD FROM ITS TOP, THROUGH THE FLOOR. Floor 0.55 and solved base
// 0.63 leave 0.08 of headroom, and a 0.08-tall box rendered as a brown
// floor-mat with a stick in it, not a machine bolted to a pedestal. A pedestal
// does not have to START at the floor, though: extend the box DOWN past the
// floor plane, which hides the overshoot and leaves a properly proportioned
// mount above it. Nothing solved moves -- the top face stays at 0.63.
const PLINTH_TOP = 0.63;
const PLINTH_H = 0.80;                      // most of it is below the floor
const PLINTH_X = 1.65;                      // directly under the robot's base
const PLINTH_Z = -0.37;
// Pulled out of the literal below so the other three arms' plinths are the
// same object rather than three near-copies that can drift apart.
const PLINTH_W = 0.86;
const PLINTH_D = 0.86;
// SLATE, THE ARMS' OWN BASE COLOUR (robotarm.js BASE = 0x5a6472), not the
// warm tan it was. A cold read of the projector frames put it plainly: four
// warm tan rectangles against a cool grey-violet floor were the
// highest-contrast objects in the lower third, so the eye went to them
// before it went to the chair -- and four identical detached mats scattered
// round a seated person reads as stuff placed on a floor, which is the
// opposite of one machine.
//
// Sharing the arms' colour makes each plinth read as the bottom of the arm
// standing on it rather than as a separate object under it. One hex value.
const PLINTH_COL = 0x5a6472;
// THE CHASSIS, A THIRD DARKER THAN THE PLINTHS. A second cold read caught
// the frame doing nothing: rails the same value as the bases AND the floor
// read as a shadow smear rather than structure, so the whole lower third
// went to mush. A darker value gives one continuous readable line from the
// left arms, under the person, to the right arms -- which is the only
// reason the frame exists.
const FRAME_COL  = 0x3d4550;
const plinth = new THREE.Mesh(
  new THREE.BoxGeometry(PLINTH_W, PLINTH_H, PLINTH_D),
  new THREE.MeshToonMaterial({ color: PLINTH_COL, gradientMap: ramp }));
plinth.position.set(PLINTH_X, PLINTH_TOP - PLINTH_H / 2, PLINTH_Z);
scene.add(plinth);

// A wider skirt at floor level so the pedestal sits INTO the room rather than
// punching through it, and the eye reads a base rather than a cut-off box.
// A SHADE DARKER THAN THE PLINTH, not a different colour. It was warm brown
// while the plinth above it was warm tan; recolouring only the plinth left
// one arm standing on a slate block with a brown skirt still around it, which
// looked like an oversight rather than a base. Same family, darker, so it
// reads as the plinth meeting the floor.
const riser = new THREE.Mesh(
  new THREE.BoxGeometry(1.22, 0.07, 1.22),
  new THREE.MeshToonMaterial({ color: 0x424a57, gradientMap: ramp }));
riser.position.set(PLINTH_X, FLOOR_Y + 0.035, PLINTH_Z);
scene.add(riser);

// FLOOR. One large plane in the same toon language. Kept darker than the
// bench so the bench reads as a separate object rather than a stripe.
// THE FLOOR MUST SEPARATE FROM THE BACKDROP, IN EITHER DIRECTION.
// Two wrong passes, both caught by rendering:
//   0x2a3350 -- so close to the gradient's lower stop that the horizon read as
//               a seam in a flat backdrop rather than a ground plane.
//   0x161c30 -- overcorrected DARKER, straight past the backdrop's #0b0f1c and
//               into invisibility: the floor vanished entirely and the plinth
//               hung in space with no ground under it.
// The answer is not darker or lighter but WARMER. A desaturated warm grey-brown
// reads as a floor against a cool blue backdrop at any brightness, and it picks
// up the same family as the plinth without matching it.
const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(60, 60),
  new THREE.MeshToonMaterial({ color: 0x4a4657, gradientMap: ramp }));
floor.rotation.x = -Math.PI / 2;
floor.position.y = FLOOR_Y;
scene.add(floor);

// ---- THE ROOM ----------------------------------------------------------
// The floor and the gradient gave the scene a ground and a sky, but nothing
// BEHIND the person: past the horizon line the world simply stopped, so the
// chair read as sitting on a plane in space rather than in a room. That is
// the "94% empty void" finding coming back in a smaller form -- the same
// problem the original overhaul fixed for the foreground only.
//
// Two flat planes, eleven draw calls' worth of nothing, and the scene has a
// corner. Deliberately DARKER than the floor and only slightly warmer than
// the backdrop: a bright wall would compete with the character, and the
// character is what the audience is meant to look at.
//
// No ceiling. A ceiling closes the frame from above and makes a 16:9
// projector feel cramped; a room with an open top reads as a bright care
// space rather than a basement.
const WALL_C = 0x343a58;

/** A tiled wall, drawn rather than downloaded.
 *
 *  A bathroom reads from its tile before it reads from anything else, and the
 *  presenter's opening line is about bathing over a room that was two flat
 *  planes. contactShadow() has built canvas textures here since the floor
 *  existed, so this needs no new asset, no vendor.sh line and no fourth
 *  asset path.
 *
 *  Deliberately LOW contrast: the grout is a shade of the wall rather than a
 *  drawn line, because the wall is meant to sit behind the character and a
 *  high-contrast grid would compete with the four arm colours and the point
 *  cloud. It should read as tile at ten feet and as texture at two.
 */
function tileTexture(hex, cell = 64, grout = 0.90) {
  const c = document.createElement('canvas');
  c.width = c.height = cell * 4;
  const x = c.getContext('2d');
  const r = (hex >> 16) & 255, g = (hex >> 8) & 255, b = hex & 255;
  x.fillStyle = `rgb(${r},${g},${b})`;
  x.fillRect(0, 0, c.width, c.height);
  // The grout: darker than the tile by a fixed ratio, so one number tunes
  // the whole look and the hue never drifts from the wall's.
  x.strokeStyle = `rgb(${r * grout | 0},${g * grout | 0},${b * grout | 0})`;
  x.lineWidth = 2;
  for (let i = 0; i <= 4; i++) {
    x.beginPath(); x.moveTo(i * cell, 0); x.lineTo(i * cell, c.height); x.stroke();
    x.beginPath(); x.moveTo(0, i * cell); x.lineTo(c.width, i * cell); x.stroke();
  }
  const t = new THREE.CanvasTexture(c);
  // SRGB, EXPLICITLY. An untagged canvas texture renders LINEAR in this three
  // version and comes out washed pale -- the ramp texture a hundred lines
  // above carries the same line for the same reason. Caught by reading that
  // one rather than by a screenshot.
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}
// Repeats sized so one tile is about 0.5 world units -- a 500mm wall tile,
// which is what a care bathroom actually uses. The plane is 60 x 18, so that
// is 30 x 9 tiles at 4 tiles per texture repeat.
const _wallTex = tileTexture(WALL_C);
_wallTex.repeat.set(30, 9);
const backWall = new THREE.Mesh(
  new THREE.PlaneGeometry(60, 18),
  new THREE.MeshToonMaterial({ color: 0xffffff, map: _wallTex, gradientMap: ramp }));
backWall.position.set(0, FLOOR_Y + 9, -7.5);
scene.add(backWall);

// The side wall sits far enough left that it never crowds the measured body
// at x -2.45, and it gives the light something to fall across so the frame
// has a direction.
// Its own texture rather than a shared one, because the side wall is a
// darker shade -- reusing the back wall's would flatten the corner the room
// was built to have.
const _sideTex = tileTexture(0x2d3350);
_sideTex.repeat.set(9, 9);          // 18 units at the same ~0.5-unit tile
const sideWall = new THREE.Mesh(
  new THREE.PlaneGeometry(18, 18),
  new THREE.MeshToonMaterial({ color: 0xffffff, map: _sideTex, gradientMap: ramp }));
sideWall.rotation.y = Math.PI / 2;
sideWall.position.set(-8.5, FLOOR_Y + 9, 0);
scene.add(sideWall);

// ---- THE LIGHT SHAFT -------------------------------------------------
// A beam falling on the chair from a ceiling fixture that is out of frame.
//
// Geometry, not light: a THREE.SpotLight would change the brightness of
// everything already balanced against the toon ramp -- the key, the rim and
// the hemisphere were each measured. A cone adds a shape and touches nothing.
//
// Additive so it brightens what is behind it rather than covering it, and
// depthWrite off so it never occludes the person, the arms or the point
// cloud. Rendered BEFORE the overlay (renderOrder 900 against territories'
// 999) so the scan still draws over the beam.
const shaftGeo = new THREE.CylinderGeometry(0.28, 2.6, 5.4, 28, 1, true);
const shaft = new THREE.Mesh(shaftGeo, new THREE.ShaderMaterial({
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
  side: THREE.DoubleSide,
  uniforms: { uCol: { value: new THREE.Color(0xbfe6ff) },
              uInt: { value: 0.16 } },
  vertexShader: `
    varying float vY;
    varying vec3 vN;
    void main() {
      // uv.y runs 0 at the bottom of the cylinder to 1 at the top.
      vY = uv.y;
      vN = normalize(normalMatrix * normal);
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }`,
  fragmentShader: `
    varying float vY;
    varying vec3 vN;
    uniform vec3 uCol;
    uniform float uInt;
    void main() {
      // FADE AT BOTH ENDS. A hard edge at the top reads as a cone someone
      // placed; a hard edge at the floor reads as a decal. Bright near the
      // fixture, gone before it reaches the ground.
      float a = smoothstep(0.0, 0.35, vY) * (1.0 - smoothstep(0.55, 1.0, vY));
      // AND AT THE SILHOUETTE. A beam is denser where you look through more
      // of it, so the rim should be softer than the middle -- without this
      // the cone's outline is a hard ellipse and reads as a solid object.
      a *= pow(1.0 - abs(dot(normalize(vN), vec3(0.0, 0.0, 1.0))), 0.8);
      gl_FragColor = vec4(uCol, a * uInt);
    }`,
}));
// Over the chair, which sits at the origin, tall enough that its top is out
// of frame at every shot and its bottom lands on the seated person.
shaft.position.set(0, FLOOR_Y + 3.4, 0);
shaft.renderOrder = 900;
// NO OUTLINE ON THE BEAM. This page renders through OutlineEffect, which
// draws every MESH a second time as a black inverted hull -- and a
// double-sided cone's hull fills the cone completely. The first screenshot
// was a solid black cone dominating the frame, with the additive pass
// invisible behind it.
//
// The point cloud escapes this only because OutlineEffect skips
// THREE.Points. A mesh has to opt out, which the effect supports through
// userData.outlineParameters.visible (OutlineEffect.js:301).
shaft.material.userData.outlineParameters = { visible: false };
scene.add(shaft);

// ---- STEAM ---------------------------------------------------------------
// A light shaft is invisible until something moves through it. The beam has
// been in the scene since the room was built and it reads as a flat cone,
// because nothing crosses it. This is what makes it pay.
//
// It is also the warmth. A shower with water spray and no steam reads as a
// machine rubbing a mannequin; steam reads as someone getting a warm wash,
// which is the dignity half of the pitch and the reason the room exists.
//
// THREE.Points, NOT sprites or meshes. OutlineEffect draws every mesh a
// second time as a black inverted hull (see the note above) and skips
// THREE.Points entirely -- so this needs no opt-out at all, where the beam
// needed one.
//
// NO NEW ASSET. The puff texture is the same canvas radial gradient
// contactShadow() builds, inverted to white. Downloading a smoke sprite for
// eight soft circles would be taking something this file already makes.
const STEAM_N = 90;
function steamTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 64;
  const g = c.getContext('2d').createRadialGradient(32, 32, 1, 32, 32, 31);
  // A LONG, SOFT FALLOFF. The first version went 0.55 -> 0.18 -> 0 and read
  // as a field of distinct white dots rather than steam: the alpha fell off
  // the edge too fast, so each puff had a visible boundary. Steam has no
  // boundary. Lower peak, and most of the radius spent fading.
  g.addColorStop(0,    'rgba(255,255,255,0.34)');
  g.addColorStop(0.25, 'rgba(255,255,255,0.20)');
  g.addColorStop(0.6,  'rgba(255,255,255,0.07)');
  g.addColorStop(1,    'rgba(255,255,255,0)');
  const cx = c.getContext('2d');
  cx.fillStyle = g; cx.fillRect(0, 0, 64, 64);
  const t = new THREE.CanvasTexture(c);
  // SRGB, like every other canvas texture on this page. An untagged canvas
  // is treated as LINEAR and renders washed and pale -- the tile wall
  // records the same trap.
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
const steamPos = new Float32Array(STEAM_N * 3);
const steamVel = new Float32Array(STEAM_N);
const steamAge = new Float32Array(STEAM_N);
function steamSeed(i, high) {
  const a = Math.random() * Math.PI * 2;
  const r = 0.25 + Math.random() * 0.85;
  steamPos[i * 3]     = Math.cos(a) * r;
  // Seeded across the whole column on the first fill so it does not start as
  // a single puff at the floor and climb as one visible front.
  steamPos[i * 3 + 1] = FLOOR_Y + (high ? Math.random() * 2.6 : 0.15);
  steamPos[i * 3 + 2] = Math.sin(a) * r * 0.7;
  steamVel[i] = 0.22 + Math.random() * 0.26;
  steamAge[i] = high ? Math.random() : 0;
}
for (let i = 0; i < STEAM_N; i++) steamSeed(i, true);
const steamGeo = new THREE.BufferGeometry();
steamGeo.setAttribute('position', new THREE.BufferAttribute(steamPos, 3));
const steam = new THREE.Points(steamGeo, new THREE.PointsMaterial({
  // BIGGER AND FEWER-READING. Larger puffs at lower alpha overlap into a
  // mass instead of resolving as individual sprites at projector size.
  map: steamTexture(), size: 1.15, transparent: true, depthWrite: false,
  // NOT additive. The beam above is already additive and stacking two of
  // them blows out to a white blob on a bright projector. Normal blending
  // keeps the puffs reading as puffs.
  blending: THREE.NormalBlending, opacity: 0, sizeAttenuation: true,
}));
// UNDER the beam's 900, so the shaft still reads as the brightest thing in
// that column rather than being fogged by what is drifting through it.
steam.renderOrder = 880;
scene.add(steam);

/** Drift the steam up and fade it by height. Opacity is driven by the
 *  caller, so it can thicken with the wash and clear on a mode change.
 */
function stepSteam(dt, want) {
  const m = steam.material;
  // Eased, so it thickens and clears rather than switching.
  m.opacity += (want - m.opacity) * Math.min(1, dt * 1.4);
  if (m.opacity < 0.004) { steam.visible = false; return; }
  steam.visible = true;
  const h = Math.min(dt || 0.016, 0.05);
  for (let i = 0; i < STEAM_N; i++) {
    steamPos[i * 3 + 1] += steamVel[i] * h;
    // A slow curl, so it does not read as a column of dots rising in lockstep.
    steamPos[i * 3]     += Math.sin(steamAge[i] * 2.1 + i) * 0.12 * h;
    steamPos[i * 3 + 2] += Math.cos(steamAge[i] * 1.7 + i) * 0.09 * h;
    steamAge[i] += h;
    if (steamPos[i * 3 + 1] > FLOOR_Y + 3.0) steamSeed(i, false);
  }
  steamGeo.attributes.position.needsUpdate = true;
}

// A SKIRTING LINE where the wall meets the floor. One thin box, and it is
// what actually sells the corner: without it the two planes meet in a seam
// the eye reads as a rendering artefact rather than as architecture.
const skirt = new THREE.Mesh(
  new THREE.BoxGeometry(60, 0.18, 0.08),
  new THREE.MeshToonMaterial({ color: 0x232842, gradientMap: ramp }));
skirt.position.set(0, FLOOR_Y + 0.09, -7.45);
scene.add(skirt);

// CONTACT SHADOWS, FAKED, AND DELIBERATELY SO. `renderer.shadowMap.enabled`
// is never set and no light in this scene has `castShadow`, so the real
// pipeline is off. A radial-gradient plane is unconditional, costs one draw
// call, needs no light config, and matches flat toon art better than a real
// penumbra would.
//
// IGNORE THE `castShadow = true` FLAGS. Six meshes set them (the chair, the
// bowl, the plinths, the props) and every one is dead code -- a mesh-side flag
// does nothing until both the renderer and a light opt in. They are harmless
// and left alone rather than stripped, because turning the real pipeline on
// later wants them. Do not read them as evidence that shadows are configured.
function contactShadow(x, z, r, opacity, y = FLOOR_Y + 0.185) {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d').createRadialGradient(64, 64, 2, 64, 64, 62);
  g.addColorStop(0,   'rgba(0,0,0,0.62)');
  g.addColorStop(0.5, 'rgba(0,0,0,0.28)');
  g.addColorStop(1,   'rgba(0,0,0,0)');
  const cx = c.getContext('2d');
  cx.fillStyle = g; cx.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  const m = new THREE.Mesh(
    new THREE.PlaneGeometry(r * 2, r * 2),
    // NOT a toon material: a shadow must not take the cel ramp or it bands
    // into a hard-edged grey disc. Basic + transparent keeps it soft.
    new THREE.MeshBasicMaterial({ map: tex, transparent: true,
                                  opacity, depthWrite: false }));
  m.rotation.x = -Math.PI / 2;
  // Lift fractionally off the surface or it z-fights with whatever it sits on.
  m.position.set(x, y, z);
  scene.add(m);
  return m;
}
contactShadow(0.00, 0.05, 0.95, 1.0);    // under the character, on the floor

// RIM LIGHT. A cool back-left key separates the silhouette from the backdrop.
// Toon materials band it into a clean edge highlight rather than a gradient,
// which is exactly the look the outline already establishes.
const rim = new THREE.DirectionalLight(0x8fd0ff, 1.15);
rim.position.set(-4, 3.2, -3);
scene.add(rim);

// THE REAL ARM'S PROPORTIONS, BEFORE THE FIRST ARM IS BUILT.
//
// makeRobotArm reads its link lengths once, at construction, so this has to
// land first or arm 0 is the old shape while arms 1-3 are the new one -- four
// machines that do not match, which is worse than four that are all slightly
// wrong. Awaited for that ordering only.
//
// SAFE TO AWAIT AT MODULE SCOPE. loadArmGeometry never rejects: a missing
// file, a 404, bad JSON or a nonsense number all resolve false and leave the
// page's own constants in place. The one thing it could cost is boot time, and
// it is one fetch of a 600-byte local file against index.html's 6s watchdog.
await loadArmGeometry();

// THE ROBOT ARM. Built here, beside the room, for the reason the room is here:
// it must survive the avatar failing to load. Its placement is solved against
// the character's splotches and so stays in the avatar block below.
const robot = makeRobotArm(scene, ramp, 'forearm-right');

// THE FLEET. `robot` stays the name for arm 0 so every existing call site --
// setPhase, strokeNow, the estop, the scrub choreography -- keeps working
// untouched. `fleet` is the list all four are driven through, filled in where
// the other three are placed. Empty until then, and every consumer tolerates
// that, because a GLB failure must not take the arms down with it.
const fleet = [];

// WHERE THE ARMS' DRAWN POSE CAME FROM: 'commanded', 'measured', or null for
// "nothing on the wire, the page is posing them itself".
//
// DECLARED HERE, BESIDE `fleet`, BECAUSE BOTH READERS ARE FAR BELOW IT. The
// websocket handler writes it and flag() reads it, and those sit ~200 lines
// apart near the bottom of this file; a `let` beside either one is in the
// temporal dead zone for any path that reaches the other first. This is the
// one place above both.
//
// null IS THE HONEST DEFAULT AND THE SAFE ONE. It is what the page has with
// no backend, and flag() prints no source tag at all for it -- so the string
// on a page with nothing behind it is unchanged from the day before this
// field existed.
let armJointsSrc = null;

// THE PERSON ON THE WIRE, as opposed to the person this Mac's own camera sees.
// `limbsSrc` is limb_event()'s own name for what it sent ("pose_2d_lifted"
// today, a depth-backed name once the RealSense is back on the GB10) and
// `limbsSeen` is how many of the six joints that payload actually carried.
// Both are null/0 when nobody is in frame on the other machine, because that
// field is deliberately not sticky: a held-over pose is a person standing
// where they are not. Read by the socket handler; see the comment there for
// why this records rather than poses.
let limbsSrc = null;
let limbsSeen = 0;

// The measured joints themselves, world millimetres, or null when this frame
// had none. Set only when EVERY reported joint was depth-measured; see the
// socket handler for why a partial set is not used.
let limbsWorld = null;

/** Measured world joints -> the landmark array avatar.update() poses from.
 *
 *  THE TWO FRAMES ARE NOT THE SAME AND THE DIFFERENCE IS NOT COSMETIC.
 *  py/vision.py publishes millimetres in scrub3d's world: +x the way the
 *  person faces, +y their left, +z up, origin on the floor under them.
 *  avatar.js wants MediaPipe's convention: metres, +y DOWN, hip-centred,
 *  and it negates y and z itself on the way in.
 *
 *  So this inverts vision.py's own _MP_TO_S3D:
 *      s3d.x =  mp.z      ->  mp.x =  s3d.y
 *      s3d.y =  mp.x      ->  mp.y = -s3d.z
 *      s3d.z = -mp.y      ->  mp.z =  s3d.x
 *  and subtracts the mid-shoulder so the result is centred the way
 *  MediaPipe's world landmarks are. Getting a sign wrong here is invisible
 *  in code and obvious on screen: the cartoon faces backwards, or leans the
 *  wrong way, with no error anywhere.
 *
 *  Returns null unless BOTH shoulders are present, because the centring
 *  needs them and a skeleton centred on one shoulder swings the whole body.
 */
function limbsToLandmarks(mm) {
  const need = ['l_shoulder', 'r_shoulder'];
  if (!mm || need.some(k => !Array.isArray(mm[k]))) return null;
  const cx = (mm.l_shoulder[0] + mm.r_shoulder[0]) / 2;
  const cy = (mm.l_shoulder[1] + mm.r_shoulder[1]) / 2;
  const cz = (mm.l_shoulder[2] + mm.r_shoulder[2]) / 2;
  // MediaPipe's own indices, the ones avatar.js's LM table names.
  const IDX = { l_shoulder: 11, r_shoulder: 12, l_elbow: 13, r_elbow: 14,
                l_wrist: 15, r_wrist: 16 };
  const out = [];
  for (const [name, i] of Object.entries(IDX)) {
    const p = mm[name];
    if (!Array.isArray(p)) continue;
    const sx = p[0] - cx, sy = p[1] - cy, sz = p[2] - cz;
    // mm -> metres, and into MediaPipe's axes.
    //
    // THE VERTICAL IS NEGATED ONCE, NOT TWICE. scrub3d's world has +z UP;
    // MediaPipe's world landmarks have +y DOWN, which is the convention
    // avatar.js's V() is written against -- it negates y itself to get
    // three.js's +y up. Negating here as well cancelled it: the drawn arms
    // pointed up and outward instead of hanging.
    //
    // Measured on the recording, world frame: the upper arm drops 203mm
    // from shoulder to elbow and the forearm a further 28mm, which is a
    // person sitting with their hands on their knees. On screen that came
    // out 93% horizontal and 10% downward -- arms raised out to the sides.
    // The sign check that missed it compared left against right, and a
    // flipped vertical is symmetric, so both arms were wrong the same way
    // and looked consistent.
    out[i] = { x: sy / 1000, y: -sz / 1000, z: sx / 1000 };
  }
  // HIPS, DERIVED FROM THE SHOULDERS RATHER THAN SENT.
  //
  // avatar.js reads them before it aims anything: its orientation assert
  // compares shoulder height against hip height, and V() on an undefined
  // landmark throws, which aborted the whole tracked-pose block every frame
  // and left the character in its idle clip while six measured joints sat
  // in the array unused.
  //
  // But the hips ON THE WIRE cannot be used for it. Depth never reaches a
  // seated person's hips -- the seat is in the way -- so they fall back to
  // vision.py's seated-adult constant of 1050mm while the shoulders are
  // MEASURED at about 270mm. In one skeleton that puts the hips three
  // quarters of a metre ABOVE the shoulders, and the assert fired exactly
  // as it was written to: "AVATAR UPSIDE DOWN: shoulderY=0.000 <=
  // hipY=2.340."
  //
  // So they are placed a torso below the shoulder line instead. Nothing
  // reads them for a position -- only for "which way is up" -- and a
  // derived point that answers that correctly beats a measured-looking one
  // that answers it backwards. 0.45 is a seated adult's shoulder-to-hip in
  // the metres-scale MediaPipe frame, from anatomy.ADULT's torso_len.
  const sh = out[IDX.l_shoulder] || out[IDX.r_shoulder];
  if (sh) {
    const hipY = sh.y + 0.45;            // +y is DOWN in this frame
    out[23] = { x:  0.09, y: hipY, z: sh.z };
    out[24] = { x: -0.09, y: hipY, z: sh.z };
  }

  // avatar.update() indexes up to LM.R_HIP (24) before it will pose
  // anything, so the array has to be that long even though the hips are not
  // published. The holes are undefined, and aim() already skips a joint it
  // cannot read -- the arms pose, the legs keep their idle.
  out.length = Math.max(out.length, 25);
  return out;
}

// HOW MUCH OF THE DRAWN BODY WAS ACTUALLY MEASURED.
//
// `bodyMeasuredDims` counts only the dimensions whose confidence cleared the
// threshold; `bodyTotalDims` is how many the model takes in all, so the
// readout can say "3 of 9" rather than "measured". `bodyFrames` is how much
// depth is behind them.
//
// ZERO IS THE HONEST DEFAULT AND THE SHIPPED ONE. With no backend these stay
// 0 and showBody() leaves the panel empty, so the page with nothing running
// is byte-identical to the one before this existed. They are never cleared
// once set, because the measurement is sticky on the wire for the reason its
// handler states: a body's proportions do not change when its owner leans
// out of frame.
let bodyMeasuredDims = 0;
let bodyTotalDims = 0;
let bodyFrames = 0;
// Did a live solve actually REBUILD the drawn body from those numbers? The
// two facts are separate and the panel must not merge them: the backend can
// be measuring a person perfectly while the body on screen is still the
// bake, which is exactly the state between the measurement settling and the
// solver finishing. Set from m.solve, which is the only thing that can say
// the drawn geometry changed.
let bodySolved = false;

// The measured-body overlay, or null if its bake is missing. Every consumer
// uses `territories?.` because a missing file must not cost the demo.
let territories = null;
let territoriesOn = false;
// The measured body's own pedestal. Shown only while the scan is, so an
// empty plinth off to one side is not read as a stray arm base.
let bodyPlinth = null;
let bodyShadow = null;   // and the shadow under it
let bodyIndex = 0;
// True while a body swap is mid-fetch. The handler awaits, so without
// this a second press re-enters it and leaks the cloud it abandons.
let swapBusy = false;
// THE FILE A FRESH SOLVE WROTE, or null when there has never been one.
//
// The partition on screen is normally a RECORDING -- tools/export_body.py bakes
// body.json offline and this page fetches it. When the backend runs the same
// solver live it writes body-live.json and says so on the socket; this holds
// that filename so 'b' shows the fresh partition instead of the baked one.
//
// NULL IS THE SHIPPED STATE AND MUST STAY INDISTINGUISHABLE FROM TODAY. With no
// backend, or a backend nobody asked to solve, this is null, bodyFile() returns
// exactly the names it always returned, and every path below is byte-identical
// to the demo that already works. The bake is the floor, not the fallback of
// last resort -- a failed solve simply never sets this.
let liveBody = null;

// WHICH BODY FILE THE OVERLAY SHOULD BE SHOWING. One definition, because the
// initial load, the 'n' swap and the live-solve refresh all have to agree: two
// of them computing the name independently is how the page ends up drawing one
// body while the counts panel describes another.
//
// A live solve replaces body A only. 'n' is the SECOND person -- the whole
// point of that key is proving the partition re-solves for a different body, so
// it must keep reaching a differently proportioned one. Live-solving body A and
// pressing 'n' therefore compares the real measured person against the baked
// alternate, which is the comparison the demo is making anyway.
function bodyFile() {
  if (bodyIndex) return 'body-b.json';
  return liveBody || 'body.json';
}

// POPS FROM THE CYCLE THAT WAS SCRUBBING THE PREVIOUS PERSON MUST NOT LAND ON
// THE NEW ONE. Swapping bodies resets the page's state, but Python does not
// know a swap happened: it is still mid-scrub and its next pop arrives about
// 200ms later, which rebuilt the count and repainted the new body. Measured on
// that path: a swap at 38% flashed 562 of the new body's cells at 67% with no
// sweep at all.
//
// The page cannot cancel the cycle -- the socket protocol has arm, estop and
// clear, and inventing a fourth message for an operator-error path is not
// worth a protocol change. Ignoring the tail of a cycle the operator abandoned
// is local and needs no server support.
//
// Cleared by `s`, so arming after a swap works normally.
let popsStale = false;
// THE COVERAGE SWEEP. Built when the scan loads; runs while the arms scrub so
// an audience can watch four agents work through one body.
let coverage = null;

// THE BODY FILLS IN ON THE SAME NUMBER THE COUNTER SHOWS. Not on the raw
// popped-splotch count and not on a clock of its own: the counter is a
// back.out(2.2) tween, so the raw count is already past what the number reads,
// and anything with its own clock drifts. Measured with all three loose: the
// HUD said 100% beside a body that was one third painted, and the tween's
// overshoot walked the number backwards 38, 34, 33 while the cells sat still.
//
// Registered ONCE, and it reads `coverage` through the live binding rather than
// capturing it, because a body swap (the `n` key) throws the old cloud away and
// builds a new one. A captured reference would keep painting the dead cloud.
// A DIP IS NOT A RESET. The counter's tween is back.out(2.2), which overshoots
// and settles, and each pop starts a fresh tween from wherever the last one
// was -- so the emitted fraction legitimately goes down mid-cycle (measured:
// 38, 36, 33 across three frames). Treating any low value as a reset wiped the
// body to dirty four times during one scrub.
//
// Only an exact zero is a reset, because resetCounter() is the one caller that
// emits it and it emits it directly rather than through a tween. Everything
// else only ever moves the painted head forward, which seek() already
// guarantees on its own.
onCleanPct((frac) => {
  if (!coverage || !territoriesOn) return;
  if (frac === 0) coverage.reset();
  else coverage.seek(frac);
});

// VOICE. Built on the browser's own Web Speech API -- no library, no key, no
// server. Starts only when the operator presses 'v', because a demo machine
// that opens the microphone on page load will be denied by the OS at the
// worst possible moment.
let voice = null;
// What went wrong with the recogniser, if anything, so the readout can
// say it instead of inviting a keypress that will fail the same way.
// Cleared when the operator turns the microphone on again.
let voiceFault = null;

// THE BOWL. Loaded once with the chair, hidden until feeding mode. Kept at
// module scope so setMode can reach it without threading it through.
let bowl = null;
// The pulse sensor on the chair's armrest and its reading light. The
// light pulses on the ECG's own cardiac phase, so the glow and the
// spike cannot drift apart.
let sensorPad = null;
let sensorLed = null;
let spoon = null;
let bowlTween = null;
let glass = null;
// Set by the voice intent for pills, read by the feed mode on the very next
// tick. A flag rather than a second mode, because to the machine it IS the
// same job -- lift something to the person's mouth -- and only the payload
// and the words differ.
let pillsRequested = false;
// The third thing the feed tile promises. Separate from pillsRequested
// because a drink is not medication: same glass, no pill count, and
// the readout says DRINKING rather than MEDICATION.
let drinkRequested = false;

// The heartbeat trace. Created on first use so its canvas and animation frame
// cost nothing until someone actually opens vitals.
let ecg = null;

/** Tick every arm's motion, not just arm 0.
 *
 *  THE OTHER THREE WERE NEVER UPDATED. The render loop called
 *  `robot?.update(dt)` -- the single arm that existed when it was written --
 *  so arms 1 to 3 had their target poses set by setPhase and then never
 *  stepped toward them. They were holding whatever pose they loaded with.
 */
// ---- CARE DELIVERED --------------------------------------------------
// The only number on this screen that is about a person. Everything else
// measures the machine.
//
// It counts REAL WORKING SECONDS, not wall clock: it advances while a Python
// scrub cycle is live or a mode beat is running, and stops when they stop. A
// clock that ran regardless would be a stopwatch pretending to be a
// measurement, which is the thing this project keeps removing.
//
// The two zeros are the claim. BRAINSTORM-2 line 72: nursing assistants are
// injured at five times the industry rate, mostly back and shoulder, from
// handling people. The presenter says that at 0:15 and the screen has never
// supported it. Nobody lifted this person and nobody risked their back,
// because the machine did it -- and those stay zero for the whole demo, which
// is exactly the point.
let careSeconds = 0;
let careEl = null;

/** Show that frames are arriving and that none of them are kept.
 *
 *  The privacy line has always read "ON-DEVICE ONLY - 0 FRAMES STORED" as
 *  fixed text, and the presenter asserts it at 0:28 -- the single strongest
 *  defensive claim in the pitch, made by a caption that would say the same
 *  thing with the camera unplugged.
 *
 *  A number that climbs beside a zero that does not is the same claim with
 *  evidence attached, and it costs nothing to be honest about: framesSeen is
 *  incremented in the one branch that consumes a real camera frame, and the
 *  stored count is a literal zero because nothing in this page writes a frame
 *  anywhere. If a future change ever does store one, this line has to start
 *  lying deliberately rather than by omission.
 *
 *  ONLY WHEN IT CHANGES. This runs every frame; rewriting identical text into
 *  the DOM 60 times a second is work for nothing, and on the seen/stored pair
 *  the second half never changes at all.
 *
 *  IT IS A FLOOR, AND THAT IS THE RIGHT DIRECTION. Two cases undercount: a
 *  backgrounded tab stops the render loop while the camera keeps producing,
 *  and a camera faster than the loop advances currentTime by more than one
 *  frame-time while this adds one. Neither can overcount, because the branch
 *  is synchronous and reassigns lastVideoTime in the same tick. So the number
 *  is frames the detector actually consumed, never frames it might have --
 *  which is the narrower claim and the one the line should be making.
 */
function stepPrivacy() {
  if (framesSeen === framesShown && subjectSeen === subjectShown) return;
  framesShown = framesSeen;
  subjectShown = subjectSeen;
  const el = document.getElementById('privacy');
  if (!el) return;
  // ONLY ONCE FRAMES ARE ARRIVING. With no camera at all the count sits at 0
  // and "no subject" would be true but useless -- it would read as a fault
  // when the real state is that there is no camera. Below the first frame the
  // line says nothing about subjects.
  const sub = framesSeen === 0 ? ''
    : subjectSeen
      ? ' &middot; <span class="lock">SUBJECT LOCKED</span>'
      : ' &middot; <span class="hunt">LOOKING FOR A SUBJECT</span>';
  el.innerHTML = 'ON-DEVICE ONLY &middot; '
    + `<span class="seen">${framesSeen}</span> SEEN &middot; `
    + '<span class="kept">0</span> STORED'
    + sub;
}

function stepCare(dt) {
  if (careEl === null) careEl = document.getElementById('care') || false;
  if (!careEl) return;
  // WORKING, NOT MERELY ON. A live scrub cycle counts, and so do feeding and
  // vitals -- the arms are moving or the chair is reading someone. Voice does
  // NOT: the machine listening to a question is not care delivered, and
  // counting it would inflate the one number here that has to be honest.
  //
  // Shower with no cycle running is the resting state, so it does not count
  // either; the cycle flag is what makes it real.
  // WORKING, NOT MERELY SELECTED. feedLive is false once the last
  // spoonful lands, because `mode` stays 'feed' until the operator
  // moves on and the arms are parked at rest for all of it. Vitals is
  // different on purpose: it keeps reading a heart rate for as long as
  // it is up, so the whole time genuinely is care delivered.
  const working = cycleLive || feedLive || mode === 'vitals';
  if (!working) return;
  careSeconds += Math.min(dt || 0, 0.05);      // clamp a tab-switch spike
  const s = Math.floor(careSeconds);
  careEl.innerHTML =
      `<div><span class="n">${s}s</span> <span class="k">OF CARE</span></div>`
    + `<div><span class="z">0</span> <span class="k">LIFTS BY A PERSON</span></div>`
    + `<div><span class="z">0</span> <span class="k">BACKS AT RISK</span></div>`;
  careEl.classList.add('on');
}

function stepArms(dt) {
  stepPrivacy();
  stepCare(dt);
  stepSensor();
  // STEAM ONLY DURING THE WASH, and thicker as it goes. Not in feed, vitals
  // or voice: the arms have withdrawn by then and the script wants those
  // beats calm. It clears itself on the mode change because `want` drops to
  // zero and stepSteam eases down rather than cutting.
  //
  // THE BACKUP VIDEO HAS NO STEAM, AND THAT IS CORRECT. tools/record_backup.py
  // deliberately never arms a cycle -- its comment explains why -- because the
  // clip exists for the total-failure case where Python is dead and the
  // operator is popping splotches by hand with 1/2/3. No cycle is running in
  // that story, so no steam is the honest picture. Do not loosen this gate to
  // make the fallback video prettier.
  //
  // Tied to the same fraction the counter and the foam use, so the room
  // warming up and the person getting clean are one number, not two.
  const washing = mode === 'shower' && (cycleLive || choreoInterval !== null);
  const done = recs.length ? cleaned / recs.length : 0;
  stepSteam(dt, washing ? 0.30 + done * 0.45 : 0);
  if (fleet.length) { fleet.forEach(({ arm }) => arm.update(dt)); return; }
  robot?.update(dt);        // before the fleet is built
}

/** THE SENSOR LIGHT BEATS WITH THE HEART, not on a timer of its own.
 *
 *  It reads the ECG's cardiac phase -- the same 0-to-1 the trace is drawn
 *  from -- so the glow on the chair and the spike in the HUD are the same
 *  event. Two things claiming to show one heartbeat and running on separate
 *  clocks is the bug class this file has spent the day removing.
 *
 *  Dim and steady when vitals is not the mode: the sensor is still there, it
 *  is just not what the audience is being asked to look at.
 */
function stepSensor() {
  // THE SENSOR KEEPS READING THROUGH AN ESTOP, DELIBERATELY. `x` clears the
  // mode timers and puts the props down but does not leave vitals, so this
  // keeps pulsing. That is right: the sensor is not an actuator, and a person
  // whose heart is beating still has a pulse after an emergency stop. A light
  // that went out would be claiming the sensor failed -- the arms stop, the
  // monitoring does not, which is the whole point of monitoring.
  if (!sensorLed) return;
  const live = (mode === 'vitals') && ecg;
  // The R spike lands early in the cycle, so the flash is front-loaded: a
  // fast rise and a slower fall reads as a pulse rather than a blink.
  const ph = live ? ecg.phase : 0;
  const beat = live ? Math.max(0, 1 - Math.min(1, ph / 0.22)) ** 1.6 : 0;
  const k = 0.22 + 0.78 * beat;
  sensorLed.material.color.setRGB(0.91 * k, 0.08 * k, 0.24 * k);
  // A touch of scale with it. Colour alone reads as a texture change at ten
  // feet; a shape that moves reads as a light.
  const sc = 1 + beat * 0.35;
  sensorLed.scale.set(sc, 1, sc);
}

/** Drive EVERY arm, not just arm 0.
 *
 *  The eight existing `robot?.setPhase(...)` sites were written when there was
 *  one arm. Rewriting each to loop would put the fleet's shape in eight
 *  places; these two put it in one, and the call sites change by a single
 *  word.
 *
 *  THE STAGGER IS THE POINT. Four arms hitting the same pose on the same
 *  frame reads as one four-headed machine -- mechanical, and slightly
 *  menacing over a person. Offsetting each by 110ms makes them read as four
 *  agents that each decided to move, which is what the brainstorm's
 *  "independent AI agent" claim looks like on screen. 90ms sits inside the
 *  50-100ms band ECC's motion-patterns skill gives for stagger: below 50 reads
 *  as mechanical, above 100 as sluggish.
 */
function fleetPhase(phase, stagger = 90) {
  fleet.forEach(({ arm }, i) => {
    if (stagger <= 0) { arm.setPhase(phase); return; }
    setTimeout(() => arm.setPhase(phase), i * stagger);
  });
  // The governor only has something to say while the arms are moving -- and
  // only where its lines are TRUE. 'hover' has two callers: the scrub's
  // approach, where the arms really are travelling in and a reach can be
  // cleared or held, and voice mode, where all four lift slightly and wait.
  // In voice mode the panel was printing "HOLD waiting for arm 2 to leave the
  // shared zone" at four parked arms, which is a false statement on screen
  // during the beat a judge is most likely to read it.
  //
  // Voice mode sets the phase and then shows its own readout, so gating here
  // on the mode rather than adding a third phase name keeps the fix to the
  // display that was lying.
  // typeof, not a bare read: `let mode` is declared below this function
  // and the file already records that a temporal-dead-zone throw here
  // kills the whole keyboard handler rather than one line. No call path
  // reaches this before the declaration today; this makes that not matter.
  const inVoice = typeof mode !== 'undefined' && mode === 'voice';
  showGovernor((phase === 'scrub' || phase === 'hover') && !inVoice);
  // THE BODY FILLS IN ON THE COUNTER, NOT ON A CLOCK. This used to call
  // coverage.start(), a free-running sweep paced for 13 seconds. A real
  // CAM=fake cycle finishes in 10, so it never got past 127 of 699 cells and
  // the end-of-cycle reset wiped those: the HUD read 100% beside a body that
  // was 18% clean. The sweep is now seeked from the same fraction the counter
  // uses (see setClean's call site), so the two cannot disagree.
  //
  // stop() still belongs here. It cancels any frame loop a previous version
  // left running and costs nothing when there is none, so a page that has
  // been through a reload does not carry a stray sweep into the next cycle.
  if (coverage && phase === 'rest') coverage.stop();
  // LIGHT THE TERRITORIES WHILE THEY WORK. The scan beside the person is
  // static data until it reacts, and a static diagram reads as a slide. When
  // all four scrub, every region lights; when they park, it dims back.
  //
  // This is the one place the two halves of the merge actually touch on
  // screen: the frontend's choreography driving the backend's partition.
  if (territories && territoriesOn) {
    // Brighter while working, dimmer at rest. Not focus() here -- every arm
    // is moving, so singling one out would be a lie about what is happening.
    territories.focus(null);
    territories.points.material.uniforms.uFade.value =
      phase === 'scrub' ? 1.0 : phase === 'hover' ? 0.8 : 0.55;
  }
}

/** One stroke on every arm, with alternating direction per arm so they do not
 *  scrub in lockstep. */
function fleetStroke(dir) {
  fleet.forEach(({ arm }, i) => arm.strokeNow(i % 2 === 0 ? dir : -dir));
}

// ---- THE FOUR CAPABILITIES ---------------------------------------------
// The brainstorm's product is a wheelchair that showers, feeds (including
// pills), senses vitals and takes voice commands. The demo did the first one,
// so a judge watching had no way to know the other three existed.
//
// Each mode changes what the arms do and what the bottom readout measures.
// That is deliberately the whole mechanism: a mode nobody can SEE is a claim,
// and this project's rule is that a claim needs a picture.
//
// modeTimers is separate from the scrub choreography's own timers so
// switching modes mid-cycle cancels only the mode's own beats.
let mode = 'shower';
let modeTimers = [];
// The single pending id of whichever self-rearming readout loop is
// running. Separate from modeTimers because only the latest one can
// be pending, so there is nothing to accumulate.
let modeTick = null;
// True only while the feeding beat is actually delivering. `mode`
// stays 'feed' long after the last spoonful lands, so the mode name
// alone cannot tell care being delivered from a mode left up.
let feedLive = false;
const MODES = {
  shower: { label: 'CLEANLINESS', unit: '%',
            // the existing cycle; arms work the body
            enter: () => { fleetPhase('rest'); } },
  feed:   { label: 'FEEDING',     unit: '%',
            // ONE arm lifts to mouth height and holds. Four arms converging
            // on someone's face reads as an attack; one arm offering food
            // reads as care, which is the difference the pitch depends on.
            //
            // AND IT CARRIES SOMETHING. An empty arm waving near a face is
            // not legible as feeding; a bowl travelling from the tray to the
            // mouth is legible instantly, with no narration. The bowl is
            // Kenney's Food Kit (CC0), the same artist as the character and
            // the chair, so nothing here is modelled.
            enter: () => {
              fleetPhase('rest', 0);
              // WHICH PROP depends on what was asked for. `pills` is set by
              // the voice intent just before the mode switch, so saying "I
              // need my pills" brings the glass and saying "I'm hungry"
              // brings the bowl. Without this the voice claimed one thing
              // while the screen showed another.
              // THE ARM CHANGES TOOLS. The brainstorm asks for it by name
              // ("Magnet to change tools?") and without it the feeding arm
              // carries soup to a mouth while still wearing the sponge it
              // scrubs with -- one machine pretending to be five.
              //
              // Hidden, not removed: robotarm.js exposes the sponge and the
              // tests read its WORLD POSITION to check it meets the dirt, so
              // the mesh has to stay exactly where it is.
              const feeder = fleet[1] && fleet[1].arm;
              // AN EXCHANGE, NOT A TELEPORT. Both props hang off the same
              // attach point, so flipping .visible on each swapped them in a
              // single frame -- the presenter says "it puts the sponge down
              // and picks up a spoon" while the screen shows one object
              // becoming another. Shrinking one away and growing the other in
              // over a quarter second costs no geometry and makes the spoken
              // line a shown one.
              //
              // SCALE IS A MULTIPLIER OFF EACH PROP'S OWN AUTHORED VALUE,
              // never a fixed number: the spoon's 0.55 and the sponge's 1.0
              // were each measured against the character and a tween that
              // ended on a literal would silently resize them.
              if (feeder && feeder.sponge) swapOut(feeder.sponge);
              // THE SPOON IS FOR SOUP, NOT FOR PILLS. Showing it
              // unconditionally put a spoon AND a glass of water in the same
              // hand on the medication beat. The point of a tool change is
              // that the tool matches the job.
              // No spoon for a drink either -- you do not spoon a glass.
              if (spoon) {
                if (pillsRequested || drinkRequested) swapOut(spoon);
                else swapIn(spoon);
              }
              // HIDE THE ONE WE ARE NOT USING. enter() only ever showed the
              // chosen prop, and setMode skips leave() when the mode has not
              // changed -- which is exactly what `shift+8` from inside feed
              // does. So the soup bowl stayed parked at the mouth while the
              // glass tweened into the same spot: two props interpenetrating
              // at the volunteer's face, under the word MEDICATION, at the
              // closest camera angle in the demo.
              const useGlass = pillsRequested || drinkRequested;
              const prop  = useGlass ? glass : bowl;
              const other = useGlass ? bowl : glass;
              if (other) other.visible = false;
              if (prop) prop.visible = true;
              const label = document.getElementById('label');
              if (label) label.textContent = pillsRequested ? 'MEDICATION'
                                          : drinkRequested ? 'DRINKING' : 'FEEDING';
              // THE READOUT MUST MOVE. Feeding and medication left the
              // counter frozen at 0% under their own label, which reads as a
              // broken meter rather than as a beat that does not measure
              // anything. Every other mode on this screen moves.
              //
              // It counts SPOONFULS, not a percentage of nothing: a meal is
              // eaten in mouthfuls and a dose is a number of pills, so the
              // number is a real thing a carer would track.
              borrowPct(true);
              // FOUR, NOT SIX. The script gives ten seconds to feed, pills
              // and vitals together; six spoonfuls at 1.9s is 11.4s and eats
              // the whole beat on its own, so the presenter runs out of line
              // before the machine runs out of soup. Measured against
              // DEMO-SCRIPT-V2's 1:42-1:52.
              // THREE THINGS THE TILE PROMISES: eating, drinking, pills.
              // `drinkRequested` is the one that was missing -- saying "I'm
              // thirsty" routed to the food branch and brought a bowl of soup
              // while the chair said "Bringing your food".
              //
              // Sips are fewer and slower than spoonfuls, which is how a
              // person actually drinks and also keeps the beat inside its ten
              // seconds.
              const total = pillsRequested ? 2 : drinkRequested ? 3 : 4;
              const word  = pillsRequested ? 'PILLS'
                          : drinkRequested ? 'SIPS' : 'SPOONS';
              let given = 0;
              const pct = document.getElementById('pct');
              const fill = document.getElementById('fill');
              // CLAIM THE READOUT NOW, NOT IN 400ms. serve() is deferred until
              // the arm has travelled to mouth height, and setMode has already
              // painted the CLEANLINESS percentage into this element on its way
              // in -- so for those 400ms the projector read "FEEDING" over
              // "100%", which parses as "fed 100%". Writing the zero up front
              // costs nothing and there is never a frame where the label and
              // the number describe different things.
              if (pct) pct.textContent = `0 / ${total} ${word}`;
              if (fill) fill.style.width = '0%';
              feedLive = true;
              const serve = () => {
                if (mode !== 'feed' || !pct) return;
                pct.textContent = `${given} / ${total} ${word}`;
                if (fill) fill.style.width = `${(given / total) * 100}%`;
                if (given >= total) {
                  // Land on the last one and stop, rather than looping back
                  // to zero while a judge is watching the number.
                  fleet[1]?.arm.setPhase('rest');
                  // THE BEAT IS OVER, SO THE CARE COUNT STOPS. `mode` stays
                  // 'feed' until the operator presses another mode key, and
                  // the script has the presenter talking over the transition
                  // for about 25 seconds with the arms parked at rest. Gating
                  // the count on the mode NAME counted all of that as care
                  // delivered while nothing on screen was moving -- the
                  // stopwatch-pretending-to-be-a-measurement this panel
                  // exists to avoid. See stepCare.
                  feedLive = false;
                  return;
                }
                given += 1;
                // ONE SOUND PER MOUTHFUL. The demo script tells the presenter
                // to stop talking and let the sound carry the beat, and
                // feeding was silent -- three of the five beats on this
                // screen made no noise at all, so the advice only worked for
                // the scrub. A soft tick per spoonful gives the count a
                // rhythm an audience can follow without watching the number.
                play('click');
                liftBowl(prop);
                modeTimers.push(setTimeout(serve,
                  pillsRequested ? 2200 : drinkRequested ? 1900 : 1600));
              };

              modeTimers.push(setTimeout(() => {
                fleet[1]?.arm.setPhase('hover');
                // ONE arm is working, so light ONLY its territory. This is
                // what focus() exists for: the audience can see which part of
                // the body that arm is responsible for while it feeds.
                if (territories && territoriesOn) territories.focus(1);
                serve();
              }, 400));
            },
            leave: () => {
              if (bowl) bowl.visible = false;
              if (glass) glass.visible = false;
              // Switching away MID-BEAT must stop the care count too, or the
              // flag stays true for the rest of the demo and every later
              // second counts as feeding. serve() only clears it when the
              // last spoonful lands, which is the other way out.
              feedLive = false;
              // THE TOOL GOES BACK. Leaving the spoon on and the sponge off
              // would break the next scrub cycle's picture, and leave() is the
              // only place that knows this mode is over.
              const fed = fleet[1] && fleet[1].arm;
              // THROUGH swapIn, NOT .visible. swapOut leaves the mesh at
              // scale 0.001, so setting visible alone would put an
              // invisible speck back on the arm and the next scrub cycle
              // would run with no sponge on screen.
              if (fed && fed.sponge) swapIn(fed.sponge);
              // And the spoon goes away the same way it arrived, so the
              // exchange reads in both directions.
              if (spoon) swapOut(spoon);
              pillsRequested = false;
              drinkRequested = false;
              borrowPct(false);
              // UNDO THE FOCUS THIS MODE APPLIED. enter() calls focus(1) so the
              // audience can see whose territory the feeding arm owns; nothing
              // here put it back. Every other exit happens to recover because
              // the next mode's enter calls fleetPhase, which calls
              // focus(null) -- an accident, not a guarantee, and the one mode
              // that dims three quarters of the body should be the one that
              // undims it.
              if (territories && territoriesOn) territories.focus(null);
              // Hand the bar back to the cleanliness counter at whatever it
              // actually holds, or the next scrub starts from a feeding bar.
              const fill = document.getElementById('fill');
              if (fill && typeof recs !== 'undefined' && typeof cleaned !== 'undefined') {
                fill.style.width = `${recs.length ? (cleaned / recs.length) * 100 : 0}%`;
              }
            } },
  vitals: { label: 'HEART RATE',  unit: ' BPM',
            // Arms withdraw entirely. Vitals come from sensors in the chair,
            // per the hardware list (MAX30102 pulse oximetry), so the honest
            // picture is a machine that has stopped touching the person.
            //
            // THE NUMBER IS SIMULATED AND THE SENSOR DOES NOT EXIST YET. It
            // is on the request list, not in the box. Driving the readout from
            // a fake feed now means the visual is real and the source swaps to
            // the real sensor with one function, which is the whole point of
            // building the panel before the hardware lands.
            enter: () => {
              fleetPhase('rest');
              borrowPct(true);        // stop setClean repainting a percentage
              if (!ecg) ecg = makeVitals({ mount: 'ecg' });
              const host = document.getElementById('ecg');
              if (host) host.classList.add('on');
              ecg?.start();
              const pct = document.getElementById('pct');
              const beat = () => {
                if (mode !== 'vitals' || !pct) return;
                // A resting adult, wandering a little so it never looks frozen.
                const bpm = 72 + Math.round(Math.sin(Date.now() / 2600) * 5);
                pct.textContent = `${bpm} BPM`;
                // SPO2 COMES FROM THE SAME SENSOR, and the mode strip has
                // promised it since the strip was built. A MAX30102 measures
                // pulse oximetry and heart rate from one part, so showing
                // both is the honest reading rather than a second claim.
                //
                // On the LABEL line, not in the big readout: #pct is 97px on
                // a projector and already carries the BPM. A second number
                // there competes with the headline instead of supporting it.
                //
                // Healthy resting range, drifting on its own slower cycle so
                // it never looks pinned to the pulse.
                const spo2 = 97 + Math.round(Math.sin(Date.now() / 7100) * 1.4);
                const lab = document.getElementById('label');
                if (lab) lab.textContent = `HEART RATE  ·  SPO2 ${spo2}%`;
                // The trace and the number come from the SAME value, so they
                // can never disagree on screen.
                ecg?.setBpm(bpm);
                // A tick on the beat. A monitor that shows a pulse and makes
                // no sound reads as a screensaver; the sound is most of what
                // makes it read as a live reading.
                play('click');
                // ONE ID, NOT A GROWING LIST. This re-arms itself, so only
                // the latest timer can be pending -- pushing each one made
                // modeTimers grow for as long as the mode was held.
                modeTick = setTimeout(beat, 900);
              };
              beat();
            },
            leave: () => {
              ecg?.stop();
              document.getElementById('ecg')?.classList.remove('on');
              // GIVE THE READOUT BACK HERE, not only in setMode. feed and
              // voice both release it in their own leave(); vitals relied on
              // setMode's `if (next !== 'vitals')` branch, so the pair was
              // asymmetric and any future caller of leave() outside setMode
              // would strand the flag -- which freezes the counter, the bar
              // and the body overlay together, since all three now read the
              // same value.
              borrowPct(false);
            } },
  voice:  { label: 'LISTENING',   unit: '',
            // All four lift slightly and wait. A machine that is listening
            // should look attentive rather than parked.
            //
            // THE READOUT WAS INHERITING A STALE NUMBER. Voice was the only
            // mode that set no value, so switching to it from a finished
            // scrub left "100%" sitting under the word LISTENING. It now says
            // what it is doing, and whether the microphone is actually open,
            // which is the one fact an operator needs when a room is noisy.
            enter: () => {
              fleetPhase('hover');
              borrowPct(true);
              const pct = document.getElementById('pct');
              const fill = document.getElementById('fill');
              const tick = () => {
                if (mode !== 'voice' || !pct) return;
                const on = voice && voice.listening;
                // 'M', NOT 'V'. Voice was originally bound to `v`, which has
                // cycled the 12 characters since long before voice existed;
                // the binding moved to `m` and the docs moved with it, but
                // this string did not. The projector was telling the operator
                // to press the key that swaps the person on screen -- caught
                // by a screenshot, not by any assertion, because nothing
                // checks what the readout SAYS against what the keys DO.
                // NAME THE FAULT instead of inviting a keypress that will
                // fail the same way. After a network error the recogniser
                // stops, so `on` goes false and this used to read PRESS M --
                // which sends the operator to press the key that just failed,
                // on the beat where they have least time to work it out.
                pct.textContent = on ? 'SAY SOMETHING'
                                     : (voiceFault || 'PRESS M');
                // The bar becomes a breathing level meter rather than a
                // progress bar, because there is no progress to show -- it is
                // the visual difference between waiting and working.
                if (fill) {
                  const b = on ? 30 + Math.sin(Date.now() / 420) * 22 : 0;
                  fill.style.width = `${b}%`;
                }
                // Same as the vitals beat: self-rearming, so one id.
                // At 90ms this was the worst of the two -- the script ENDS in
                // voice mode, so a page parked between runs collected about
                // 666 dead ids a minute.
                modeTick = setTimeout(tick, 90);
              };
              tick();
            },
            leave: () => {
              borrowPct(false);
              // THE WORDS GO WITH THE MODE. Nothing cleared #heard, so the
              // last phrase the recogniser settled on stayed on screen for
              // the rest of the demo -- under a heart rate, under a
              // cleanliness percentage, claiming the machine is still
              // hearing something it heard a minute ago. It also sat inside
              // the heartbeat trace, which is taller now.
              const heard = document.getElementById('heard');
              if (heard) { heard.textContent = ''; heard.classList.remove('final'); }
              const fill = document.getElementById('fill');
              if (fill && typeof recs !== 'undefined' && typeof cleaned !== 'undefined') {
                fill.style.width = `${recs.length ? (cleaned / recs.length) * 100 : 0}%`;
              }
            } },
};

// ---- THE SAFETY GOVERNOR, ON SCREEN -------------------------------------
// scrub3d routes EVERY arm command through a governor that can clear, hold,
// retreat, refuse or estop it. That is the answer to the question a judge
// always asks about a machine that touches people, and it was invisible --
// the pitch could only assert it.
//
// THESE ARE THE REAL VERDICT NAMES from fleet.py, not invented labels, so
// what an audience reads matches what the backend actually decides. When the
// live governor is wired to the socket this display does not change; only its
// source does.
//
// WHY IT IS DRIVEN BY THE CYCLE RATHER THAN THE SOCKET TODAY. The socket
// carries events only, by the rule that keeps the cartoon alive when Python
// dies. Adding a verdict field is a backend change on another branch; until
// then the page shows the verdicts the cycle it is running would produce,
// which are true statements about this cycle.
const GOV_LINES = [
  { k: 'clear',  t: 'CLEAR   reach + torque within limits' },
  { k: 'clear',  t: 'CLEAR   no arm-to-arm intersection' },
  // "CONTESTED", not "the shared zone". The counts panel directly below
  // reports CONTESTED 433 with the solver's own class name, and a judge
  // reading both would otherwise have to work out that two different words
  // meant one thing. The number in that row is this line's evidence.
  { k: 'hold',   t: 'HOLD    arm 2 is in a contested cell' },
  { k: 'refuse', t: 'REFUSE  target outside the workspace box' },
];

/** Every arm's share, from the bake's own stats.
 *
 *  These are the numbers `partition.solve` produced, not a summary written by
 *  hand: swapping the body re-runs the solver and the rows move with it.
 *
 *  "EVERY ARM", NOT "THE FOUR ARMS". The rows have always come from the bake's
 *  per-arm stats and have never been four of anything -- the count follows the
 *  rig file, which is now the measured two. Only the sentence was stale.
 */
/** Rebuild the measured-body overlay from whatever bodyFile() now names.
 *
 * ONE SWAP IMPLEMENTATION, called by the 'n' key and by a live solve landing
 * over the socket. It was the 'n' handler's inline body until the solve needed
 * the same thing; two copies of this would have been two copies of every bug
 * listed below, and the socket path would have been the copy that never got
 * the fixes.
 */
async function swapBody() {
  // ONE SWAP AT A TIME. This function awaits a fetch, and `territories` still
  // holds the OLD object across that await -- so a second entry disposed the
  // same object twice and started a second fetch. The loser's cloud was
  // already added to the scene and never removed, leaking a geometry and a
  // shader per press.
  //
  // And because the second call read `wasOn` after the first had zeroed it,
  // the restore never ran: the measured body and the arm-share list vanished
  // on the beat whose whole job is showing them re-solve.
  //
  // An operator double-presses because the first press looks like it did
  // nothing -- there is a fetch and a 300ms fade before anything appears. A
  // live solve landing while a press is mid-flight is the same race arriving
  // down a different wire.
  if (swapBusy) return;
  if (!territories) return;
  swapBusy = true;
  const wasOn = territoriesOn;
  // TAKE THE OVERLAY OUT OF SERVICE BEFORE FREEING IT. This function awaits a
  // fetch below, and across that await the old coverage object still points
  // at the geometry disposed on the next line. The onCleanPct subscriber's
  // guard reads `coverage` and `territoriesOn`, so with both still set, any
  // emit during the fetch calls seek() -> setXYZ on a freed
  // BufferAttribute. Two live paths reach it: a gsap tween from an earlier
  // pop is still running for up to 0.8s, and a new pop can arrive over the
  // socket at any moment.
  //
  // popsStale and resetAll() below were written for exactly this hazard but
  // both land AFTER the await, so neither one covers the window. Order is
  // the whole fix: nothing can find a dangling buffer if the overlay is
  // already out of service when the buffer goes.
  //
  // Clearing `coverage` here also fixes the permanent version of the same
  // bug: when the fetch fails, makeTerritories returns null, the `if` below
  // never runs, and coverage would otherwise keep pointing at freed geometry
  // for the rest of the demo rather than for 200ms.
  territoriesOn = false;
  coverage = null;
  popsStale = true;      // set BEFORE the await too, for the same reason
  scene.remove(territories.points);
  territories.dispose?.();        // free the old buffers, not just unparent
  territories = await makeTerritories(scene, {
    file: bodyFile(),
    scale: 2.2,
    offset: new THREE.Vector3(BODY_X, PLINTH_TOP, BODY_Z),
    pointSize: 9.0,
  });
  if (territories) {
    territories.points.rotation.y = CHAIR_YAW;
    coverage = makeCoverage(territories, territories.data);
    // A NEW PERSON HAS NOT BEEN WASHED, so the swap ends the cycle. The
    // fresh sweep starts at head 0 while the counter is still wherever the
    // last cycle left it, so without this the first emitted fraction seeks
    // straight to that level and paints most of the new body in one frame:
    // swapping at 70% would flash 70% of the person clean with no sweep at
    // all, on the beat where a judge is watching the partition re-solve.
    //
    // The counter goes back too, not just the cells -- the number and the
    // body have to agree, which is the whole reason they read one value.
    //
    // The script presses `n` at 0:52 and `s` at 1:12, so this never happens
    // mid-scrub in the run of show -- but an operator under pressure will
    // press it, and measured on that path the new body flashed to 563 of its
    // cells at 67% with no sweep at all. The cause is not the reset failing:
    // Python is still scrubbing the OLD person and its next pop lands 200ms
    // later, so the in-flight cycle simply carries on into the new body.
    //
    // resetAll() is the existing definition of "no cycle is running": it
    // clears cycleLive, the count, the sweep, the splotches and the foam
    // together. Reusing it rather than writing a partial version here, which
    // is how the counter and the dirt end up disagreeing.
    resetAll();
    if (wasOn) {
      territoriesOn = true;
      territories.show(300);
      showCounts(territories);
      // PUT THE CAMERA BACK ON THE SCAN. resetAll() ends with
      // setShot('wide', 0.6) because it is the end-of-cycle reset, and wide is
      // the right framing for "nothing is happening". It is the wrong framing
      // here: the overlay was up when this swap started, so the audience is
      // looking AT the measured body, and the swap swings the camera off it on
      // the exact beat the partition re-solves.
      //
      // MEASURED, not reasoned: a live solve landing with the scan up left
      // shot='wide' while territoriesOn, the plinth and the cloud all still
      // read true -- so every state check passed and the picture was still
      // wrong. The screenshot is what caught it.
      //
      // The 'n' key never exposed this because the run of show presses it at
      // 0:52 with the camera already wide. Restoring only when `wasOn` means
      // that path is untouched: a swap from a hidden overlay still ends wide.
      setShot('scan');
    }
  }
  // Released whether or not the fetch produced a body, so a failed swap
  // does not wedge the key for the rest of the demo.
  swapBusy = false;
}

/** SAY WHOSE BODY IS ON SCREEN. Reads the module state the socket sets.
 *
 * THE WORDING IS THE WHOLE FUNCTION, and docs/MEASURED-NOT-TYPED.md is
 * explicit about why: "a generic body drawn as if it were the person in the
 * chair is the exact failure this document exists to prevent."
 *
 * Three states, and the middle one is the one that needed inventing:
 *
 *   (nothing)            no backend. The page draws the baked body.json and
 *                        says nothing, exactly as it did before this panel
 *                        existed. SILENCE, NOT "GENERIC" -- see below.
 *   BODY PART-MEASURED   some dimensions came off depth, the rest are still
 *   3 OF 9 DIMENSIONS    the population table, and the solver has rebuilt
 *                        the body from the measured ones.
 *   BODY MEASURED        every dimension the model takes was measured.
 *
 * WHY "PART-MEASURED" AND NOT "MEASURED". On the real recording this was
 * built against, three of the nine dimensions reach confidence 0.882 and six
 * sit at exactly 0.0 -- nothing samples a circumference off 3D joints. Those
 * six are anatomy.ADULT, the typical-adult table, and the body on screen is
 * genuinely this person's shoulder width wearing a typical adult's chest.
 * "MEASURED" claims the chest too. The number beside it is what makes the
 * word checkable rather than a softer adjective doing the same overclaiming.
 *
 * WHY THE NO-BACKEND PAGE SAYS NOTHING RATHER THAN "BODY GENERIC". The spec
 * asks the HUD to distinguish measured from generic, and an empty panel does
 * distinguish them -- but a label has to be TRUE of the thing it points at,
 * and with no backend there is no person in the chair to be generic about.
 * The word would be describing a drawing of nobody. It is also the one
 * change that would alter the no-backend page, which rule 2 forbids
 * outright. The modes strip already carries "scanned body" as the standing
 * claim for that state, and this panel appears the moment a real measurement
 * contradicts or confirms it.
 */
function showBody() {
  const host = document.getElementById('body');
  if (!host) return;
  // Nothing measured means nothing to say. This is the no-backend state and
  // it must stay silent -- the panel has opacity 0 until .on is added, so an
  // untouched page never paints it at all.
  if (!bodyMeasuredDims) { host.classList.remove('on'); return; }
  const all = bodyMeasuredDims >= bodyTotalDims && bodyTotalDims > 0;
  const word = all ? 'MEASURED' : 'PART-MEASURED';
  // NAME THE SOURCE, NOT JUST THE VERDICT. "3 OF 9 DIMENSIONS" is the claim
  // a judge can check against the body they are looking at; the frame count
  // is how much depth stands behind it.
  const rows = [
    `<div class="row hd">BODY ${word}</div>`,
    `<div class="row n">${bodyMeasuredDims} OF ${bodyTotalDims} DIMENSIONS`
      + `<span class="k">  ${bodyFrames} FRAMES OF DEPTH</span></div>`,
  ];
  // THE MEASUREMENT AND THE DRAWING ARE TWO DIFFERENT CLAIMS. Between the
  // numbers settling and the solver finishing -- about a second, and longer
  // on a slower body -- the backend has measured this person while the shape
  // on screen is still the bake. Saying "measured" during that window would
  // point the word at geometry nobody has rebuilt yet.
  rows.push(bodySolved
    ? `<div class="row ok">BODY REBUILT FROM THESE NUMBERS</div>`
    : `<div class="row k">DRAWING THE BAKED BODY — NOT YET REBUILT</div>`);
  host.innerHTML = rows.join('');
  host.classList.add('on');
}

function showCounts(t) {
  const host = document.getElementById('counts');
  if (!host) return;
  if (!t || !t.stats) { host.classList.remove('on'); return; }
  const per = t.stats.per_arm || {};

  // NAME THE BODY PARTS, NOT JUST THE COUNTS. The bake already labels every
  // cell with one of thirteen anatomical regions -- forearm_L, upper_arm_R,
  // thigh_L and so on -- and nothing on screen read them, so the panel said
  // "ARM 0  273 cells" where it could say which part of a person that is.
  //
  // It matters because the pitch's central claim is that the plan comes from
  // the body rather than from a script. A number proves nothing; "arm 2 owns
  // the left forearm" is a sentence a judge can check against what they are
  // watching. It also survives the body swap for free: pressing `n` re-solves
  // the partition and these names change with it.
  //
  // Counted from the cells themselves rather than from a second table, so it
  // cannot drift from what is drawn.
  const cells = t.data && t.data.cells;
  const byArm = {};
  if (cells && cells.region && cells.owner) {
    for (let i = 0; i < cells.owner.length; i++) {
      const a = cells.owner[i];
      if (a < 0 || a > 3) continue;
      (byArm[a] || (byArm[a] = {}))[cells.region[i]] =
        (byArm[a][cells.region[i]] || 0) + 1;
    }
  }
  /** The regions an arm owns, biggest first, in words a person would use. */
  const partsOf = (a) => Object.entries(byArm[a] || {})
    .sort((x, y) => y[1] - x[1])
    .slice(0, 2)                       // two is enough to read at a glance
    .map(([r]) => r.replace(/_L$/, ' L').replace(/_R$/, ' R').replace(/_/g, ' '))
    .join(', ');

  const rows = [0, 1, 2, 3].map((a) => {
    const n = per[a] ?? per[String(a)] ?? 0;
    const parts = partsOf(a);
    return `<div class="row a${a}">ARM ${a}  ${String(n).padStart(4)} cells`
         + (parts ? `<span class="part">  ${parts}</span>` : '')
         + `</div>`;
  });
  const un = per['-1'] ?? 0;
  rows.push(`<div class="row un">OUT OF REACH  ${un}</div>`);

  // THE CONTESTED CELLS, WHICH ARE WHAT "AGENTIC" MEANS HERE.
  //
  // The project's own title is "Agentic Multi-Arm Robotic AI Bathing
  // Assistant", and that word was doing no work on screen: the governor panel
  // shows four fixed lines, so four arms coordinating read as a caption
  // rather than as a result.
  //
  // The bake has carried the real number since it was first exported and
  // nothing read it. 433 of the 5824 cells on body A are reachable by more
  // than one arm -- genuinely contested, claims that had to be resolved --
  // and the split across the arms is 94/132/124/83. On body B the solver
  // renegotiates to 150/88/92/152 because the body is a different shape.
  //
  // That is the difference between four arms following a script and four
  // agents dividing a job, and it is one line of the solver's own output.
  const cls = t.stats.per_class || {};
  const contested = cls.contested ?? 0;
  if (contested) {
    rows.push(`<div class="row cont">CONTESTED  ${contested}`
            + `<span class="part">  claimed by more than one arm</span></div>`);
  }

  // AND WHO CAN MOVE AT THE SAME TIME. The contested line above says the four
  // arms had to divide a job; this says what they concluded about doing it
  // together.
  //
  // `phases` is the solver's conflict graph coloured: arms in the same group
  // can work simultaneously without reaching into one another, and a second
  // group has to wait. It comes straight from partition.solve and has been
  // computed on every bake since the beginning -- the export took only the
  // territories and threw the schedule away, so it existed on disk nowhere
  // and on screen nowhere.
  //
  // It genuinely differs per body, which is the whole point: body A splits
  // [0,2,3] then [1], body B splits [0,2] then [1,3]. Press `n` and this line
  // renegotiates along with the cell counts. A fixed caption could not.
  const ph = t.data && t.data.phases;
  if (Array.isArray(ph) && ph.length) {
    const groups = ph.map((g) => g.map((a) => `ARM ${a}`).join(' + '));
    const together = ph.length === 1
      ? 'all at once'
      : `${ph.length} passes`;
    rows.push(`<div class="row sched">TOGETHER  ${together}`
            + `<span class="part">  ${groups.join(', then ')}</span></div>`);
  }
  host.innerHTML = rows.join('');
  host.classList.add('on');
}

/** Which arm owns the body part someone just asked for, and light it.
 *
 *  Plain words in, because a person in a chair says "my arms", not
 *  "forearm_L". Each word maps to the bake's own region names, and the arm is
 *  whichever one owns the most cells of those regions -- read from the same
 *  per-cell ownership the counts panel prints, so the answer cannot drift
 *  from what is on screen.
 */
// LEFT AND RIGHT, NOT ARM AND LEG -- because that is what the partition can
// actually honour. Counted from both bakes: the four arms own ONLY forearms
// and upper arms. Hands, legs and torso are unreachable from where the arms
// are mounted, which is the same fact the counts panel reports as OUT OF
// REACH 5125.
//
// Offering "my legs" would have hit the no-arm-can-reach reply three times
// out of four. Left versus right is the honest version AND the better
// demonstration: ask for the left and arm 3 answers with 164 patches, ask for
// the right and arm 0 answers with 273. Genuinely different robots, from the
// solver's own split.
const SPOT_WORDS = [
  [/\bleft\b/,  ['forearm_L', 'upper_arm_L']],
  [/\bright\b/, ['forearm_R', 'upper_arm_R']],
  // Unqualified "my arm" takes whichever side the solver gave more of.
  [/\barms?\b/, ['forearm_L', 'forearm_R', 'upper_arm_L', 'upper_arm_R']],
];

function focusSpokenSpot(said) {
  const t = String(said || '').toLowerCase();
  const hit = SPOT_WORDS.find(([re]) => re.test(t));
  if (!hit || !territories || !territories.data) {
    say('I can focus on your left arm or your right arm. Which one?');
    return;
  }
  const want = new Set(hit[1]);
  const cells = territories.data.cells;
  const tally = [0, 0, 0, 0];
  for (let i = 0; i < cells.owner.length; i++) {
    const a = cells.owner[i];
    if (a >= 0 && a <= 3 && want.has(cells.region[i])) tally[a] += 1;
  }
  const best = tally.indexOf(Math.max(...tally));
  if (tally[best] === 0) {
    say('No arm can reach there from where they are mounted.');
    return;
  }
  // Make sure the scan is up, or the highlight has nothing to land on.
  if (!territoriesOn) {
    territoriesOn = true;
    if (bodyPlinth) bodyPlinth.visible = true;
    if (bodyShadow) bodyShadow.visible = true;
    territories.show();
    showCounts(territories);
    setShot('scan');
  }
  territories.focus(best);
  fleet[best]?.arm.setPhase('hover');
  flash(`ARM ${best} TAKING IT`);
  play('ding');
  say(`Arm ${best} has that. ${tally[best]} patches.`);
}

/** Answer a question out loud from what is on screen RIGHT NOW.
 *
 *  THE AGENT HALF OF "voice support / personal agent" (BRAINSTORM-2). Every
 *  other voice intent makes the chair DO something; these make it answer.
 *  The difference matters for the pitch: a machine that only takes orders is
 *  a remote control, and the brainstorm asked for an agent.
 *
 *  WHY NOT A LANGUAGE MODEL. It would answer from its training, fluently,
 *  about a machine it cannot see, and a judge could not check a word of it.
 *  Every answer here is read out of live state while the projector is
 *  showing that same state, so it is checkable in the room. It also needs no
 *  API key on a laptop strangers will handle, no venue wifi, and adds no
 *  latency to the beat that has to feel instant.
 *
 *  EVERY BRANCH READS STATE AT CALL TIME, never a cached string. Ask "how
 *  clean am I" twice during a wash and the two answers differ, because the
 *  counter moved between them. That is the whole point; a fixed sentence
 *  would be indistinguishable from a caption.
 */
function answerQuestion(kind) {
  // `recs` and `cleaned` are declared far below this function (they cannot
  // exist before the avatar loads) and this is hoisted above them, so a
  // question asked during the boot window would hit the temporal dead zone.
  // One TDZ throw takes the whole keyboard with it on this page. typeof is
  // the only check that is safe before declaration -- see shotNow()'s note.
  const haveSplotches = typeof recs !== 'undefined'
                     && typeof cleaned !== 'undefined' && recs.length;

  if (kind === 'ask-clean') {
    if (!haveSplotches) { say('I have not started your wash yet.'); return; }
    const pct = Math.round(100 * cleaned / recs.length);
    const left = recs.length - cleaned;
    if (pct >= 100) { say('All done. You are completely clean.'); return; }
    if (pct === 0)  { say('I have not started yet. Say wash when you are ready.'); return; }
    // The count, not just the percent: "two patches left" is a thing the
    // person can look down and verify, which a percentage is not.
    say(`You are ${pct} percent clean. ${left} ${left === 1 ? 'patch' : 'patches'} left.`);
    return;
  }

  if (kind === 'ask-arms') {
    // STRAIGHT FROM THE SOLVER's per-cell ownership, the same source the
    // counts panel draws. Swap the body with `n` and this answer changes,
    // because the partition genuinely re-solved.
    const cells = territories && territories.data && territories.data.cells;
    if (!cells || !cells.owner || !cells.region) {
      say('I have not measured you yet. Press the scan and I will.');
      return;
    }
    const byArm = {};
    for (let i = 0; i < cells.owner.length; i++) {
      const a = cells.owner[i];
      if (a < 0 || a > 3) continue;
      (byArm[a] || (byArm[a] = {}))[cells.region[i]] =
        (byArm[a][cells.region[i]] || 0) + 1;
    }
    // ONE ARM, THE BUSIEST, not all four. Four clauses spoken aloud is
    // fifteen seconds of a two-minute demo and nobody retains it; the panel
    // on screen already carries the full breakdown for anyone who wants it.
    const totals = [0, 1, 2, 3].map(
      (a) => Object.values(byArm[a] || {}).reduce((s, n) => s + n, 0));
    const best = totals.indexOf(Math.max(...totals));
    const parts = Object.entries(byArm[best] || {})
      .sort((x, y) => y[1] - x[1]).slice(0, 2)
      .map(([r]) => r.replace(/_L$/, ' left').replace(/_R$/, ' right')
                    .replace(/_/g, ' '))
      .join(' and your ');
    if (!parts) { say('I have not measured you yet.'); return; }
    say(`Arm ${best} is doing the most. It has your ${parts}. `
      + `${totals[best]} patches of you.`);
    return;
  }

  if (kind === 'ask-care') {
    const s = Math.floor(careSeconds);
    if (s < 1) { say('I have not started looking after you yet.'); return; }
    // Spoken as minutes once it is long enough to be worth the word. The HUD
    // shows raw seconds because it updates every frame; a sentence does not.
    const mins = Math.floor(s / 60), secs = s % 60;
    const t = mins ? `${mins} ${mins === 1 ? 'minute' : 'minutes'}`
                   + (secs ? ` and ${secs} seconds` : '')
                   : `${s} ${s === 1 ? 'second' : 'seconds'}`;
    // AND THE CLAIM THAT MATTERS, attached to the number. The counter's
    // whole reason for existing is the second line: nobody had to lift them.
    say(`${t} of care so far. Nobody had to lift you for any of it.`);
    return;
  }

  if (kind === 'ask-vitals') {
    // THE SAME NUMBER THE READOUT IS DRAWING, read off the element rather
    // than recomputed, so the spoken answer cannot disagree with the screen
    // a judge is looking at while it is spoken.
    const shown = document.getElementById('pct')?.textContent || '';
    const bpm = /(\d+)\s*BPM/i.exec(shown);
    if (bpm) { say(`Your heart rate is ${bpm[1]}. That is normal.`); return; }
    // Not in vitals mode: open it, and let its own enter() start the sensor
    // rather than inventing a number here.
    say('Let me check.');
    setMode('vitals');
    return;
  }
}

function showGovernor(on) {
  const host = document.getElementById('governor');
  if (!host) return;
  if (!on) { host.innerHTML = ''; return; }
  host.innerHTML = '';
  // THE REAL GOVERNOR'S OWN WORDS, when the bake carried them.
  //
  // `stats.verdicts` is what fleet.py actually replied when export_body.py
  // proposed real targets on THIS body: its own action and its own reason
  // string, not a label written here. Swapping the body with `n` can change
  // them, because it is a different person and the solver says so -- body A
  // and body B do not produce the same set.
  //
  // GOV_LINES below stays as the fallback for a bake that predates this, or
  // one made on a machine without scipy. A blank safety panel on stage would
  // be worse than four true-but-static lines.
  const baked = territories?.data?.stats?.verdicts;
  const lines = (Array.isArray(baked) && baked.length)
    ? baked.map((v) => ({
        k: v.action === 'clear' ? 'clear'
         : v.action === 'hold' ? 'hold' : 'refuse',
        // Pad the action so the reasons line up in a column, the way the
        // hand-written lines did. ESTOP is the longest at five.
        t: `${v.action.toUpperCase().padEnd(6)}  ${v.reason || ''}`.trimEnd(),
      }))
    : GOV_LINES;
  lines.forEach((l, i) => {
    const el = document.createElement('div');
    el.className = `v ${l.k}`;
    el.textContent = l.t;
    host.appendChild(el);
    // Staggered so they read as decisions arriving one after another rather
    // than as a static list that was always there.
    setTimeout(() => el.classList.add('on'), 220 + i * 260);
  });
}

/** Put a tool away: shrink it to nothing, then hide it.
 *
 *  The scale is remembered on the object the first time it is touched, so
 *  this can never lose a measured value -- the spoon is authored at 0.55 and
 *  the sponge at 1.0, and each was set against the character with a ruler.
 *  A tween that ended on a literal would quietly resize one of them.
 */
function swapOut(obj) {
  if (!obj) return;
  if (obj.userData.toolScale === undefined) obj.userData.toolScale = obj.scale.x;
  if (!obj.visible) return;                   // already away, nothing to play
  // WINDOW.GSAP WITH A FALLBACK, like every other tween on this page. A bare
  // `gsap` would throw if the vendored copy failed to load, and this runs
  // inside a mode switch -- the throw would take the whole keypress with it,
  // leaving the demo stuck in shower. Without gsap the tool just swaps
  // instantly, which is exactly what it did before this change.
  if (!window.gsap) { obj.visible = false; return; }
  window.gsap.killTweensOf(obj.scale);
  window.gsap.to(obj.scale, {
    x: 0.001, y: 0.001, z: 0.001, duration: 0.22, ease: 'back.in(2)',
    onComplete: () => { obj.visible = false; },
  });
}

/** Take a tool out: appear at nothing and grow to the authored size. */
function swapIn(obj) {
  if (!obj) return;
  if (obj.userData.toolScale === undefined) obj.userData.toolScale = obj.scale.x;
  const to = obj.userData.toolScale;
  if (!window.gsap) { obj.scale.setScalar(to); obj.visible = true; return; }
  window.gsap.killTweensOf(obj.scale);
  // STARTS SMALL EVERY TIME, including when it was already showing. Entering
  // feed twice in a row (which `shift+8` from inside feed does) would
  // otherwise play no animation on the second one, and the beat the script
  // narrates would happen only sometimes.
  obj.scale.setScalar(0.001);
  obj.visible = true;
  // A SHORT HOLD FIRST, so the two tools do not cross in mid-air. The sponge
  // takes 0.22s to shrink away; starting the spoon at 0.12 means the hand is
  // visibly empty for a moment, which is what makes it read as an exchange
  // rather than a morph.
  window.gsap.to(obj.scale, {
    x: to, y: to, z: to, duration: 0.26, delay: 0.12, ease: 'back.out(2)',
  });
}

/** Carry the bowl from the tray to the mouth, then hold it there.
 *
 *  gsap is already vendored and drives every other motion on this page, so
 *  this uses it rather than adding a second animation system. If gsap failed
 *  to load the bowl simply appears at the mouth, which is worse-looking and
 *  still legible -- the same degradation the counter already takes.
 */
function liftBowl(prop) {
  const obj = prop || bowl;
  if (!obj) return;
  if (bowlTween) bowlTween.kill();
  obj.position.set(BOWL_REST.x, BOWL_REST.y, BOWL_REST.z);
  if (!window.gsap) { obj.position.set(BOWL_MOUTH.x, BOWL_MOUTH.y, BOWL_MOUTH.z); return; }
  bowlTween = window.gsap.to(obj.position, {
    x: BOWL_MOUTH.x, y: BOWL_MOUTH.y, z: BOWL_MOUTH.z,
    duration: 1.5,
    // Slow out of the tray, slow into the face. A linear carry reads as a
    // machine moving an object; an eased one reads as being careful with it,
    // which is the whole point of the beat.
    ease: 'power2.inOut',
  });
}

function setMode(next) {
  if (!MODES[next]) return;
  modeTimers.forEach(clearTimeout); modeTimers = [];
  if (modeTick !== null) { clearTimeout(modeTick); modeTick = null; }
  // The mode just left is marked done rather than simply dimmed, so the strip
  // reads as progress through the product rather than as a menu.
  document.querySelectorAll('#modes .mode').forEach((el) => {
    const m = el.dataset.mode;
    el.classList.toggle('active', m === next);
    if (m === mode && m !== next) el.classList.add('done');
    if (m === next) el.classList.remove('done');
  });
  if (MODES[mode] && MODES[mode].leave && mode !== next) MODES[mode].leave();
  mode = next;
  const label = document.getElementById('label');
  if (label) label.textContent = MODES[next].label;
  // RESTORE THE PERCENT ON THE WAY OUT OF VITALS. Vitals overwrites #pct with
  // a BPM string on its own timer; without this, leaving it stranded a heart
  // rate under a CLEANLINESS label -- two readouts disagreeing on the one
  // number a judge is watching.
  // `recs` and `cleaned` are declared further down the module (the splotches
  // cannot exist before the avatar loads). setMode is hoisted above them, so
  // a keypress during the boot window would hit the temporal dead zone and
  // throw -- which on this page means the whole keyboard handler dies, not
  // just this line. typeof is the one check that is safe before declaration.
  // THE FINALE'S GREEN BELONGS TO THE FINALE. body.complete is added by
  // finale() and removed only by resetCounter(), so after a completed cycle
  // the heart rate rendered in celebration green under HEART RATE, and voice
  // mode's SAY SOMETHING did too. A mode change is not a reset, so it must not
  // call resetCounter -- it just stops claiming the last cycle's result.
  if (next !== 'shower') document.body.classList.remove('complete');
  if (next !== 'vitals') {
    borrowPct(false);
    const pct = document.getElementById('pct');
    const ready = typeof recs !== 'undefined' && typeof cleaned !== 'undefined';
    if (pct) pct.textContent = ready && recs.length
      ? `${Math.round(100 * cleaned / recs.length)}%` : '0%';
  }
  // THE SCAN BELONGS TO THE SHOWER BEAT. `t` was the only thing that ever
  // cleared it, so the run of show -- scan with `b`, then feed with `8` --
  // left the per-arm cell counts and the two-pass schedule sitting on top of
  // the character through the whole feeding beat. Caught on a screenshot of
  // feed: the panel overlaps the bowl going to the mouth, which is the one
  // thing that beat exists to show.
  //
  // The overlay and the plinth go with it. They are the scan's evidence, and
  // evidence for a claim nobody is making any more is just clutter. `t`
  // brings all of it back, unchanged.
  if (next !== 'shower' && territoriesOn) {
    territoriesOn = false;
    if (bodyPlinth) bodyPlinth.visible = false;
    if (bodyShadow) bodyShadow.visible = false;
    if (territories) territories.hide();
    showCounts(null);
  }
  // EACH MODE BRINGS ITS OWN FRAMING, set here rather than in the keydown
  // handler so a spoken command reaches the same shot a keypress does.
  setShot({ shower: 'wide', feed: 'feed', vitals: 'room', voice: 'room' }[next]
          || 'wide');
  MODES[next].enter();
  play('click');
}

// TOP-LEVEL AWAIT IS A TRAP. If makeAvatar REJECTS (the GLB 404s because
// vendor.sh was not run, or the file is corrupt), module evaluation stops
// dead: the keyboard listener below is NEVER registered and the websocket is
// never opened. Verified by blocking mini-character.glb in Playwright — the page
// sat on "PRESS ANY KEY TO START" forever, keys did nothing, and the only
// clue was "Failed to fetch" in a console nobody has open in kiosk mode.
//
// Catch it, show the operator what is wrong ON SCREEN, and let the rest of
// the module finish loading so the keyboard fallbacks still work.
let avatar = null;
try {
  avatar = await makeAvatar(scene);
  // The bottom HUD band is ~19vh. Raise the character so its feet clear it.
  avatar.root.position.y = 0.55;
  // FACE THE CAMERA. Kenney's model fronts -Z and the camera sits at +Z, so
  // the demo was showing the character's BACK -- with the arms tucked behind
  // the torso, which also hid the splotches. Caught by rendering from both
  // sides instead of assuming; the eyes were the giveaway.
  // The MINI pack fronts +Z (the blocky pack fronted -Z), so no flip.
  avatar.root.rotation.y = 0;

  // ---- THE WHEELCHAIR -------------------------------------------------
  // THE PRODUCT IS A WHEELCHAIR, not an arm on a table. Everything else on
  // this screen was solved for a standing person beside a plinth, which is a
  // demo of a subsystem rather than a picture of the thing being pitched.
  //
  // TAKEN, NOT BUILT. `wheelchair.glb` is already vendored in web/assets --
  // one of four Kenney chairs sitting unused since the asset pack landed.
  // Nothing here is modelled.
  //
  // TURNED SIDE-ON. Seated legs fold along the body's own -Z, so head-on the
  // thighs point straight at the camera and foreshorten into two pale discs:
  // the pose is correct and completely unreadable. A quarter turn puts the
  // fold across the frame where an audience can see the person is sitting.
  //
  // The chair's own GLB sits its wheels at y=0 (measured: ymin 0.000, size
  // 0.50 x 0.495 x 0.582), so it drops straight onto FLOOR_Y with no offset
  // hunting -- the step the earlier seated attempt skipped when it scaled a
  // chair to "0.75 of body height" and got a chair worn like a belt.
  try {
    const chairGltf = await new GLTFLoader().loadAsync('./assets/wheelchair.glb');
    const chair = chairGltf.scene;
    chair.traverse((o) => {
      if (!o.isMesh) return;
      o.castShadow = o.receiveShadow = true;
      // Same flat toon language as everything else on stage. A PBR chair
      // beside a cel-shaded person reads as two different shows.
      if (o.material && o.material.map) {
        o.material = new THREE.MeshToonMaterial({ map: o.material.map, gradientMap: ramp });
      }
    });
    chair.scale.setScalar(CHAIR_SCALE);
    chair.position.set(0, FLOOR_Y, 0);
    chair.rotation.y = CHAIR_YAW;

    // ---- THE PULSE SENSOR, ON THE CHAIR ---------------------------------
    // The brainstorm asks for the vitals sensing to be ON THE CHAIR, on its
    // own line, and the hardware list names the part: a MAX30102 pulse
    // oximeter. Until now the heart rate appeared in the HUD from nowhere, so
    // the claim that the chair reads you was made only in text.
    //
    // Placed from the seat surface that is already solved above rather than
    // by eye: an armrest sits about 0.20 above a seat, and 0.82 of the
    // chair's half-width puts the pad ON the arm instead of beside it.
    // Parented to the chair so it inherits the yaw and cannot drift from it.
    const padGeo = new THREE.BoxGeometry(0.085, 0.022, 0.13);
    sensorPad = new THREE.Mesh(padGeo, new THREE.MeshToonMaterial({
      color: 0x1d2740, gradientMap: ramp }));
    // Chair-LOCAL coordinates: the parent carries the rotation.
    // FORWARD ALONG THE ARM, where a wrist actually rests. The first
    // placement sat level with the seated body and was completely occluded by
    // it -- a screenshot showed the vitals beat with no sensor visible at all,
    // which is the whole point of putting one there. Moving it toward the
    // front of the armrest clears the torso and is also where a MAX30102
    // belongs: under the heel of the hand, not under the elbow.
    // ON THE ARMREST, PARTLY BEHIND THE PERSON, AND THAT IS THE HONEST
    // ANSWER. Three placements and the geometry settles it: the seated
    // cartoon's shoulders overhang a chair 0.9 world units wide, and the
    // chair's three-quarter yaw puts the near armrest behind the torso. The
    // only local positions that clear the body either swing across the
    // chair's midline (world x goes to 0 -- in front of the person, on
    // nothing) or sit outside the chair entirely.
    //
    // So the sensor stays where a MAX30102 actually goes: on the near
    // armrest under the heel of the hand, forward enough that the light is
    // visible past the torso. A prop moved somewhere it could not be, to be
    // seen better, would be the thing this whole overlay exists not to do.
    sensorPad.position.set(0.5 * 0.82 / 2, (CHAIR_H * SEAT_FRAC) + 0.13, 0.17);
    sensorPad.castShadow = true;
    chair.add(sensorPad);

    // The reading light. Emissive rather than lit, so it reads as a lamp
    // rather than as a painted dot, and small enough that it never competes
    // with the four arm colours.
    sensorLed = new THREE.Mesh(
      new THREE.BoxGeometry(0.03, 0.006, 0.03),
      // 0xe8143c, NOT a lighter red. Measured in RGB distance against the
      // four arm colours: 0xff5566 sits 0.21 from arm 3's coral, close enough
      // to read as that arm's light rather than the chair's. This is 0.51
      // from the nearest and is a deeper, more clinical red, which is also
      // what a pulse oximeter actually emits.
      new THREE.MeshBasicMaterial({ color: 0xe8143c }));
    sensorLed.position.set(0, 0.014, 0);
    sensorPad.add(sensorLed);
    scene.add(chair);

    // Seat the body and put it where the chair's seat actually is. setSeated
    // returns the root Y it chose, so the two are solved against each other
    // rather than both being eyeballed.
    // setSeated folds the legs and picks its own drop; then override the root
    // with the height solved against THIS chair's seat, so body and chair are
    // one solution rather than two guesses that have to agree by luck.
    // THE BOWL, loaded beside the chair so one failed fetch cannot leave the
    // feeding mode half-built. Hidden until that mode is chosen.
    try {
      const bg = await new GLTFLoader().loadAsync('./assets/bowl-soup.glb');
      bowl = bg.scene;
      bowl.traverse((o) => {
        if (!o.isMesh) return;
        o.castShadow = true;
        if (o.material && o.material.map) {
          o.material = new THREE.MeshToonMaterial({ map: o.material.map, gradientMap: ramp });
        }
      });
      // MEASURED, after a screenshot showed a bowl bigger than the
      // character's head. The Kenney bowl ships 0.50 units wide and I scaled
      // it UP by 2.4 on the assumption that food-kit props are small; they
      // are not, they are authored at roughly the same scale as the
      // characters. A soup bowl held by a person is about 0.15 of their
      // height, the seated cartoon is about 1.5, so the bowl wants to be
      // ~0.22 wide: 0.22 / 0.50 = 0.44.
      bowl.scale.setScalar(0.44);
      bowl.position.set(BOWL_REST.x, BOWL_REST.y, BOWL_REST.z);
      bowl.visible = false;
      scene.add(bowl);
    } catch (e) {
      console.warn('bowl failed to load, feeding will be armless:', e);
    }

    // THE SPOON THE FEEDING ARM HOLDS. Vendored with the food kit and never
    // loaded until now, so nothing was built for this.
    //
    // SCALED FROM MEASUREMENT, not from taste, because the bowl taught this
    // lesson once already: food-kit props are authored at character scale, and
    // assuming they are small put a soup bowl bigger than the character's head
    // on screen. The spoon measures 0.666 long raw; 0.375 brings it to 0.25,
    // a little longer than the bowl is wide (0.221), which is the real-world
    // proportion of a soup spoon to a soup bowl.
    try {
      const sg = await new GLTFLoader().loadAsync('./assets/cooking-spoon.glb');
      spoon = sg.scene;
      spoon.traverse((o) => {
        if (!o.isMesh) return;
        o.castShadow = true;
        if (o.material && o.material.map) {
          o.material = new THREE.MeshToonMaterial({ map: o.material.map, gradientMap: ramp });
        }
      });
      // 0.55, NOT 0.375. The ruler said 0.375 gives a 0.25 spoon against a
      // 0.221 bowl, which is the real-world ratio -- and a screenshot showed
      // a sliver, because the model is 0.024 thick and no scale fixes that.
      // 0.55 makes it 0.366 long, comparable to the 0.30 sponge it replaces,
      // which is what has to read from ten feet. The bowl taught this same
      // lesson in the other direction.
      spoon.scale.setScalar(0.55);
      spoon.visible = false;
      spoon.rotation.z = Math.PI / 2;    // lying along the forearm, not across
      // PARENTED TO THE ARM, NOT THE SCENE. `scene.add` with a y of 0.62 put
      // it at the world origin under the chair, where a screenshot found it:
      // the sponge vanished correctly and nothing replaced it, so the arm
      // ended in a bare grip. The spoon has to hang off the same forearm the
      // sponge does or the swap reads as losing a tool rather than changing
      // one.
      //
      // robotarm.js exports the sponge but not its parent, so the sponge's
      // own parent IS the attach point -- and using it means the spoon lands
      // exactly where the sponge was without a second position to keep in
      // step with it.
      // Parked in the scene for now. The fleet does not exist yet at this
      // point in the boot -- the arms are built about 200 lines below -- so
      // the mount happens there, once there is a forearm to hang it on.
      scene.add(spoon);
    } catch (e) {
      console.warn('spoon failed to load, the arm keeps its sponge:', e);
    }

    // THE GLASS, for the pill beat. Pill feeding is its own line in the
    // brainstorm, and until now saying "here are your pills" produced the
    // same soup bowl -- the voice claiming one thing while the screen showed
    // another, which is the single worst kind of demo bug.
    //
    // There is no pill bottle in the Kenney food kit (checked every prop in
    // it). A glass of water is the honest stand-in: medication is taken WITH
    // water, and a small glass reads instantly different from a soup bowl at
    // projector distance, which is the whole job here.
    try {
      const gg = await new GLTFLoader().loadAsync('./assets/glass.glb');
      glass = gg.scene;
      glass.traverse((o) => {
        if (!o.isMesh) return;
        o.castShadow = true;
        if (o.material && o.material.map) {
          o.material = new THREE.MeshToonMaterial({ map: o.material.map, gradientMap: ramp });
        }
      });
      // 0.16 wide at scale 1; 0.81 puts it at ~0.13, a cup rather than a vase.
      glass.scale.setScalar(0.81);
      glass.position.set(BOWL_REST.x, BOWL_REST.y, BOWL_REST.z);
      glass.visible = false;
      scene.add(glass);
    } catch (e) {
      console.warn('glass failed to load, pills will be armless:', e);
    }

    avatar.setSeated(true);
    avatar.root.rotation.y = CHAIR_YAW;
    avatar.root.position.y = SEATED_ROOT_Y;
    avatar.root.position.z = SEAT_Z;
    window.__chair = chair;
  } catch (e) {
    // A missing chair must not cost the demo. The character stands, which is
    // what every previous run looked like, and the rest of the page is fine.
    console.warn('wheelchair failed to load, standing instead:', e);
  }

  // THE SCRUBBING ARM, ON SCREEN. The projector showed only the person, so the
  // audience never saw the scrub HAPPEN -- splotches just vanished off a limb
  // for no visible reason. A judge can now look back and forth: cartoon arm on
  // cartoon forearm, real arm on real forearm, same rhythm.
  //
  // Parked beside the character's LEFT arm (screen-right of centre after the
  // 180-degree rotation), at the height the splotches sit.
  //
  // THE ARM ITSELF IS BUILT AT MODULE SCOPE, not here. It has no dependency on
  // the avatar -- makeRobotArm takes only (scene, ramp) -- and building it
  // inside this try meant a GLB 404 took the robot down with the character,
  // leaving a literally empty room. Only the PLACEMENT stays here, because the
  // numbers below were solved against the loaded character's splotches.
  // MEASURED, not eyeballed. THESE TWO NUMBERS ARE THE BUG, NOT THE STATE:
  // BEFORE the offset below, at the CONTACT pose the sponge landed at world
  // (-1.41, 2.31, 1.25) while the splotches sat at (-0.80, 2.07, 0.39) -- a
  // 1.06 gap, the sponge reaching into empty space beside the arm, which is
  // worse than no robot at all because it looks like it is missing on purpose.
  // RE-MEASURED TODAY with the offset in place: sponge (0.68, 1.40, 0.21) vs
  // splotch (0.63, 1.33, 0.34) -- a gap of 0.16. Contact lands on the dirt.
  // Offset the base by the difference so contact lands ON the dirt.
  // The base sits OUT to the side and in FRONT, where the audience can see the
  // whole machine, and the arm reaches ACROSS to the forearm. Placing it so
  // the sponge landed on the dirt by translation alone buried the base behind
  // the character and left a sponge floating on a stick.
  // SOLVED, not tuned. Forward-kinematics of the arm at its CONTACT pose puts
  // the sponge at base-local (0, 1.13, -0.79); inverting that against the
  // measured middle splotch (-0.80, 2.06, 0.38) gives the base position for a
  // chosen yaw. I hand-tuned three numbers first and got a 1.2-unit miss --
  // with a target I can measure, guessing was the wrong tool.
  // Base = previous placement plus the MEASURED residual. My forward-kinematics
  // model predicted 0.01 and the scene gave 0.68, so the model was missing
  // something in the transform chain -- rather than keep refining a model that
  // disagrees with the renderer, measure the actual sponge position at the
  // contact pose and correct by the difference. The pose is fixed, so the
  // offset is constant.
  // ROLL, NOT YAW. The sponge sits at local x=0, so rotating about Y just
  // spins the arm in place -- a yaw sweep moved the base by 24px across its
  // whole range while the machine stayed hidden behind the character's arm.
  // Rolling about Z leans the whole arm in from the side, which is both how a
  // bench-mounted arm actually reaches and what puts the machine on screen:
  // base at screen x=368 instead of 486. Position re-solved for the roll so
  // the sponge still lands on the dirt.
  // RE-MEASURED for the blob body. The rounded character is a different size
  // and shape than the Kenney box, so the splotches moved and the old base
  // put the sponge 1.2 units out in empty space. Residual measured at the
  // contact pose and applied, same method as before.
  // RE-SOLVED for the Kenney mini character. The skinned model is a different
  // size and its dirty arm is on the opposite side of screen, so the old base
  // missed by 1.35 units AND approached from the wrong direction -- negative
  // roll put the machine BEHIND the character. Positive roll brings it in from
  // screen-right, where the splotches now are. Measured, not guessed.
  // BOLTED TO THE PLINTH TOP. This y is the SOLVED value (see the mount block
  // above); PLINTH_TOP is defined to equal it, not the other way round.
  robot.placeNear(PLINTH_X, PLINTH_TOP, PLINTH_Z);
  robot.root.rotation.z = 0.9;     // reaches in from screen-right
  // Its own contact shadow, on the plinth's top face rather than the floor.
  contactShadow(PLINTH_X, PLINTH_Z, 0.5, 0.8, PLINTH_TOP + 0.012);

  // ---- THE MEASURED BODY, AS DATA --------------------------------------
  // scrub3d's four-arm territory split, rendered as a point cloud over the
  // cartoon. See territories.js for why points and not a mesh.
  //
  // The cloud is authored for a real 1.75m adult standing at the origin; the
  // cartoon is a stylised figure seated in a chair. These two numbers fit one
  // to the other, and they are the only place that conversion happens.
  // ---- THE MEASURED BODY, SHOWN AS ITS OWN OBJECT ----------------------
  // FOUR ATTEMPTS AT OVERLAYING IT ON THE CARTOON, ALL WRONG, ALL VISIBLE IN
  // A SCREENSHOT. Scale 0.62 squeezed 1792 points into two blobs on the
  // thighs. Fitting hip-to-head against a stale bone reading drew stripes
  // through the legs. Re-fitting against live bones moved the stripes to the
  // torso and left them stripes.
  //
  // The cause is not a number. scrub3d's body is a REAL ADULT's proportions
  // -- a 448-vertex swept ellipse per limb, measured from depth. The cartoon
  // is a stylised figure with a head the size of its torso. No single scale
  // and offset maps one onto the other, and chasing one is exactly the
  // "build it yourself" this project is told not to do.
  //
  // So it stands BESIDE the person instead, on its own plinth, as the thing
  // the machine sees. That is a better demo anyway: a judge looks at the
  // cartoon, then at the measured body next to it, and understands in one
  // beat that the robot is working from a real scan rather than a script.
  // It also keeps the privacy claim exact -- the wireframe is data, clearly
  // not a picture of anybody.
  // SCALE 2.2: the bake is a seated body only 0.44 tall, and beside a 1.5
  // cartoon it read as four coloured sticks on a stump. 2.2 makes it about
  // the cartoon's own height, so the two read as the same person -- one drawn,
  // one measured. pointSize follows so the cells still touch.
  //
  // Y IS PLINTH_TOP, NOT FLOOR_Y. makeTerritories drops the cloud so its
  // lowest cell sits exactly on the offset, so passing the plinth's top face
  // stands it ON the plinth. Passing the floor left it hovering above one.
  territories = await makeTerritories(scene, {
    scale: 2.2,
    offset: new THREE.Vector3(BODY_X, PLINTH_TOP, BODY_Z),
    pointSize: 9.0,
  });
  if (territories) {
    // Face the same way as the person so the two read as the same body.
    territories.points.rotation.y = CHAIR_YAW;
    // Its own plinth, matching the arms', so it sits IN the room.
    const bp = new THREE.Mesh(
      new THREE.BoxGeometry(PLINTH_W * 0.8, PLINTH_H, PLINTH_D * 0.8),
      new THREE.MeshToonMaterial({ color: PLINTH_COL, gradientMap: ramp }));
    bp.position.set(BODY_X, PLINTH_TOP - PLINTH_H / 2, BODY_Z);
    bp.castShadow = bp.receiveShadow = true;
    // HIDDEN UNTIL THE SCAN IS UP. It stands at x -2.45, outside all four
    // arms, and for most of the two minutes it holds nothing. A cold read of
    // the frame called it "a forgotten fifth base" and was right to: an empty
    // pedestal off to one side reads as a leftover once the other four are
    // joined into one chassis. It appears with the body it exists to hold.
    bp.visible = false;
    bodyPlinth = bp;
    scene.add(bp);
    // ITS SHADOW GOES WITH IT. Hiding the pedestal alone left a dark
    // smudge on empty floor, which reads worse than the plinth did --
    // a shadow with nothing casting it.
    bodyShadow = contactShadow(BODY_X, BODY_Z, 0.45, 0.75, PLINTH_TOP + 0.012);
    if (bodyShadow) bodyShadow.visible = false;
    coverage = makeCoverage(territories, territories.data);
    console.log('territories loaded:', territories.stats);
  }

  // ---- THE REST OF THE FLEET, FROM THE MEASURED RIG --------------------
  // "4x Waveshare high-torque robotic arm. Each arm would act as an
  // independent AI agent... split the body into 4 sections? one arm handles
  // each section of the body" -- the brainstorm. One arm on a plinth is a
  // picture of a subsystem; arms on the chair are a picture of the product.
  //
  // THESE POSITIONS ARE READ, NOT TYPED. They used to be four literals in
  // this file, describing four arms on a RING of separate floor pads at
  // x +/-1.65 and +/-1.20, well outside the chair. That ring was invented:
  // tools/export_body.py solved the territories against
  // place_arms.ring_layout(4), a synthetic arc it fell back on because the
  // real optimiser did not finish, and this literal was a hand-drawn copy of
  // that arc. Nothing on screen came from a measurement, so the picture was
  // of a machine nobody has built.
  //
  // The rig the hardware is actually bolted to is
  // scrub3d/live/live_rig_openyam.json -- TWO arms, seat-relative, off a tape
  // measure rather than off a search. In the page's own axes it reads:
  //
  //   a0  x  0.425  z  0.20   the person's left, on the armrest plank
  //   a1  x -0.425  z  0.20   the person's right, facing it
  //
  // 850mm apart, 550mm above the floor, 200mm ahead of the seat, the two bases
  // facing each other across the person. export_body.py bakes that rig into
  // body.json's `arms`, so this reads the same numbers the partition was
  // solved against and the two can no longer disagree.
  //
  // IT USED TO BE THREE, from live_rig_table.json, and three was never bolted
  // to anything -- place_arms searched for three mounts that divide this body
  // well and found them. A search result is a proposal. The plank is the
  // product. The count on screen follows the tape measure now, which is also
  // why index.html's caption says TWO.
  //
  // NOTHING NEW IS MODELLED. makeRobotArm is already a factory and placeNear
  // already takes a position, so a fleet is a loop over the arm that has been
  // on screen all along.
  const rigArms = territories?.data?.arms || [];
  // THE CARTOON IS BIGGER THAN THE PERSON THE RIG WAS MEASURED ON. Measured:
  // the scanned body is 0.81m from seat to crown, the cartoon is 1.016 above
  // its own seat -- so the chair the arms bolt to is 1.25x real size, and a
  // real-scale rig planted on it would sit inside the character's hips.
  // Everything the arms are placed by scales by this one number.
  const RIG_SCALE = 1.25;
  // The real chair's seat height above its floor, in metres. The rig file
  // stores each arm mount as a height above the FLOOR, and the line below
  // re-expresses that as a height above the SEAT -- so an arm that clears a
  // real person's knees still clears the cartoon's, whose seat sits at a
  // different fraction of its own height.
  //
  // 0.48m is a standard wheelchair seat-to-floor height, and the measured rig
  // is consistent with it: both mounts come in at 0.55, i.e. 70mm above a
  // 480mm seat. Seventy millimetres is an ARMREST, which is exactly what
  // live_rig_openyam.json says the plank is laid across.
  //
  // THAT NUMBER USED TO BE 240mm AND 160mm, off the searched three-arm rig,
  // and the comment here called that "where armrest-mounted hardware actually
  // sits". It was describing a rig nobody had built. The measured plank sits
  // a third as high, so the arms now come off the armrests rather than out of
  // the air beside the chair.
  //
  // THIS WAS MISSING AND THE PAGE DIED ON IT. `REAL_SEAT_Y is not defined`
  // threw inside the avatar loader, which swallowed the character AND all
  // three arms and painted "NO CHARACTER -- run ./vendor.sh". A single
  // undeclared constant, and the symptom pointed at the asset pipeline.
  const REAL_SEAT_Y = 0.48;
  // ---- HOW BIG AN ARM IS, FROM THE SAME MEASUREMENT AS WHERE IT IS --------
  //
  // POSITION CAME FROM THE RIG AND SIZE DID NOT, and that is the whole bug in
  // one line. The arm is modelled at a fixed 1.42 page units root to sponge
  // (robotarm.js SPONGE_CHAIN), a length solved years ago against the dirt
  // splotches on a standing character. Nothing ever re-checked it against the
  // machine. Screenshot before this change: three arms so large they cover the
  // person completely -- you cannot see who is being washed.
  //
  // The two numbers that settle it, both measured, neither typed here twice:
  //
  //   the rig is 850mm across          live_rig_openyam.json "span_mm"
  //   the page draws it 1.0625 across  0.85 baked * RIG_SCALE 1.25
  //   => 1.25 page units per real metre
  //
  //   the OpenYAM reaches 722.6mm      kinematics_openyam.REACH_MAX,
  //                                    L2_MM 264 + TCP_Z_MM 458.6
  //   => it should be drawn 0.9032 units long
  //
  //   0.9032 / 1.42 = 0.636
  //
  // DERIVED, NOT TYPED, so the day somebody re-measures the plank or swaps
  // the arm the picture follows instead of drifting. RIG_SCALE appears in
  // both halves and cancels; it is left in because taking it out would make
  // the line stop reading as "real metres -> page units".
  const REACH_M = 0.7226;            // OpenYAM shoulder -> tool, metres
  const DRAWN_CHAIN = 1.42;          // robotarm.js SPONGE_CHAIN, page units
  const ARM_SCALE = (REACH_M * RIG_SCALE) / DRAWN_CHAIN;
  // Each arm keeps the region it reaches for. TWO arms, two regions, in the
  // order the bake writes them: a0 is the arm on the person's left and takes
  // the forearm the whole scrub choreography is already wired to, a1 faces it
  // from the right and takes the other forearm.
  //
  // THE LEG ENTRY IS GONE BECAUSE THE THIRD ARM IS. It was 'leg-left' for the
  // searched rig's third mount; with two arms nothing indexes position 2, and
  // leaving it would be a region assigned to an arm that does not exist. The
  // `|| 'forearm-right'` fallback below still covers a longer rig file, so a
  // future third mount draws rather than throwing.
  const RIG_REGION = ['forearm-right', 'forearm-left'];
  const FLEET = rigArms.map((a, i) => ({
    // body.json is metres, Y up, with the seat at the origin -- the same
    // axes as the page, so this is a scale and nothing else.
    x: a.pos[0] * RIG_SCALE,
    // HEIGHT COMES FROM THE SEAT, NOT THE FLOOR. The rig file stores z above
    // the FLOOR; the cartoon's floor and its seat are not the same distance
    // apart as the real one's. Measuring each mount from the seat surface
    // keeps an arm that clears a real person's knees clearing the cartoon's.
    y: SEAT_SURFACE_Y + (a.pos[1] - REAL_SEAT_Y) * RIG_SCALE,
    z: a.pos[2] * RIG_SCALE,
    // LEAN IN FROM THE SIDE THE ARM IS ON, by the angle the rig file MEASURED.
    //
    // This used to be `clamp(a.pos[0] * 1.8, -0.9, 0.9)` -- a lean invented
    // from how far out the base sits, with the baked `facing_deg` beside it
    // going unread. The bake has always written that field (export_body.py
    // computes the yaw off the base pose precisely so the file can say which
    // way an arm looks) and the page threw it away and guessed instead.
    //
    // On the measured rig the guess gave 43.8 degrees of roll, which at the
    // arm's true size tipped both machines down into the seat: screenshot
    // after the scale fix showed two mechanisms folded at the hips with their
    // sponges buried. The file says the bases face each other at +/-90.
    //
    // HALF THE MEASURED ANGLE, AND THE HALF IS THE ONE INVENTED NUMBER LEFT.
    // A full 90 degrees lays an arm flat on its side, because roll is a lean
    // about Z and the real base achieves that facing by YAW about its own
    // mount. The page's arm has no yaw joint at the base to spend it on, so
    // the lean is how it reaches across -- and leaning the whole way over is
    // a pose the metal does hold but the cartoon reads as toppled. Half of
    // the measured facing is an arm reaching in over the armrest, which is
    // what the plank is for. Sign follows the side, off the same field.
    //
    // It is derived from a measurement and clamped by one, so a re-measured
    // plank moves the picture. That is the property the old literal lacked.
    roll: THREE.MathUtils.clamp(
      THREE.MathUtils.degToRad(a.facing_deg ?? 0) * 0.5, -0.9, 0.9),
    region: RIG_REGION[i] || 'forearm-right',
  }));
  // FALL BACK RATHER THAN SHOW ONE ARM. A missing or malformed bake must not
  // cost the demo its fleet; the page has to boot with no backend at all.
  if (!FLEET.length) {
    console.warn('no rig in body.json; keeping arm 0 alone');
    fleet.push({ arm: robot, region: 'forearm-right' });
  } else {
    // Arm 0 is the one built far above and already placed against the
    // splotches. MOVE IT ONTO THE RIG rather than leaving it where it was:
    // half a fleet at measured positions and half at an invented one is the
    // drift this whole change exists to remove. It keeps every wire it has
    // (setPhase, strokeNow, the estop) because only its position changes.
    robot.placeNear(FLEET[0].x, FLEET[0].y, FLEET[0].z);
    robot.root.rotation.z = FLEET[0].roll;
    // SIZE IS PART OF PLACEMENT, and it belongs on the same object as the
    // position so an arm can never be moved without being sized. Scaling the
    // root scales the whole chain -- links, joints, sponge -- uniformly, so
    // every proportion robotarm.js derived from the URDF is preserved and only
    // the overall size changes. Nothing inside that file has to know.
    robot.root.scale.setScalar(ARM_SCALE);
    fleet.push({ arm: robot, region: FLEET[0].region });
    for (let i = 1; i < FLEET.length; i++) {
      const f = FLEET[i];
      const a = makeRobotArm(scene, ramp, f.region);
      a.placeNear(f.x, f.y, f.z);
      a.root.rotation.z = f.roll;
      a.root.scale.setScalar(ARM_SCALE);
      fleet.push({ arm: a, region: f.region });
    }
    // ---- WHAT EACH ARM STANDS ON ---------------------------------------
    // NOT A FLOOR PAD. The old fleet gave every arm its own 0.86-wide plinth
    // on the floor, which is what made a cold read call the frame "four
    // industrial arms on separate floor pads": four detached slabs is four
    // objects, and the product is ONE chair.
    //
    // A rig bolted to a wheelchair has a MOUNT -- a short post off the
    // chair's own frame, under each arm, reaching down to the seat rails.
    // Same slate as the arms so the post reads as the bottom of the arm
    // rather than as a thing beside it.
    for (const f of FLEET) {
      const h = Math.max(0.12, f.y - (SEAT_SURFACE_Y - 0.34));
      const post = new THREE.Mesh(
        new THREE.BoxGeometry(0.17, h, 0.17),
        new THREE.MeshToonMaterial({ color: PLINTH_COL, gradientMap: ramp }));
      post.position.set(f.x, f.y - h / 2, f.z);
      post.castShadow = post.receiveShadow = true;
      scene.add(post);
    }
  }

  // ---- THE FRAME THAT MAKES IT ONE MACHINE --------------------------------
  // A cold read of the projector frames: "four industrial arms on separate
  // floor pads" around "a person sitting cross-legged on the floor". Four
  // detached bases and a chair with no visible connection to them is five
  // objects, not a product -- and the product IS the chair.
  //
  // A rail along each side, from the front plinth to the back one and on
  // past the chair, is the connection. It is the same box geometry and the
  // same slate the plinths already use, so nothing is modelled and nothing
  // new is downloaded.
  //
  // LOW AND THIN on purpose. It has to tie the bases together without
  // becoming another loud object in the lower third, which is the mistake
  // the tan plinths were already making.
  // FROM THE ARMS' REAL POSITIONS, not from FLEET[0]. Arm 0 is placed at
  // PLINTH_X/PLINTH_Z (1.65, -0.37) by the block far above; its FLEET entry
  // says z 0.10 and is never used for placement. Building the rails off the
  // declared value put the right-hand rail 0.47 short of its own plinth, and
  // a cold read called that plate "a forgotten fifth base".
  const RAIL = [
    { a: { x: 1.65, z: -0.37 }, b: { x: 1.30, z: -0.95 } },   // right pair
    { a: { x: -1.55, z: 0.10 }, b: { x: -1.20, z: -0.95 } },  // left pair
  ];
  for (const { a, b: bb } of RAIL) {
    const dx = bb.x - a.x, dz = bb.z - a.z;
    const len = Math.hypot(dx, dz) + 0.5;      // overlap both plinths
    const rail = new THREE.Mesh(
      new THREE.BoxGeometry(0.18, 0.11, len),
      new THREE.MeshToonMaterial({ color: FRAME_COL, gradientMap: ramp }));
    rail.position.set((a.x + bb.x) / 2, PLINTH_TOP - 0.02, (a.z + bb.z) / 2);
    // Turn it to actually join the two, rather than assuming they line up.
    rail.rotation.y = Math.atan2(dx, dz);
    rail.castShadow = rail.receiveShadow = true;
    scene.add(rail);
  }

  // And a cross-member under the chair joining the two side rails, so the
  // eye can follow one continuous frame from the left arms, under the
  // person, to the right arms.
  const spine = new THREE.Mesh(
    new THREE.BoxGeometry(3.2, 0.10, 0.18),
    new THREE.MeshToonMaterial({ color: FRAME_COL, gradientMap: ramp }));
  spine.position.set(0.05, PLINTH_TOP - 0.02, -0.95);
  spine.castShadow = spine.receiveShadow = true;
  scene.add(spine);

  // ---- THE PART OF A WHEELCHAIR A SEATED BODY CANNOT HIDE -----------------
  // MEASURED, not guessed. The chair GLB occupies x -0.57..0.68, z -0.66..0.60.
  // The seated character occupies x -0.89..0.80, z -0.62..0.67. The body's
  // silhouette covers the chair almost exactly, which is the whole reason a
  // cold read said "person sitting cross-legged on the floor" -- there is no
  // angle from the wide shot where any of that chair is not behind a person.
  //
  // The rails above join the four ARM BASES to each other behind the chair at
  // z -0.95. They do not touch the chair and the camera cannot see them: a
  // screenshot with them in place still read as a person on the floor.
  //
  // So build the one piece that is in front of the knees and below them, where
  // nothing else is: a footplate on two forward struts. On a real wheelchair
  // that is exactly what sits there, it is the lowest and most forward thing,
  // and it is the reason the eye reads "seat" rather than "floor" -- feet
  // resting above the ground cannot be feet on the ground.
  //
  // SEAT_SURFACE_Y (1.05) is where the seat is; the footplate hangs well below
  // it at 0.72, clear of FLOOR_Y 0.55 so a gap of daylight shows underneath.
  // That gap is the tell. Sitting the plate ON the floor would just read as
  // another floor pad, which is the mistake the tan plinths already made.
  // THE FEET ARE AT z 0 AND -0.24, NOT FORWARD. Measured off the two leg
  // bones rather than assumed: `leg-left` sits at (0.22, 1.05, 0) and
  // `leg-right` at (-0.14, 1.05, -0.24). The first version of this block put
  // the footplate at z 0.78 on the assumption that a seated figure's feet
  // reach forward the way a person in an armchair's do. This character's
  // pose folds them nearly underneath. A screenshot of that version read as
  // a blue folding luggage cart parked in front of a person -- a FOURTH
  // detached object, which is worse than the problem it was added to fix.
  //
  // So the plate goes under the feet where the feet actually are, and it is
  // the chair's blue DARKENED most of the way to the frame slate. Full
  // saturation made it the loudest thing in the lower third, which is the
  // exact mistake the tan plinths made before they were recoloured.
  const CHAIR_FRAME_COL = 0x2b3c6b;   // chair blue, dropped toward the slate
  const footMat = new THREE.MeshToonMaterial(
    { color: CHAIR_FRAME_COL, gradientMap: ramp });

  const footplate = new THREE.Mesh(new THREE.BoxGeometry(0.66, 0.06, 0.40),
    footMat);
  footplate.position.set(0.04, 0.70, 0.02);
  footplate.castShadow = footplate.receiveShadow = true;
  scene.add(footplate);

  // Two short posts from the seat line down to the plate, INSIDE the chair's
  // own x span (-0.57..0.68) so they read as part of it rather than as a
  // frame around it. Near-vertical now: the feet are directly below the
  // seat, so a raked strut would point at nothing.
  for (const sx of [-0.22, 0.30]) {
    const strut = new THREE.Mesh(new THREE.BoxGeometry(0.07, 0.34, 0.07),
      footMat);
    strut.position.set(sx, 0.87, 0.02);
    strut.castShadow = strut.receiveShadow = true;
    scene.add(strut);
  }

  // HANG THE SPOON ON THE FEEDING ARM, now that the arm exists. It is loaded
  // much earlier (beside the bowl, so one failed fetch cannot half-build the
  // feeding mode) and parked in the scene until here.
  //
  // A screenshot caught the version that skipped this: the sponge vanished on
  // entering feed and nothing replaced it, so the arm ended in a bare grip and
  // the swap read as losing a tool rather than changing one. The spoon was
  // sitting at the world origin under the chair.
  //
  // robotarm.js exports the sponge but not its parent, so the sponge's own
  // parent IS the attach point -- which also means the spoon lands exactly
  // where the sponge was, with no second position to keep in step.
  if (spoon && fleet[1] && fleet[1].arm && fleet[1].arm.sponge) {
    const sp = fleet[1].arm.sponge;
    spoon.position.copy(sp.position);
    sp.parent.add(spoon);
  }

  // NO PROPS BESIDE THE CHARACTER. A wheelchair stood here; Tyler asked for it
  // gone. I had added it unasked, reasoning that the caregiving
  // purpose was never visible on screen. Two reasons it was wrong regardless:
  // it puts a disability signifier on a volunteer who never agreed to carry
  // one, and it reads as "this is for wheelchair users" when the demo is a
  // person resting a forearm on a table. The pitch is the presenter's to make
  // in words, not the set's to make about whoever sits down.
  //
  // Do not re-add this or any equivalent prop as scenery. `vendor.sh` still
  // vendors the models; nothing loads them.
} catch (err) {
  console.error('avatar failed to load:', err);
  const g = document.getElementById('gate');
  if (g) {
    g.innerHTML = 'AVATAR FAILED TO LOAD<br>' +
      '<span style="font-size:.45em;line-height:2;display:block">' +
      'run ./vendor.sh &mdash; then reload<br>' +
      'keys 1 2 3 f r still work</span>';
    g.style.color = '#ff6b6b';
  }
  // AND ON A SURFACE THAT SURVIVES THE GATE. #gate is REMOVED, not hidden, by
  // the first keypress (see the keydown handler's gate branch) -- and
  // DEMO-SCRIPT.md's pre-set has Enter already pressed BEFORE the timer starts,
  // so an error written only into #gate is guaranteed to be invisible by the
  // time anyone is watching. MEASURED by blocking **/*.glb in Playwright: the
  // gate carried "AVATAR FAILED TO LOAD / run ./vendor.sh" before Enter, and
  // after Enter the element did not exist at all. The operator was left with a
  // room, a robot, no character and no explanation.
  //
  // The recovery card does not cover this either: its "Browser blank" row is a
  // DIFFERENT failure (a missing vendor lib means the module never evaluates
  // and nothing renders). A missing character GLB boots fine and renders a room.
  //
  // setTimeout(0), NOT a direct call: flashHold() closes over `held`,
  // `heldIsLinkWarning` and `flashTimer`, all `let`s declared BELOW this catch.
  // A direct call hits the temporal dead zone and throws INSIDE this catch,
  // where it is swallowed -- the operator would then see nothing at all.
  // Verified in a browser: immediate call -> "ReferenceError: Cannot access
  // 'held' before initialization"; deferred -> works.
  setTimeout(() => flashHold('NO CHARACTER -- run ./vendor.sh, then reload'), 0);
  // A minimal stand-in so every later reference is safe and the HUD, the
  // counter, the confetti and the keyboard fallbacks all still function.
  // The stub MUST honour the requested `t` — an earlier version returned t:0
  // for every splotch, so popNearest() could never match and pressing 1/2/3
  // silently did nothing. The whole point of this branch is that the manual
  // fallback keeps working, so the stub has to be faithful enough to prove it.
  const stubSplotches = [];
  avatar = {
    root: new THREE.Group(), node: {}, splotches: stubSplotches,
    addSplotch: (part, t) => {
      const rec = { part, t, gone: false, baseScale: 1,
                    sprite: { visible: true, scale: { setScalar() {} },
                              getWorldPosition: v => v.set(0, 1.5, 0) } };
      stubSplotches.push(rec);
      return rec;
    },
    resetSplotches: () => stubSplotches.forEach(r => {
      r.gone = false; r.sprite.visible = true; }),
    update: () => {},
    // SAFE BY CONSTRUCTION, NOT BY FOUR COINCIDENCES. The stub had no setSuds
    // at all, and the blocked-GLB path stayed alive only because all four call
    // sites happen to write `avatar.setSuds?.(...)`. A fifth written without
    // the `?.` throws on the one path nobody watches. P4-SUDS-BUILDUP.md's own
    // verification plan asks for exactly this check, and the stub's recs carry
    // no `foam` and no `holder` -- measured: hasFoam false, hasHolder false.
    // A no-op here matches how `update` is already handled.
    setSuds: () => {},
    // AND THE SAME FOR faceSplotches, for the same reason. It lifts each
    // splotch toward the camera every frame and the render loop guards the
    // call with `?.` -- which is the one coincidence this comment block
    // exists to refuse. The stub's recs carry no `holder`, so a shared
    // implementation would be safe too (the real one skips a rec without
    // one), but a no-op is simpler and matches `update`.
    faceSplotches: () => {},
    // The accessory keys, for the same reason setSuds is here: the `?.`
    // in the keydown handler saves it today, but a future caller who
    // drops the guard throws on the one path nobody watches.
    nextAccessory: () => -1,
    wearAccessory: () => null,
    accessoryCount: 0,
    accessoryNames: [],
  };
}
initShapes();

// MUST match py/scrubbot.py's SPLOTCH_TS -- the socket sends t and the
// browser matches it against these. See the comment there: 0.22/0.78 were
// outside the sponge's reachable travel, so those splotches could never pop.
const SPLOTCH_TS = [0.34, 0.50, 0.66];
const recs = SPLOTCH_TS.map(t => avatar.addSplotch('arm-left', t));
let cleaned = 0;

function popNearest(t) {
  let best = -1, bd = 1e9;
  recs.forEach((r, i) => {
    if (r.gone) return;
    const d = Math.abs(r.t - t);
    if (d < bd) { bd = d; best = i; }
  });
  // TOLERANCE MUST BE LESS THAN HALF THE SPACING. Splotches sit 0.16 apart
  // (0.34/0.50/0.66), so a 0.18 window overlapped its neighbours: once the
  // nearest splotch was gone, a DUPLICATE event for it would find the
  // neighbour 0.16 away, still inside tolerance, and pop the WRONG dirt in
  // front of judges. 0.07 is under half the spacing, so every event can only
  // ever match its own splotch. The sponge is ~40mm on a ~250mm forearm =
  // 0.16 of limb length, so this is still physically generous.
  if (best < 0 || bd > 0.07) return;
  popSplotch(recs[best], camera);
  // THE POP HITS THE CHARACTER. Cartoon recoil, not simulation: the scrubbed
  // arm kicks and the torso takes a share of it (arms are the torso's
  // children, so a torso shove rocks the whole upper body). Without this the
  // splotch just vanishes and the character is a bystander to its own scrub.
  // The manual fallback must LOOK like a scrub too -- 1/2/3 is what the
  // recovery card says to use when Python is dead, and an arm sitting still
  // while splotches vanish gives the trick away.
  fleetPhase('scrub', 0);                 // a manual pop: all four react at once
  fleetStroke(Math.random() < 0.5 ? -1 : 1);
  setTimeout(() => fleetPhase('rest', 0), 900);
  // THE POP RECOIL, raised now that the spring can actually carry it.
  // Measured before touching these: at damp=0.72 an impulse of ANY magnitude
  // produced ZERO direction reversals -- 2.2, 2.5, 4, 6 and 9 all bulged to a
  // peak around frame 9 and decayed without ever crossing back. The limb had
  // no wobble to amplify. At damp=0.93 the same 2.5 impulse wobbles 3 times
  // and is quiet by frame 45, so a bigger shove now reads as comic overshoot
  // rather than as a bigger shove.
  // 9 -> 4.5, CHOSEN FROM SCREENSHOTS, not from the peak numbers. Swept 3.0 /
  // 4.5 / 6.0 / 9.0 at damp=0.93 and looked at each frame 200ms after the pop:
  //   3.0  no visible reaction -- the arm still reads as its resting pose
  //   4.5  a clear kick, body coherent, the shoulder still reads as a shoulder
  //   6.0  the arm is past the hip and starting to look detached
  //   9.0  frankly dislocated: flung up past the head, the other arm across
  //        the shorts at an angle no shoulder makes
  // The peak numbers (x +0.169 / +0.245 / +0.335 / +0.474) are a smooth ramp
  // and say nothing about which one reads as floppy rather than broken. That
  // is the whole reason the acceptance test for this work is a picture.
  avatar.impulse?.('arm-left', (Math.random() - 0.5) * 4.5, 0, -3.5);
  // THE WHOLE BODY REACTS, not just the limb. R5c: "a whole-torso wobble when
  // a splotch pops, not just a limb impulse." The arms are the torso's
  // children, so a torso shove rocks the entire upper body with it.
  avatar.impulse?.('torso', (Math.random() - 0.5) * 0.9, 0, -1.3);
  avatar.squash?.('arm-left', 0.26);
  avatar.squash?.('torso', 0.10);
  cleaned++;
  setClean(Math.round(100 * cleaned / recs.length));
  // SUDS BUILD-UP RIDES THE SAME COUNTER THE HUD DOES. Foam is a LEVEL, not an
  // event: the dead air this addresses is the seconds of rubbing BETWEEN pops,
  // where sudsAt's transients already fire but nothing shows accumulated
  // progress. Driving it off cleaned/recs.length means the foam and the number
  // can never disagree -- and it touches no sensing claim, exactly as sudsAt
  // does not (see juice.js:47).
  avatar.setSuds?.(cleaned / recs.length);
  // THE CHARACTER REACTS TO BEING CLEAN. The Kenney pack ships 32 clips and
  // exactly one (idle) was ever played -- an animation library left in the
  // box. This is the demo's payoff beat: 100%, confetti, fanfare, and now the
  // character actually celebrates instead of standing there.
  if (cleaned >= recs.length) avatar.playOnce?.('emote-yes');
}

function resetAll() {
  // 'r' MUST CLEAR THE PHASE LINE. This never touched cycleLive, which was
  // harmless while the line could only turn on at the first pop -- the
  // server's m.reset always followed and cleared it. The line now turns on at
  // the 's' keypress, so 'r' left the projector reading CYCLE RUNNING with the
  // counter at 0% and nothing happening. That is the script's 1:52 beat.
  //
  // MEASURED before this: 20 dense samples over 3s after 'r', every one
  // cyc='CYCLE RUNNING' live=true pct='0%'. It read as IDLE only on the runs
  // where the socket's m.reset happened to arrive first, which is why a
  // five-rehearsal pass showed it on three runs out of five. The keyboard
  // fallback has no socket at all, so the page cannot wait for that message.
  cycleLive = false;
  setCycle(false);
  cleaned = 0;
  // AND THE CARE COUNT GOES BACK TO ZERO. Every other number on the screen
  // resets here; a care count that survived would be the only readout that
  // describes a different run than the rest of the screen. Rehearse once for
  // 40 seconds, press `r` for the judges' run, and the panel would open at
  // 40s OF CARE with nothing yet having happened.
  //
  // The estop is deliberately NOT this path: `x` does not call resetAll, so
  // an emergency stop keeps the count. That care really was delivered.
  careSeconds = 0;
  feedLive = false;
  if (careEl) { careEl.innerHTML = ''; careEl.classList.remove('on'); }
  coverage?.reset();
  avatar.resetSplotches();
  // resetSplotches() already zeroes each foam sprite; this makes the LEVEL
  // explicit so a future reset path that skips it still lands at 0.
  avatar.setSuds?.(0);
  // The death gag CLAMPS its last frame (a punchline has to stay down), and
  // nothing else releases it -- 'r' left the character face-down at torso
  // deviation 0.2978. 'r' is the documented reset, so it stands them up too.
  avatar.standUp?.();
  resetCounter();
  // AND THE FRAME GOES BACK. `r` is the recovery key; an operator reaching for
  // it has already lost the thread, and leaving them pushed in on a close-up
  // of a finished cycle makes that worse. Fast, because this is a recovery and
  // not a beat -- 0.6s reads as "put it back", not as a move.
  setShot('wide', 0.6);
}

/** Rebuild the splotches at explicit positions (UV mode). */
function placeSplotches(ts) {
  const same = ts.length === recs.length &&
               ts.every((t, i) => Math.abs(t - recs[i].t) < 0.001);
  if (same) return;                       // already there; don't churn
  for (const r of recs) {                 // detach the old sprites
    if (r.holder && r.holder.parent) r.holder.parent.remove(r.holder);
  }
  avatar.splotches.length = 0;
  recs.length = 0;
  for (const t of ts) recs.push(avatar.addSplotch('arm-left', t));
  cleaned = 0;
  resetCounter();
  // The holders were DETACHED and the recs rebuilt above, so these are new foam
  // sprites -- re-assert the level rather than trusting the old one.
  avatar.setSuds?.(0);
  console.log('splotches placed at', ts);
}

// ---- websocket: EVENTS ONLY. Never pose. ---------------------------------
// If this socket dies the cartoon still mirrors the person perfectly — it
// just stops popping splotches, and 1/2/3 is the rehearsed manual fallback.
// A judge cannot tell the difference from ten feet.
let sock = null;                 // set by connect(), used by the 's' key
let cycleLive = false;           // a Python scrub cycle is running RIGHT NOW
let scrubPhase = 1;              // flips per contact -> a back-and-forth stroke
let emoteIdx = -1;               // 'e' cycles the reaction clips
let choreoTimers = [];
let choreoInterval = null;      // module scope so the ESTOP can reach it

/** Cancel the on-screen scrub immediately. THE ESTOP MUST REACH THIS.
 *
 *  The stroke interval used to be a `const iv` trapped inside the closure, so
 *  pressing 'x' stopped the REAL arm and the cartoon kept scrubbing for the
 *  rest of the 9.8s window. The presenter's scripted line at that exact moment
 *  is "that's the emergency stop -- it's bound to a key and a torque limit on
 *  every joint", delivered while the screen behind them shows the arm still
 *  going. It reads as the estop having FAILED, on the one beat the demo most
 *  needs to land -- and it hides a genuinely failed estop, because the operator
 *  cannot tell "confirmed but still animating" from a real runaway.
 */
function stopScrubChoreography() {
  choreoTimers.forEach(clearTimeout);
  choreoTimers = [];
  if (choreoInterval !== null) { clearInterval(choreoInterval); choreoInterval = null; }
  // EVERY PENDING BEAT, NOT JUST THE SCRUB. The feed, vitals and voice modes
  // run on `modeTimers`, a separate list added after this function was
  // written, and nothing but setMode ever cleared it.
  //
  // So `x` during the feeding beat played the thunk, flinched the character,
  // parked all four arms -- and then the pending serve() fired about 1.6s
  // later, set the feeding arm back to 'hover' and lifted the bowl to the
  // person's mouth. After an emergency stop, the machine resumed touching the
  // volunteer.
  //
  // The script offers `x` to a judge as the safety demonstration and the
  // recovery card says "press `x` and the arms stop". Both were false in
  // three of the five modes.
  modeTimers.forEach(clearTimeout);
  modeTimers = [];
  if (modeTick !== null) { clearTimeout(modeTick); modeTick = null; }
  // The props go down with the arms. A bowl left floating at a stopped
  // person's mouth is the same lie in a different form.
  if (bowl) bowl.visible = false;
  if (glass) glass.visible = false;
  fleetPhase('rest');
}

/** APPROACH -> SCRUB -> RETREAT, on the clock, driven by the arm keypress.
 *  Contact events still stroke it (see the ws handler) -- this is the spine
 *  that guarantees the beat happens even when they arrive in one batch. */
function startScrubChoreography() {
  choreoTimers.forEach(clearTimeout);
  choreoTimers = [];
  if (choreoInterval !== null) { clearInterval(choreoInterval); choreoInterval = null; }
  const dur = 8000;                      // config.json scrub_seconds default
  fleetPhase('hover');                   // travel in, deliberate, staggered
  choreoTimers.push(setTimeout(() => fleetPhase('scrub'), 1600));
  // Keep stroking for the length of the scrub so the sponge visibly rubs
  // rather than pressing once and freezing.
  let n = 0;
  choreoInterval = setInterval(() => {
    if (n++ > 26) { clearInterval(choreoInterval); choreoInterval = null; return; }
    scrubPhase = -scrubPhase;
    fleetStroke(scrubPhase);
    avatar?.impulse?.('arm-left', scrubPhase * 1.5, 0, -1.0);
    avatar?.impulse?.('torso', scrubPhase * 0.35, 0, -0.22);
    // THE SCRUB IS AUDIBLE. Rides this tick for the same reason the foam does:
    // it is the spine that fires whether or not contact events arrive, it is
    // capped at 26 ticks, and stopScrubChoreography() -- which the ESTOP calls
    // -- clears it. So the sound inherits the rate limit and the stop path for
    // free. The contact-path stroke in ws.onmessage is deliberately SILENT:
    // those events can arrive batched, and one batch would fire a burst of
    // squishes at once.
    play('rub');
    // FOAM WHERE THE SPONGE ACTUALLY IS. robot.sponge is the mesh the tests
    // read for the contact gap, so it is the authoritative contact point --
    // reading the character's arm instead would put the suds on the limb even
    // when the arm is parked. See sudsAt() for why this rides the stroke tick
    // rather than a contact event.
    //
    // No throttle: this interval is 260ms and capped at 26 ticks, so foam fires
    // at most ~3.8/s for ~6.8s. stopScrubChoreography() already clears this
    // interval and the ESTOP already reaches it, so 'x' stops the foam too.
    // EVERY ARM, NOT JUST ARM 0. This read `robot.sponge` alone, so three of
    // the four scrubbed completely dry -- invisible in a still because arm 0
    // is the one nearest the splotches, and the exact shape of the bug that
    // once left three arms not animating at all.
    //
    // Water on the working arm, foam on all of them: the foam is a level that
    // builds across the body, the spray is where the sponge is biting right
    // now, and firing spray four times over would read as a rainstorm rather
    // than a shower.
    // FOAM ON ALL FOUR, SPRAY ON ONE, AND THE TOTAL STAYS WHERE IT WAS.
    // Four arms of foam at the old per-arm count is 20 particles a tick
    // against the 5 this has always emitted -- four times the density, which
    // is a wall of white rather than suds. sudsAt takes a count now so the
    // arms share the budget instead of each claiming it.
    //
    // The invariant the foam was written under still holds: a splotch pop is
    // 42 particles in one burst, so it stays the loudest single event on
    // screen. Nothing here may compete with the pop.
    const wet = fleet.length ? fleet : (robot ? [{ arm: robot }] : []);
    const per = wet.length > 1 ? 2 : 5;      // 4 x 2 = 8, close to the old 5
    wet.forEach(({ arm }, i) => {
      if (!arm?.sponge) return;
      arm.sponge.getWorldPosition(_sudsV);
      sudsAt(_sudsV, camera, per);
      // Spray from the working arm only. Four sprays reads as a rainstorm,
      // and the point is a sponge biting, not weather.
      if (i === 0) sprayAt(_sudsV, camera);
    });
  }, 260);
  choreoTimers.push(setTimeout(() => {
    if (choreoInterval !== null) { clearInterval(choreoInterval); choreoInterval = null; }
    fleetPhase('rest');
  }, dur + 1800));
}
(function connect() {
  let ws;
  try { ws = new WebSocket(`ws://${location.hostname}:8765`); }
  catch (_) { return setTimeout(connect, 800); }
  ws.onopen  = () => {
    sock = ws;
    // A RESTORED LINK ANSWERS A LINK WARNING. Not an arm warning: "CUT POWER
    // AT THE SUPPLY" stays up through a reconnect, because the arm is still
    // in whatever state it was.
    if (held && heldIsLinkWarning) { held = false; heldIsLinkWarning = false; }
    flag(true);
  };
  ws.onerror = () => { try { ws.close(); } catch (_) {} };
  ws.onclose = () => {
    sock = null;
    // A DEAD SOCKET MEANS NO CYCLE. Without this the flag latches true --
    // it only ever cleared on m.reset, which a killed Python never sends --
    // so every manual 1/2/3 in the crash fallback would warn about a cycle
    // that no longer exists. Measured after SIGKILL: isCycleLive() stayed
    // true. It happened to show no warning only because flag(false) had
    // already written ARM ○ MANUAL into the same element and overwrote it
    // 1.6s later. Relying on that is a coin flip, and the crash fallback is
    // the one path that must never be noisy.
    cycleLive = false;
    setCycle(false);
    flag(false);
    // THE line that saves the demo. Do NOT call location.reload() here —
    // that re-downloads every asset mid-demo.
    setTimeout(connect, 500);
  };
  ws.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch (_) { return; }
    // The server's verdict on the last estop/clear. Never assume it worked.
    if (m.ack) {
      if (m.ack.cmd === 'estop') {
        if (m.ack.ok) flash('ESTOP CONFIRMED');
        else flashHold('ESTOP FAILED — CUT POWER AT THE SUPPLY');
      } else if (m.ack.cmd === 'arm' && m.ack.ok === false) {
        // THE SERVER REFUSED TO ARM. It does this while estopped. The page
        // starts the choreography optimistically on the keypress (the arm must
        // travel in whether or not contact events arrive), so a refusal has to
        // UNDO it -- otherwise the projector shows a full 9.8s cartoon scrub of
        // a volunteer's forearm while the machine is emergency-stopped, and the
        // operator, who is watching the projector, believes the resume worked
        // and stops reaching for shift+C.
        //
        // MEASURED before this: stroke span 0.5479 with the arm latched off.
        stopScrubChoreography();
        // Same reason as the estop key: the line is on from the keypress now,
        // and this branch means the server REFUSED to arm. The comment above
        // says the danger is an operator believing the resume worked; a corner
        // still reading CYCLE RUNNING is exactly that belief.
        cycleLive = false;
        setCycle(false);
        flashHold('STILL STOPPED — press shift+C first');
      } else if (m.ack.cmd === 'clear') {
        if (m.ack.ok) { flash('CLEARED — press s to arm'); play('ding'); }
        else flashHold('CLEAR FAILED — still stopped, press again');
      }
    }
    // A LIVE CYCLE WILL RESET THE COUNTER UNDER YOU. Measured while recording
    // the backup video: arming with 's' AND tapping 1/2/3 makes the count
    // oscillate 1 -> 0 -> 1, because the cycle's RETREAT fires fire_reset().
    // Both paths are correct alone; doing both is the operator error, and an
    // operator under pressure will absolutely do both. Track it so the manual
    // keys can say so instead of silently fighting.
    // THE ARM LINK DIED. Python drops to IDLE and stops advancing the
    // counter; without this the projector showed nothing at all and the
    // operator -- who is watching the projector, not the OpenCV window --
    // had no idea why the demo stopped or that 1/2/3 was now the only way
    // forward. flashHold so the reconnect loop cannot erase it.
    // A FRESH PARTITION EXISTS. The backend ran the SAME solver the bake runs
    // and wrote a new body file; this pulls it in so the overlay shows a
    // partition solved moments ago rather than one recorded weeks ago.
    //
    // THE GEOMETRY DOES NOT TRAVEL ON THIS SOCKET, and must not: the file is
    // about 670 KB and this is a 15Hz fire-and-forget broadcast whose whole
    // contract is that the cartoon keeps working when Python dies. What
    // arrives here is a NOTIFICATION; the body itself comes over HTTP, the
    // same way the bake always has, through the same makeTerritories call.
    //
    // m.solve is STICKY on the server, so this runs on every frame once a
    // solve has landed. Guarding on the filename actually changing is what
    // makes that safe -- without it, every one of the 15 messages a second
    // would start another swap.
    if (m.solve && m.solve.state === 'ready' && m.solve.file
        && liveBody !== m.solve.file) {
      liveBody = m.solve.file;
      // SAY WHICH BODY THIS ACTUALLY IS. The solve runs whether or not the
      // camera could measure the person: a refusal falls back to the
      // population table and still redraws, which looks identical on screen.
      // The backend ships `measured` beside the file for exactly this reason,
      // so a projector cannot claim a measurement that did not happen -- the
      // one claim this whole feature exists to support. Measured on a headless
      // run with no usable pose: state 'ready', measured false, and without
      // this line the screen still read LIVE SCAN.
      //
      // n_dims SHARPENS THE SAME CLAIM. `measured` is a yes/no and the true
      // answer today is "partly" -- three of the nine dimensions the model
      // takes came off depth and six are still the population table. The
      // banner says which, for the reason showBody() spells out at length:
      // "MEASURED THIS PERSON" over a body whose chest is anatomy.ADULT is
      // the overclaim this whole feature exists to remove. Falls back to the
      // old wording when the backend did not send a count, so an older
      // backend against a newer page still reads correctly.
      const nd = m.solve.n_dims;
      flash(!m.solve.measured
              ? `RE-SOLVED (no measurement) — ${m.solve.secs}s`
              : typeof nd === 'number'
                ? `MEASURED THIS PERSON — ${nd} DIMENSIONS, ${m.solve.secs}s`
                : `MEASURED THIS PERSON — ${m.solve.secs}s`);
      // THE DRAWN BODY IS NOW THE SOLVED ONE. showBody() keeps this separate
      // from the measurement itself because they become true at different
      // moments; this is the one place that can say the geometry changed.
      bodySolved = !!m.solve.measured;
      showBody();
      // Only disturb what is on screen if the fresh body is what should be
      // showing. Pressing 'n' to the alternate body and then having a solve
      // land would otherwise yank the alternate away mid-comparison.
      if (!bodyIndex) swapBody();
    }
    if (m.link === 'arm-lost') {
      cycleLive = false;
      setCycle(false);
      flashHold('ARM LINK LOST — check USB, use 1 2 3');
      // A failure the audience can READ on the character, not just in a
      // corner HUD they are not looking at.
      avatar.playOnce?.('emote-no');
    }
    if (m.reset) { cycleLive = false; setCycle(false); resetAll(); }
    // ---- WHAT THE ARMS ARE ACTUALLY DOING -------------------------------
    //
    // Plan §5 3.1/3.2. Until now every arm on screen was posed by INFERENCE:
    // setPhase picks one of three hand-tuned poses and reachTo solves a
    // sponge position. Neither asked the machine. py/scrubbot.py now samples
    // the arm it drives once per broadcast and sends the joint angles, and
    // this is where the drawing stops guessing.
    //
    // THE `src` FIELD IS THE REASON THIS IS NOT JUST A POSE UPDATE. Python
    // says "commanded" or "measured", and today it is always "commanded" --
    // attaching to the stack that owns the encoders is forbidden while it is
    // live on the bus. Those two are the same numbers on screen, so the
    // distinction can only ever come from the wire. armJointsSrc carries it
    // to the HUD; nothing here may infer it from the pose.
    //
    // AN ARM NOT NAMED IN THE PAYLOAD IS LEFT ALONE, and that is the whole
    // "do not invent data" rule in one branch. This process commands ONE arm
    // and says so by sending one key. Mirroring those angles onto the other
    // mount would make the picture symmetrical and the claim false; the
    // unnamed arm keeps its own fallback animation, which is honestly what
    // it has always been.
    if (m.joints && m.joints.arms) {
      const srcWas = armJointsSrc;
      armJointsSrc = typeof m.joints.src === 'string' ? m.joints.src : null;
      // REPAINT THE LINK LINE ONLY WHEN THE SOURCE CHANGES. This branch runs
      // fifteen times a second and flag() writes the DOM; rewriting identical
      // text sixty times a second is the same waste stepPrivacy's own comment
      // rejects. It also must not fire on every frame because flag() would
      // then stomp a flash() toast the operator is mid-read of.
      if (srcWas !== armJointsSrc) flag(!!(sock && sock.readyState === 1));
      // FLEET ORDER IS THE WIRE ORDER, from the same file. web/assets/body.json
      // lists the measured rig and main.js builds `fleet` straight off it, so
      // index i is mount "a<i>" -- the identical mapping py/scrubbot.py's
      // ARM_ID names. Deriving it here rather than storing a name on each
      // fleet entry keeps one definition; if the bake ever reorders, both
      // sides move together because both read that file.
      fleet.forEach(({ arm }, i) => {
        const q = m.joints.arms['a' + i];
        // undefined means "this payload says nothing about this arm", which
        // is NOT the same as null. null is Python explicitly handing the arm
        // back to its own animation, and setJoints treats it as such.
        if (q === undefined) return;
        arm.setJoints?.(q, m.joints.src);
      });
    }

    // THE PERSON, FROM THE OTHER MACHINE (plan §5 3.3). `limbs` carries the
    // six arm joints py/vision.py's limb_event() publishes -- shoulder, elbow
    // and wrist per side, in millimetres, with ABSENT MEANING ABSENT: a joint
    // the detector did not see is not a key, never a last-known position and
    // never a zero.
    //
    // WHY THIS RECORDS RATHER THAN POSES, TODAY. The cartoon is already
    // mirroring a person, from this Mac's own camera through
    // avatar.update(lm) on the render loop. Feeding a second, slower source
    // into the same bones would fight it fifteen times a second and the
    // failure would look like jitter rather than like two sources. The
    // handoff belongs where `lm` is read, and it is only correct once there
    // is depth behind these points: limb_event names its own src
    // "pose_2d_lifted" -- hip-centred, scale-normalised to a generic human,
    // floor-placed by a seated-adult constant. Plan §5 3.3 asks for lift()'s
    // depth-backed world points, and §9 says only the operator can plug the
    // RealSense back into the GB10. So this reads the wire and reports what
    // is on it, and the pose handoff lands the day the source is real.
    if (m.limbs && m.limbs.mm) {
      const src = typeof m.limbs.src === 'string' ? m.limbs.src : null;
      const n = Object.keys(m.limbs.mm).length;
      if (src !== limbsSrc || n !== limbsSeen) {
        limbsSrc = src;
        limbsSeen = n;
      }
      // POSE THE CARTOON FROM THE MEASURED JOINTS, when they were measured.
      //
      // This used to only COUNT them, and the comment said why: the cartoon
      // was already mirroring a person off this machine's own webcam, and a
      // second slower source feeding the same bones would fight it fifteen
      // times a second. That reasoning held while these points were
      // MediaPipe's generic-human estimate -- two guesses fighting is worse
      // than one.
      //
      // It stops holding once they are measured. A depth-backed joint is
      // where the person's elbow IS, in millimetres, and the webcam's is a
      // guess scaled to an average body. When the wire says every joint was
      // measured, that is the better source and it wins outright; when it
      // says "mixed" or "pose_2d_lifted" we leave the webcam alone, because
      // a half-measured skeleton is not an improvement on a whole estimated
      // one.
      // GATE ON THE JOINTS THE POSE USES, NOT ON THE WHOLE PAYLOAD. `src`
      // is "depth_measured" only when EVERY joint in the message was
      // measured, and the message grew hips for the chair -- which depth
      // cannot reach on a seated person, because the seat is in the way. So
      // src became "mixed" on every frame, this line refused every frame,
      // and the character sat in its idle T-pose while all six arm joints
      // were measured perfectly.
      //
      // The six the arms are posed from are the ones that have to be
      // measured. A hip that is still an estimate cannot move an arm.
      const names = Array.isArray(m.limbs.measured_names)
        ? new Set(m.limbs.measured_names) : null;
      const POSE_JOINTS = ['l_shoulder', 'l_elbow', 'l_wrist',
                           'r_shoulder', 'r_elbow', 'r_wrist'];
      const posable = src === 'depth_measured'
        || (names && POSE_JOINTS.every((k) => names.has(k)));
      limbsWorld = posable ? m.limbs.mm : null;
    } else if (limbsSrc !== null) {
      limbsWorld = null;
      // Absent means the detector found nobody. Clear, for the same reason
      // scrubbot publishes this field non-sticky: a held-over pose is a
      // person standing where they are not.
      limbsSrc = null;
      limbsSeen = 0;
    }

    // HOW BIG THE PERSON ACTUALLY IS. `m.body` carries the measurements
    // scrub3d/live/live_body.py takes off the depth camera -- nine dimensions
    // in the exact key names anatomy.anatomical_body(measurements=) accepts,
    // each with its OWN confidence, plus how many frames back them.
    //
    // STICKY, AND DELIBERATELY UNLIKE m.limbs ABOVE. A body's proportions do
    // not change when the person leans out of frame, so the last measurement
    // of THIS person stays true until somebody else sits down. Clearing it on
    // a frame with nobody in it -- which is what the limbs block three lines
    // up correctly does for POSE -- would make the readout flicker between
    // measured and generic while a person simply turned their head.
    //
    // WHY THIS ONLY RECORDS, AND DOES NOT REBUILD THE BODY ITSELF. The
    // drawn body is rebuilt on the BACKEND, by the same solver the bake runs
    // (py/scrubbot.py's _solve_live -> tools/export_body.export_one), and it
    // arrives here as a filename on m.solve, handled above. The alternative
    // was rebuilding the mesh in the browser from these nine numbers, and
    // that would be a second implementation of scrub3d/anatomy.py written in
    // a language that cannot import it -- two body builders that must agree
    // forever, drifting the first time either is touched. The partition the
    // arms plan against comes out of the solver too, so a browser-side mesh
    // would also be a body the arms had never planned against.
    //
    // SO WHAT IS THIS FOR: saying what is on screen. The count of dimensions
    // that were genuinely measured is the thing the banner needs and the
    // filename cannot carry.
    if (m.body && m.body.mm && m.body.confidence) {
      // ONLY WHAT WAS MEASURED COUNTS. Every key in `mm` has a value whether
      // or not anyone measured it -- scrub3d/live/scene_out.py says so in its
      // own header: a dimension with no samples is reported as the typical-
      // adult prior WITH confidence 0.0. Counting the keys would therefore
      // report nine measured dimensions for a body where three were measured
      // and six are anatomy.ADULT echoed back.
      //
      // 0.5 is the same threshold the backend solves on (scrubbot's
      // _MEASURE_MIN_CONF); both sides have to agree or the banner describes
      // a different body than the one the solver built.
      let n = 0;
      for (const k of Object.keys(m.body.mm)) {
        if ((m.body.confidence[k] ?? 0) >= 0.5) n++;
      }
      const total = Object.keys(m.body.mm).length;
      if (n !== bodyMeasuredDims || total !== bodyTotalDims
          || m.body.n_frames !== bodyFrames) {
        bodyMeasuredDims = n;
        bodyTotalDims = total;
        bodyFrames = m.body.n_frames | 0;
        showBody();
      }
    }

    if (!(m.joints && m.joints.arms) && armJointsSrc !== null) {
      // THE FEED STOPPED CARRYING JOINTS. Release every arm back to the
      // inferred pose rather than leaving it frozen at the last angle it was
      // told -- a stopped feed must read as the cartoon carrying on, which is
      // the contract scrubbot.py:78 states ("if this socket dies the cartoon
      // still mirrors the person"). Guarded on armJointsSrc so this costs
      // nothing on the fifteen messages a second that never had the key.
      armJointsSrc = null;
      fleet.forEach(({ arm }) => arm.setJoints?.(null));
      flag(!!(sock && sock.readyState === 1));
    }
    // BRACED DELIBERATELY: this was a brace-less single-line `if`. Appending
    // setCycle(true) after it would have run on EVERY message and pinned the
    // line to CYCLE RUNNING forever -- the exact lie this feature rejects.
    if (m.scrub || (Array.isArray(m.pops) && m.pops.length)) {
      cycleLive = true;
      setCycle(true);
    }
    // A KNOWN PHASE IS ITSELF PROOF A CYCLE IS LIVE. First version gated the
    // render on `cycleLive`, which only turns on at `m.scrub` or the first pop
    // -- by then the FSM is already past APPROACH. Measured: the label showed
    // RETURNING and nothing else for a whole cycle. That is the same defect the
    // comment on setCycle describes (IDLE while the arm is visibly scrubbing),
    // reintroduced one layer up.
    //
    // This is NOT the browser guessing: APPROACH/SCRUB/RETREAT come from
    // Python's own state machine, so treating them as liveness is a report, not
    // a timer. "IDLE" deliberately does not turn the line on, and every OFF
    // path (ws.onclose, arm-lost, m.reset, 'x', the refusal to arm) is
    // untouched -- kill Python mid-cycle and the line still drops to IDLE.
    if (typeof m.phase === 'string' && m.phase !== lastPhase) {
      lastPhase = m.phase;
      if (PHASE_LABEL[m.phase]) { cycleLive = true; setCycle(true); }
      else if (cycleLive) setCycle(true);
    }
    // A REPORTED IDLE CONTRADICTS A LIVE CYCLE, on EVERY message rather than
    // only on a change.
    //
    // The change guard above cannot carry this. `phase` starts as "IDLE" in
    // scrubbot's own EVENT dict, so the browser records lastPhase = 'IDLE' on
    // the very first message -- before `s` is ever pressed -- and the guard
    // never fires again. Measured on the replay fallback with the fix inside
    // the guard: CYCLE RUNNING for the full ten seconds, unchanged.
    //
    // Replay never sends a phase, a pop or a reset, so nothing else ever
    // cleared the flag that the `s` keypress sets. The corner read CYCLE
    // RUNNING, green, counter at 0%, for the whole demo -- on the fallback
    // the recovery card sends you to when the camera is dead.
    //
    // The server saying IDLE while this page claims a live cycle is a
    // contradiction whether or not the phase just changed, and the server is
    // the one that knows.
    if (m.phase === 'IDLE' && cycleLive) { cycleLive = false; setCycle(false); }
    // UV mode: move the splotches to where the tracer ACTUALLY is. Without
    // this the page keeps them at the fixed SPLOTCH_TS and matches within
    // 0.07, so tracer detected at e.g. 0.42 fired an event the page silently
    // dropped -- the counter climbed while the splotch stayed on screen.
    if (Array.isArray(m.place) && m.place.length) placeSplotches(m.place);
    // Prefer the QUEUE. The old single `scrub` flag was consumed by the 15Hz
    // pump, so three splotches popping together delivered ONE event and the
    // finale landed as 33% with two splotches still on the arm (measured).
    // THE SPONGE IS TOUCHING THE ARM RIGHT NOW. A scrub event with contact
    // means the real arm is oscillating against the real forearm, so the
    // cartoon's arm should be visibly shaken by it -- the rhythmic wobble is
    // what makes the scrub read as HAPPENING TO someone rather than as two
    // unrelated things on one screen.
    if (m.scrub && m.contact) {
      // A STROKE, NOT A VIBRATION. A uniform shove every event reads as the
      // arm buzzing; a real sponge goes back and forth, so the sideways
      // component ALTERNATES and the into-the-limb component stays constant.
      // That is what makes it look like something is being rubbed rather than
      // something is malfunctioning.
      scrubPhase = -scrubPhase;
      // 2.2 -> 3.6, and the cost is DRIFT, not rhythm. Measured at the shipped
      // 260ms tick with damp=0.93, counting crossings of the arm's own moving
      // baseline (crossings of ZERO is the wrong reference -- the arm rests at
      // z~0.137, and counting those gave an identical "1" for six different
      // configs, which is what exposed the broken instrument):
      //
      //   mag   z span   baseline drift   crossings/sec
      //   1.5    0.083        +0.049          5.5
      //   2.2    0.125        +0.013          5.5   <- what shipped
      //   3.6    0.209        -0.058          5.5   <- this
      //   4.5    0.260        -0.103          5.5
      //
      // The rub rate is FLAT across the range: amplitude does not buy or cost
      // oscillation here. It buys visible span (0.125 -> 0.209, a 67% bigger
      // stroke from ten feet) and costs a small one-way lean. 3.6 takes that
      // trade; beyond ~4.5 the lean starts to read as the arm being pushed
      // aside rather than rubbed.
      //
      // A FASTER TICK IS THE THING THAT DOES NOT WORK, and I had it backwards
      // before measuring: 10 frames -> 3 crossings, 8 -> 1, 6 -> 1, because
      // impulses land before the previous decayed and accumulate into a
      // one-way push (baseline drifts to -0.38). Leave the 260ms tick alone.
      avatar.impulse?.('arm-left', scrubPhase * 3.6, 0, -2.2);
      // The torso takes a smaller share of the same stroke, so the whole
      // upper body rocks with the rhythm instead of only the forearm.
      avatar.impulse?.('torso', scrubPhase * 0.85, 0, -0.5);
    }
    // Drop the tail of a cycle the operator walked away from by swapping the
    // body. Everything else on the message still applies -- the phase line,
    // the governor and the link state are all about the machine, not about
    // which person is on screen.
    if (!popsStale) {
      if (Array.isArray(m.pops) && m.pops.length) {
        for (const p of m.pops) popNearest(p.t);
      } else if (m.scrub) {
        popNearest(m.t);               // fallback for an older server
      }
    }
  };
})();
// Brief on-screen confirmation, so the operator knows a keypress landed
// without looking away from the projector.
let flashTimer = null;

/** A message that STAYS until something else replaces it. For failures the
 *  operator must act on -- a 1.6s toast is not enough for an estop. */
// A HELD MESSAGE MUST SURVIVE THE RECONNECT LOOP. flashHold() cleared the
// flash timer but nothing stopped flag() -- called from ws.onopen, ws.onclose
// and the 500ms reconnect retry -- from overwriting the same element.
// MEASURED: "ESTOP FAILED — CUT POWER AT THE SUPPLY" was gone in under 150ms,
// replaced by ARM ○ MANUAL. That is the single most safety-critical string in
// the UI, erased before anyone could read it, at the exact moment the arm did
// NOT stop. Only a deliberate operator action clears it now.
let held = false;
// Is the held banner ABOUT the link being down? Those are answered by the link
// coming back; banners about the ARM (cut power, estop failed) are not, and
// must survive a reconnect.
//
// WITHOUT THIS SPLIT the latch froze the HUD permanently: press 'x' with
// Python not running, get "NO LINK — HIT SPACE IN THE PYTHON WINDOW", and
// only flash() releases it -- but flash() fires on a server ack, which cannot
// arrive while the socket is down. Measured: Python restarted, socket
// reconnected, HUD still read NO LINK with held=true. The operator's only
// link indicator, dead for the rest of the demo, because of the fix that
// stopped the CUT POWER banner being erased.
let heldIsLinkWarning = false;

/** The cycle line. Driven ONLY from cycleLive's assignment sites, so there
 *  is no second source of truth and no timer. Those sites are: the 's' keypress
 *  and the first pop turn it ON; ws.onclose, the arm-lost gate, the server's
 *  refusal to arm, the 'x' keypress and `m.reset` turn it OFF. It used to turn
 *  on ONLY at the first pop, which meant the corner read IDLE while the arm was
 *  visibly scrubbing and then flipped to CYCLE RUNNING at the same instant the
 *  counter hit 100%. Python HAS a real APPROACH/SCRUB/
 *  RETREAT state machine, but that state never crosses the wire (EVENT carries
 *  no phase key), so a HUD reading "SCRUBBING" would be reporting THIS page's
 *  1600ms setPhase timer -- a status assertion about a machine the browser
 *  cannot see. cycleLive is what the browser genuinely knows, and ws.onclose
 *  clears it, so this degrades to IDLE on its own when Python dies. That is the
 *  acceptance test: kill Python mid-cycle and the line must drop to IDLE. */
/** Python's real FSM phase, when it reaches us. `EVENT["phase"]` is published
 *  once per FSM frame (scrubbot.py, just above the state dispatch), so this is
 *  a REPORT, not the 1600ms setPhase timer the comment above rejects.
 *
 *  Refinement only: the label falls back to CYCLE RUNNING the moment the phase
 *  is missing, unknown, or the socket is gone. An older server that does not
 *  send the key, or a page that loses Python mid-cycle, still reads correctly.
 *  IDLE is deliberately absent -- when the FSM is idle, `cycleLive` is false
 *  and the line already says IDLE. */
const PHASE_LABEL = { APPROACH: 'APPROACHING', SCRUB: 'SCRUBBING',
                      RETREAT: 'RETURNING' };
let lastPhase = null;          // only ever read while cycleLive is true

function setCycle(live) {
  const el = document.getElementById('cycle');
  if (!el) return;
  // A phase is shown ONLY while the browser independently believes a cycle is
  // live. Without that guard a stale phase from a dead server would sit on the
  // screen claiming the arm is scrubbing.
  const refined = live ? PHASE_LABEL[lastPhase] : null;
  el.textContent = live ? (refined || 'CYCLE RUNNING') : 'IDLE';
  el.className = live ? 'live' : '';
}

function flashHold(msg, aboutLink) {
  const el = document.getElementById('link');
  if (!el) return;
  clearTimeout(flashTimer);
  held = true;
  heldIsLinkWarning = !!aboutLink;
  el.textContent = msg;
  el.className = 'warn';
}

/** Release a held banner. Call ONLY from a real operator action. */
function releaseHold() { held = false; }

function flash(msg) {
  const el = document.getElementById('link');
  if (!el) return;
  held = false;              // a new confirmed action supersedes the banner
  el.textContent = msg;
  el.className = 'ok';
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => flag(!!(sock && sock.readyState === 1)), 1600);
}

function flag(up) {
  // Never step on a held warning -- see flashHold().
  if (held) return;
  const el = document.getElementById('link');
  // SAY WHETHER THE POSE ON SCREEN IS A MEASUREMENT, plan §5 3.1's "critical
  // honesty requirement". A linked socket used to mean one thing; now it can
  // mean two, and they look identical: an arm drawn from angles we COMMANDED
  // and an arm drawn from angles an encoder MEASURED. Nobody can tell those
  // apart by looking, which is exactly why the line has to say it.
  //
  // "CMD" is not shorthand for a caveat, it is the state of the machine
  // today: THE ARM HAS NEVER BEEN OBSERVED MOVING, and attaching to the
  // stack that owns the encoders is forbidden while it is live on the bus.
  // The day measured angles arrive, Python sends src 'measured' and this
  // reads MEAS without another edit here.
  //
  // WITH NO JOINTS ON THE WIRE THE STRING IS BYTE-FOR-BYTE WHAT IT ALWAYS
  // WAS. armJointsSrc is null until a payload carries one, so the page with
  // no backend still reads exactly 'ARM ○ MANUAL' -- the suffix is the only
  // new thing and it cannot appear without a live feed that sent it.
  const tag = up && armJointsSrc
    ? (armJointsSrc === 'measured' ? ' · MEAS' : ' · CMD')
    : '';
  el.textContent = (up ? 'ARM ● LINKED' : 'ARM ○ MANUAL') + tag;
  el.className = up ? 'ok' : 'warn';
}

// ---- browser pose --------------------------------------------------------
const video = document.getElementById('cam');
let landmarker = null;

async function startPose() {
  // getUserMedia needs a SECURE CONTEXT. http://localhost and http://127.0.0.1
  // both report isSecureContext:true; a LAN IP does NOT. The browser must run
  // on the same Mac as the camera.
  try {
    video.srcObject = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480 } });
    await video.play();
  } catch (e) {
    console.error('camera denied/missing — avatar will idle:', e);
    return;                            // page still runs; idle clip plays
  }

  const fileset = await FilesetResolver.forVisionTasks('./vendor/wasm');
  const opts = (delegate) => ({
    baseOptions: { modelAssetPath: './models/pose_landmarker_lite.task', delegate },
    runningMode: 'VIDEO',     // REQUIRED for detectForVideo; default is IMAGE
    numPoses: 1,
  });
  try   { landmarker = await PoseLandmarker.createFromOptions(fileset, opts('GPU')); }
  catch { landmarker = await PoseLandmarker.createFromOptions(fileset, opts('CPU')); }
  console.log('browser pose up');
}

let lastVideoTime = -1;
const clock = new THREE.Clock();
// A SINGLE THROW INSIDE setAnimationLoop KILLS IT PERMANENTLY. three.js does
// not re-arm the callback after an exception, so one bad frame -- a malformed
// landmark, a WebGL context hiccup, a transient decode error -- freezes the
// projector mid-demo with a static image and no visible error. Catching per
// frame costs nothing and turns a frozen demo into a logged hiccup.
// How many camera frames the pose detector has consumed. The privacy line
// reads it, and it is incremented in exactly one place: the branch that
// fires when video.currentTime actually advances.
let framesSeen = 0;
let framesShown = -1;          // what the line last rendered, to skip no-op writes
// Is a person in frame right now? Set from the detector's own result, so
// it cannot claim a subject the tracker does not have.
let subjectSeen = false;
let subjectShown = null;      // what the line last said, to skip no-op writes
let frameErrors = 0;
renderer.setAnimationLoop(() => {
  try {
    const dt = clock.getDelta(), t = clock.getElapsedTime();
    if (landmarker && video.currentTime !== lastVideoTime) {
      lastVideoTime = video.currentTime;
      // COUNT THE FRAME. This branch is the one place a genuinely new camera
      // frame is taken, so the number is real rather than a timer dressed up
      // as one. See stepPrivacy for why it is on screen at all.
      framesSeen += 1;
      // The SYNCHRONOUS overload returns the result directly and fits rAF. The
      // callback form exists only for segmentation-mask lifetime, and
      // outputSegmentationMasks defaults to false, so there are no masks here.
      const res = landmarker.detectForVideo(video, performance.now());
      const lm = res.worldLandmarks?.[0] ?? null;
      // WHETHER ANYONE IS IN FRAME, from the detector's own answer. The one
      // beat in the run of show with no keypress is 1:02, where the presenter
      // steps out and the volunteer steps in -- and for a second or two the
      // tracker has nobody. The avatar just held its last pose, silently, on a
      // static screen, right after the presenter has told the judges to watch
      // the cartoon mirror the volunteer. A freeze with no explanation reads
      // as the tracking being broken; naming it reads as the system knowing
      // what it is doing.
      // THE MEASURED SKELETON WINS OVER THIS MACHINE'S WEBCAM. `lm` is
      // MediaPipe's world landmarks from the camera attached here: real
      // direction, but a distance scaled to an average body. `limbsWorld`
      // is millimetres measured by the depth camera watching the person in
      // the actual chair. When the second exists it is simply the better
      // answer to the same question, and mixing them would produce a
      // skeleton that is neither.
      //
      // subjectSeen stays tied to the LOCAL detector on purpose: it drives
      // the "nobody in frame" line about the camera pointed at this room,
      // and a person measured on the other machine does not make somebody
      // appear in front of this one.
      const measuredLm = limbsWorld ? limbsToLandmarks(limbsWorld) : null;
      subjectSeen = !!lm;
      avatar.update(measuredLm || lm, dt, t);
      stepArms(dt);
    } else {
      // NO LOCAL CAMERA, BUT POSSIBLY A MEASURED PERSON. This branch runs
      // when this machine has no webcam or was denied one -- which is the
      // normal case for a projector fed by the arm computer. The measured
      // skeleton is not affected by any of that, so it still poses the
      // cartoon; null here would idle a character the depth camera can see
      // perfectly well.
      avatar.update(limbsWorld ? limbsToLandmarks(limbsWorld) : null, dt, t);
      stepArms(dt);
    }
    // THE SCAN FOLLOWS THE PERSON. The brainstorm's stated innovation is that
    // the arms adapt when someone moves rather than replaying a path; this is
    // that claim on screen. Only while the overlay is up, so it costs nothing
    // the rest of the time.
    if (territories && territoriesOn) territories.follow(avatar?.node);
    // LIFT THE DIRT TOWARD THE CAMERA, after the pose and the mixer have
    // settled this frame's limb angles and before anything draws. The
    // splotches sit on the limb's axis and are pushed out along the camera
    // vector here; a fixed offset baked into the holder rotates with the bone
    // and ends up inside the arm or out on the torso. See faceSplotches().
    avatar?.faceSplotches?.(camera);
    effect.render(scene, camera);  // NOT renderer.render — loses the outlines
  } catch (err) {
    // Log the first few, then go quiet: a per-frame throw at 60fps would
    // otherwise flood the console and slow the very loop we are protecting.
    if (++frameErrors <= 5) console.error('frame error (loop survives):', err);
    if (frameErrors === 6) console.error('further frame errors suppressed');
  }
});

// ---- keyboard: every manual fallback the demo needs ----------------------
addEventListener('keydown', async (e) => {
  // THE GATE MUST ACTUALLY GATE. It says "PRESS ANY KEY TO START", but only
  // Enter/Space dismissed it -- every other key fell straight through to the
  // fallbacks below, so an operator tapping '1' before the demo popped a
  // splotch and drove the counter to 33% while the gate was still up, with
  // audio still locked. Any key now dismisses it and nothing else fires on
  // that keypress.
  const gate = document.getElementById('gate');
  if (gate) {
    unlockAudio();                              // MUST be inside a gesture
    gate.remove();
    startPose();
    return;
  }
  if (e.key === 'Enter' || e.key === ' ') return;   // gate already handled
  // ARM one scrub cycle FROM THE PROJECTOR. The Python-side 's' lives in the
  // OpenCV debug window, which is behind Chrome in kiosk mode on stage -- the
  // operator would have to alt-tab to a hidden window to start the demo.
  if (e.key === 's') {
    // Arming is the operator saying "start a cycle on THIS person", so the
    // previous cycle's pops stop being something to ignore.
    popsStale = false;
    if (sock && sock.readyState === 1) {
      sock.send(JSON.stringify({cmd: 'arm'}));
      flash('ARMED');
      play('ding');
      // ...but the server may REFUSE (it does, while estopped). The ack
      // handler cancels this if so. See 'arm' in ws.onmessage.
      // CHOREOGRAPH THE ARM FROM THE KEYPRESS, not from contact events.
      // Measured: a whole CAM=fake cycle delivers ONE socket message with all
      // three pops batched into the end-of-scrub finale -- the synthetic limb
      // never sweeps the sponge across each position, so there is no stream of
      // contacts to stroke against and the arm sat at rest through the entire
      // demo. Real tracking gives a steadier stream, but the projector must
      // not DEPEND on that: the operator pressed 's', so a cycle is starting,
      // and the arm should travel in whether or not events arrive.
      // PUSH IN FOR THE SCRUB. This is the beat the script tells the
      // presenter to say "watch" and then go quiet, which only works if the
      // frame gives them something to be quiet about. Slower than the other
      // moves: 2.2s, so the push reads as deliberate rather than as a cut.
      setShot('scrub', 2.2);
      startScrubChoreography();
      // AND SAY SO ON SCREEN. The phase line only ever flipped to CYCLE
      // RUNNING on `m.scrub || m.pops.length`, and the server sets `scrub`
      // ONLY inside the pop path -- so the line could not turn on until the
      // FIRST SPLOTCH POPPED. Watched as a judge with screenshots every 2s:
      // the arm is at the forearm with the sponge on the dirt and suds
      // puffing, and the corner still reads IDLE, for ten seconds; the line
      // then flips to CYCLE RUNNING at the same instant the counter hits 100%
      // and the confetti fires, which is the one moment the cycle is over.
      //
      // The operator pressed 's' and the choreography is running, so a cycle
      // IS live. Every clear path still wins: socket close, `arm-lost` and
      // `m.reset` all call setCycle(false), so the line still degrades to IDLE
      // when Python dies -- the acceptance test in setCycle's own comment.
      // This is not the "pin it on every message" trap that comment warns
      // about; it fires once, on the keypress.
      cycleLive = true;
      setCycle(true);
    } else {
      flash('NO LINK — press s in the python window');
    }
  }
  // Emergency stop from the projector too. SPACE dismisses the gate, so this
  // is deliberately a different key from the Python side's spacebar.
  if (e.key === 'x' || e.key === 'X') {
    // NEVER SILENT. This is the control an operator reaches for when
    // something is already going wrong; with the socket down the old version
    // did nothing at all and said nothing at all, so the operator would keep
    // pressing a dead key while the arm moved. Tell them where the working
    // stop is instead.
    if (sock && sock.readyState === 1) {
      sock.send(JSON.stringify({cmd: 'estop'}));
      // "…" until the server says what actually happened. A green
      // confirmation the instant the key is pressed made a FAILED estop --
      // the case where the operator must cut power -- look identical to a
      // successful one.
      // STOP THE CARTOON TOO, on the same keypress. Not on the server's ack:
      // the ack may never arrive (that is the NO LINK case), and an operator
      // who pressed the stop must never watch the screen keep scrubbing while
      // the page waits for a reply.
      stopScrubChoreography();
      // AND DROP THE PHASE LINE. The line now turns on at the 's' keypress
      // rather than at the first pop, so without this an estop pressed right
      // after arming leaves the corner reading CYCLE RUNNING while the cartoon
      // is stopped and the arm is latched off. That is a worse lie than the
      // one the keypress change fixed, and it lands on the safety beat.
      cycleLive = false;
      setCycle(false);
      flashHold('ESTOP SENT…');
      // SOUNDS ON SEND, NOT ON THE ACK. The ack may never arrive -- that is
      // the NO LINK case -- and an operator who pressed the stop must hear it
      // land immediately. 'ESTOP CONFIRMED' stays silent so the normal path
      // does not thunk twice.
      play('thunk');
      // THE CHARACTER REACTS TO THE STOP. The recovery card's line is that a
      // visible safety cutout is a FEATURE -- "that's the emergency stop, it's
      // bound to a key and a torque limit on every joint" -- and that reads far
      // better when the cartoon flinches than when it stands there smiling.
      // 'emote-no' is the pack's own shake-head clip.
      //
      // A BIGGER, SLOWER REACTION (ROADMAP R5c). The estop is the beat where
      // the presenter says "that's the emergency stop, it's bound to a key and
      // a torque limit on every joint", and a character that shrugs politely
      // undersells it. A longer fade makes the clip arrive as a flinch rather
      // than a twitch, and a shove into the springs -- which now actually
      // wobble at damp=0.93 -- lets the whole body recoil with it.
      avatar.playOnce?.('emote-no', 0.35);
      avatar.impulse?.('torso', 0, 0, -2.6);
      avatar.impulse?.('arm-left', -1.8, 0, -1.2);
      avatar.impulse?.('arm-right', 1.8, 0, -1.2);
      avatar.squash?.('torso', 0.16);
    } else {
      flashHold('NO LINK — HIT SPACE IN THE PYTHON WINDOW / CUT POWER', true);
    }
  }
  // CLEAR an emergency stop from the projector. The torque watchdog fires on
  // its own, so a cutout mid-demo used to leave the operator unable to recover
  // from the window they were looking at. Recovery lives where the trigger
  // lives. Deliberately not 'r' -- that resets the splotches.
  // MATCH ON e.code, NOT e.key. A REAL shift+C delivers key='C' (uppercase),
  // so `e.key === 'c' && e.shiftKey` never fires on a physical keyboard --
  // the clear key would have been DEAD ON STAGE. Playwright's synthetic
  // Shift+c happens to deliver key='c', so the test passed while the real
  // thing did not work. e.code is layout- and modifier-independent.
  if (e.code === 'KeyC' && (e.shiftKey || e.metaKey || e.ctrlKey)) {
    if (sock && sock.readyState === 1) {
      sock.send(JSON.stringify({cmd: 'clear'}));
      flashHold('CLEARING…');
    } else {
      flashHold('NO LINK — press r in the python window', true);
    }
    return;
  }
  if (e.key >= '1' && e.key <= '3') {
    // Still pops -- 1/2/3 is the crash fallback and must NEVER be blocked.
    // But say why the counter is about to jump backwards.
    // NOT "CYCLE RUNNING - it will reset this". The phase line top-left now
    // reads CYCLE RUNNING from the 's' keypress, so that wording put the same
    // two words in both corners at once during a rescue -- and the half that
    // matters, the warning that the cycle's RETREAT will undo this pop, is the
    // half a reader skips after recognising the prefix. Screenshotted at the
    // 1:22 rescue beat: top-left "CYCLE RUNNING", top-right "CYCLE RUNNING -
    // it will reset this".
    if (cycleLive) flash('THIS CYCLE WILL RESET IT');
    // POP THE SPLOTCH THAT IS ACTUALLY ON SCREEN, not the hardcoded position.
    // UV mode moves the splotches to where the tracer really is (0.11/0.25/
    // 0.75 in a measured run), and popNearest() rejects anything further than
    // 0.07 -- so 1/2/3 matched NOTHING and popped 0 of 3 with the counter
    // stuck at 0%. That is the rehearsed manual fallback, dead in exactly the
    // mode you switch to when a judge asks "is the dirt detection real?".
    // Index into recs so the keys follow the splotches wherever they move.
    const r = recs[+e.key - 1];
    if (r) popNearest(r.t);
  }
  // ---- 'k' : SKIN TONE  /  'o' : OUTFIT ----------------------------------
  // Tyler: "change the outfits, skin colors, etc to make it feel much more
  // like a polished video game." It is also the privacy pitch working harder:
  // a volunteer who sees their own skin tone gets "that's me" rather than
  // "that's a generic guy", which is the claim the whole demo rests on.
  //
  // UNLIKE 'v', THESE DO NOT RELOAD. Swapping the character reloads the page
  // because springs, splotches and the arm placement are solved per-body; a
  // recolour repaints one texture in place, so it is instant and safe to press
  // mid-demo. Reloading here would throw away the counter and the cycle.
  // e.code, not e.key, for the same reason as the other operator keys: e.key
  // is uppercase with caps lock on and the binding would silently die.
  if (e.code === 'KeyK' && !e.metaKey && !e.ctrlKey) {
    const i = avatar?.nextSkin?.();
    if (typeof i === 'number' && i >= 0) {
      flash(`SKIN ${i + 1}/${avatar.skinCount}`);
      play('click');
    }
  }
  if (e.code === 'KeyO' && !e.metaKey && !e.ctrlKey) {
    const i = avatar?.nextOutfit?.();
    if (typeof i === 'number' && i >= 0) {
      flash(`OUTFIT ${i + 1}/${avatar.outfitCount}`);
      play('click');
    }
  }
  // GLASSES, A MASK, A HEARING AID, A CANE, A CRUTCH. Eight props from the same
  // pack as the twelve characters, vendored long ago and never once loaded.
  //
  // They belong beside the skin and outfit keys because they are the same
  // argument: the pitch is that this machine gives dignity back to someone who
  // needs help bathing, and a volunteer who sees a cartoon wearing their own
  // glasses reads it as themselves rather than as a generic figure. It also
  // costs nothing -- the files were already on disk.
  //
  // Cycles through nothing -> each prop -> nothing, so a press too many takes
  // the props OFF rather than getting stuck on the last one.
  if (e.code === 'KeyJ' && !e.metaKey && !e.ctrlKey) {
    const i = avatar?.nextAccessory?.();
    if (typeof i === 'number') {
      flash(i < 0 ? 'NO ACCESSORY'
                  : `${avatar.accessoryNames[i].replace(/^aid[-_]/, '').toUpperCase()}`
                    + ` ${i + 1}/${avatar.accessoryCount}`);
      play('click');
    }
  }

  // THE FOUR CAPABILITIES, on the number row above the splotch keys. Shower
  // is what the cycle already does; the other three change what the machine
  // is doing and say so on screen. An operator can walk a judge through the
  // whole product without touching anything but these four keys.
  // THE MEASURED BODY. 'b' shows what the backend actually computed: 1792
  // surface patches coloured by which arm owns each one. This is the single
  // most technically impressive thing on screen and it was invisible.
  // VOICE CONTROL. 'v' toggles listening. Say "wash my arm", "I'm hungry",
  // "check my heart rate" or "stop" and the chair answers out loud and
  // switches mode.
  // 'm' FOR MICROPHONE, NOT 'v'. This was bound to `v` and silently killed
  // the character swap: `v` has cycled the 12 characters since long before
  // voice existed, my handler returned first, and the swap became
  // unreachable. Character choice is one of Tyler's three standing rules
  // ("make sure you can change the outfits, skin colors"), so breaking it for
  // a key that was never mine to take is the wrong trade.
  //
  // Caught by the docs guard, which went red on "every card key is in the
  // README table" the moment the two disagreed.
  if (e.key === 'm' || e.key === 'M') {
    if (!voice) {
      voice = makeVoice({
        // "stop" goes through the SAME path as the x key rather than a copy
        // of it. The estop is the one control that must not have two
        // implementations that can drift apart -- the keyboard version already
        // handles a dead socket, tells the operator where the working stop is,
        // and halts the cartoon on the keypress rather than on the server ack.
        onIntent: (m, heardText) => {
          // ASK FOR A SPOT, AND THE ARM THAT OWNS IT ANSWERS.
          //
          // BRAINSTORM-2 line 92: "User can indicate an area to clean more,
          // and the arm focuses there." The demo has always shown the machine
          // deciding and the person receiving; this is the person directing.
          //
          // The answer comes from the SOLVER's own per-cell ownership, not a
          // lookup table -- ask for a leg and a different arm replies than if
          // you ask for an arm, because that is genuinely how the partition
          // came out. Swap the body with `n` and the answer can change.
          if (m === 'spot') { focusSpokenSpot(heardText); return; }
          // A QUESTION, NOT AN ORDER. These do not change mode, start a
          // cycle or touch the counter -- they read live state and speak it.
          // Kept ahead of the action branches so an 'ask-' intent can never
          // fall through into setMode() with a mode name that does not exist.
          if (m && m.startsWith('ask-')) { answerQuestion(m); return; }
          if (m === 'stop') {
            window.dispatchEvent(new KeyboardEvent('keydown', { key: 'x' }));
          } else if (m === 'drink') {
            // Same mode, different payload, like pills. See drinkRequested.
            drinkRequested = true;
            pillsRequested = false;
            setMode('feed');
          } else if (m === 'pills') {
            // Same mode, different payload. See pillsRequested.
            pillsRequested = true;
            setMode('feed');
          } else {
            // CLEAR THE PILL PAYLOAD, the way the `8` key does. Saying "pills"
            // and then "bring me food" went through this branch with
            // pillsRequested still true, so the arm carried the glass of pills
            // while the label read FEEDING and the bar counted SPOONS. The key
            // handler clears it at the `8` branch; the voice path did not, and
            // voice is the beat where a judge is most likely to ask for
            // something twice.
            if (m === 'feed') pillsRequested = drinkRequested = false;
            setMode(m);
          }
        },
        onHeard: (text, final) => {
          const el = document.getElementById('heard');
          if (!el) return;
          // A FAULT IS NOT A QUOTATION. This wraps everything in quote marks
          // because it is showing what the machine HEARD, and the two faults
          // the recogniser reports through the same channel -- a blocked
          // microphone and a dead network -- would have rendered as if the
          // volunteer had said them out loud.
          const fault = text === 'microphone blocked'
                     || text === 'no network for speech';
          // SHORT ENOUGH FOR THE BIG READOUT. #pct renders at 97px on a 1080p
          // projector in a fixed-width pixel font, so 21 characters measure
          // 2037px against a 1920 frame. Measured, not guessed. The long
          // wording stays in #heard, where the font is 16px and there is room
          // for a sentence.
          if (fault) {
            voiceFault = text === 'no network for speech' ? 'NO NETWORK'
                                                          : 'NO MIC';
          }
          el.textContent = !text ? '' : fault ? text.toUpperCase() : `“${text}”`;
          el.classList.toggle('final', !!final);
        },
      });
    }
    if (!voice) {
      // NO RECOGNISER IN THIS BROWSER. Say so, on the beat, rather than
      // doing nothing: the operator gets the same NO MIC they already know
      // how to react to, and the mode strip still moves so the presenter's
      // line has somewhere to land.
      voiceFault = 'NO MIC';
      const h = document.getElementById('heard');
      if (h) { h.textContent = 'NO SPEECH IN THIS BROWSER'; h.classList.add('final'); }
      setMode('voice');
      play('click');
      return;
    }
    // A RETRY CLEARS THE FAULT. Without this one network blip would pin the
    // message on screen for the rest of the demo, even after the wifi came
    // back and the recogniser started working again.
    voiceFault = null;
    const on = voice.toggle();
    const el = document.getElementById('heard');
    if (el) el.textContent = on ? 'listening...' : '';
    setMode('voice');
    play('click');
    return;
  }

  // A SECOND PERSON. The pitch's central claim is that paths come from the
  // person's own measured body rather than from a script, and one scan on
  // screen cannot prove that -- it looks exactly like a canned path would.
  // 'n' swaps in a differently proportioned body, and the territories
  // visibly re-solve: arm 0 gains 44 cells, arm 3 gains 118.
  if (e.key === 'n' || e.key === 'N') {
    // ONE SWAP AT A TIME is enforced inside swapBody(); flipping the index
    // here and letting it refuse is safe because a refused swap leaves the
    // index pointing at a body nobody asked to leave.
    if (swapBusy) return;
    bodyIndex = bodyIndex ? 0 : 1;
    await swapBody();
    play('click');
    return;
  }

  // 'v' RE-SOLVES THE PARTITION FOR REAL, right now, on the backend.
  //
  // Everything else on this page reads a partition that was solved offline and
  // committed. This asks the machine to run the solver again and send the
  // result back, which is the difference between "the paths came from a body"
  // and "the paths come from a body". It is the only key that makes the
  // backend do arithmetic.
  //
  // NOT ON THE RUN OF SHOW. Measured at 3.8s, which is short enough to show
  // someone but still several times longer than any beat in a 120-second
  // script, so this is for a judge asking "can it do that live?" rather than
  // for the script itself.
  if (e.key === 'v' || e.key === 'V') {
    // NEVER SILENT, for the same reason the estop key is not: an operator who
    // presses this and sees nothing has no way to tell a slow solve from a
    // dead socket, and will press it again.
    if (sock && sock.readyState === 1) {
      sock.send(JSON.stringify({cmd: 'solve'}));
      flash('SOLVING — this takes a while');
      play('click');
    } else {
      flash('NO LINK — showing the baked scan');
    }
    return;
  }


  if (e.key === 'b' || e.key === 'B') {
    if (!territories) return;
    territoriesOn = !territoriesOn;
    // THE FRAME GOES TO THE SCAN. The script has the presenter pointing at
    // the screen here; the camera can do that instead.
    setShot(territoriesOn ? 'scan' : 'wide');
    // CATCH THE SWEEP UP BEFORE SHOWING IT. The coverage subscriber drops
    // every emit while the overlay is hidden, so the first one after it comes
    // back paints every cell up to the current fraction in a single frame --
    // two thirds of the body going white at once, which reads as a
    // pre-rendered fill and destroys the one thing `b` exists to prove.
    //
    // Seeking here, while it is still invisible, means the cells are already
    // correct when the fade brings them in. Same artefact the `n` handler
    // guards against; this is the other way in.
    if (territoriesOn && coverage && recs.length) {
      coverage.seek(cleaned / recs.length);
    }
    if (bodyPlinth) bodyPlinth.visible = territoriesOn;
    if (bodyShadow) bodyShadow.visible = territoriesOn;
    territoriesOn ? territories.show() : territories.hide();
    showCounts(territoriesOn ? territories : null);
    play('click');
    return;
  }

  // 'g' FOR THE HANDOFF MOVE, the 1:02 beat. NOT 'h': the Python window's
  // 'h' is home, and one letter meaning two things on one recovery card is a
  // trap for an operator who is already having a bad minute. A long, barely-there camera move that
  // covers the ten seconds the presenter and the volunteer spend trading
  // places. It changes no state at all -- no mode, no cycle, no props -- so
  // it is safe to press at any point and safe to forget: the next mode key
  // sets its own shot and this one is gone.
  //
  // 6s, which is most of the beat. A short move would finish while the
  // shuffle is still happening and leave the screen just as still as before.
  if (e.key === 'g' || e.key === 'G') { setShot('handoff', 6.0); play('click'); return; }
  if (e.key === '7') { setMode('shower'); return; }
  if (e.key === '8') { pillsRequested = drinkRequested = false;
                       setMode('feed'); return; }
  // SHIFT+8 is the pill beat. The operator must be able to show it without
  // the microphone, because the voice is the one part of this demo that can
  // be defeated by a noisy room.
  // MATCH THE PHYSICAL KEY, like every other operator key in this file.
  // The script tells the operator "shift+8", and on a US layout that does
  // arrive as '*' -- but e.key for a shifted digit is layout-dependent, and
  // this is the only key in the demo pressed with a modifier. e.code is the
  // physical key and ignores both the layout and the modifier. The '*'
  // branch stays so the numeric keypad's own asterisk still works.
  // SHIFT+7 IS THE DRINK, the sibling of shift+8's pills. The mode strip
  // promises eating, drinking and pills; without a key the middle one is
  // reachable only by voice, which is the least reliable part of the demo.
  if ((e.code === 'Digit7' && e.shiftKey) || e.key === '&') {
    drinkRequested = true; pillsRequested = false;
    setMode('feed'); return;
  }
  if ((e.code === 'Digit8' && e.shiftKey) || e.key === '*') {
    pillsRequested = true; drinkRequested = false;
    setMode('feed'); return;
  }
  if (e.key === '9') { setMode('vitals'); return; }
  if (e.key === '0') { setMode('voice');  return; }

  if (e.key === 'f') {
    // THE FINALE BELONGS TO THE SHOWER BEAT. With the readout borrowed by
    // feed, vitals or voice, setClean(100) takes its borrowed branch and
    // fires finale() without painting the number -- so the confetti and the
    // fanfare land over "72 BPM" or "2 / 4 SPOONS", the readout turns
    // celebration green, and the cleanliness counter sits at 100 behind it
    // for a cycle nobody watched.
    //
    // `f` is the card's "force the finale", which is what an operator who is
    // behind reaches for, and being behind is how you end up pressing it
    // after `9`. Bouncing to shower first costs one keypress they needed
    // anyway and puts the celebration on the thing being celebrated.
    if (mode !== 'shower') setMode('shower');
    recs.forEach(r => { if (!r.gone) popSplotch(r, camera); });
    cleaned = recs.length; setClean(100);
    // THE SITE THAT WOULD HAVE BEEN MISSED. 'f' bypasses the pop handler and
    // jumps the counter straight to 100, so without this the foam would freeze at
    // whatever the last individual pop left -- a visible disagreement with the
    // number, on the key an operator hits when a splotch will not pop on stage.
    avatar.setSuds?.(1);
  }
  if (e.key === 'r') resetAll();
  // `c` STAYS EXACTLY finale(), AND A GUARD PINS THAT. I added a mode bounce
  // here for the same reason as `f` above -- a finale over a heart rate
  // celebrates nothing -- and section 5d2 of test_docs_match_code.py caught
  // it within the minute. That guard exists because the docs once drew a
  // distinction the code did not make, and it holds this binding to a single
  // call so the description cannot drift again.
  //
  // `f` is the key that needed fixing: it is the card's "force the finale",
  // which is what an operator who is behind actually reaches for. `c` is
  // deliberate, and the card already warns it is the whole victory beat.
  if (e.code === 'KeyC' && !e.shiftKey && !e.metaKey && !e.ctrlKey) finale();

  // ---- 'v' : NEXT CHARACTER -----------------------------------------------
  // The pack ships 12 people on an IDENTICAL rig, so this is a filename swap
  // and every spring, splotch and clip survives it. Worth a key because a
  // volunteer seeing someone who looks a bit like them is the difference
  // between "a cartoon" and "your cartoon" -- and it costs one reload.
  // e.code, not e.key: e.key is 'E' with caps lock on, so a lowercase
  // comparison silently does nothing -- the same trap that made shift+C
  // dead on a real keyboard. e.code is the physical key and ignores both
  // caps lock and shift. (The estop keeps its explicit 'x'||'X' pair; it
  // predates this and changing a safety control needs its own change.)
  if (e.code === 'KeyV' && !e.metaKey && !e.ctrlKey) {
    const cast = avatar?.cast || [];
    if (cast.length > 1) {
      const next = cast[(cast.indexOf(avatar.current) + 1) % cast.length];
      // THE SWAP LIVES ENTIRELY IN localStorage. avatar.js reads the key back
      // on boot and falls back to CAST[0] when it is missing, so a failed write
      // is not an error anywhere -- the page reloads and returns the SAME
      // character. Measured with storage blocked the way a private window or
      // blocked site data blocks it: page boots, flash promises
      // "CHARACTER -> female-a", reload lands, character unchanged, console
      // silent. The operator presses v again and again during setup while the
      // HUD keeps agreeing with them.
      //
      // So the promise is made only after the write is known to have stuck.
      let stored = false;
      try {
        localStorage.setItem('wheelgentic.character', next);
        stored = localStorage.getItem('wheelgentic.character') === next;
      } catch (_) {}
      // NO EARLY RETURN HERE. This whole block lives inside ONE keydown arrow
      // function that runs from line 839 to the end of the handler, so a
      // `return` on the storage-blocked path would skip every binding declared
      // below it -- the 'e' emote and the 'd' death gag both sit further down.
      // They would die silently in exactly the private-window case this guard
      // was added for, which is the same bug wearing a different hat.
      if (!stored) {
        console.warn('character swap needs localStorage; it is blocked here');
        flashHold('CANNOT SWITCH CHARACTER — storage blocked');
      } else {
        flash(`CHARACTER → ${next.replace('character-', '').replace('mini-', '')}`);
        // Reload rather than hot-swap: the splotches, springs and robot-arm
        // placement are all solved against a specific body, and a hot-swap
        // that half-works on stage is worse than a 2s reload before anyone is
        // watching. This key is a SETUP control, not a demo beat.
        setTimeout(() => location.reload(), 700);
      }
    }
  }

  // ---- 'e' : EMOTE ---------------------------------------------------------
  // Cycles the character's reaction clips. The pack ships 32 and the demo only
  // triggers a few automatically; this lets the operator get a laugh on
  // demand, which is exactly the "goofy" the brief asks for.
  // e.code, not e.key: e.key is 'E' with caps lock on, so a lowercase
  // comparison silently does nothing -- the same trap that made shift+C
  // dead on a real keyboard. e.code is the physical key and ignores both
  // caps lock and shift. (The estop keeps its explicit 'x'||'X' pair; it
  // predates this and changing a safety control needs its own change.)
  if (e.code === 'KeyE' && !e.metaKey && !e.ctrlKey) {
    const EMOTES = ['emote-yes', 'emote-no', 'interact-right', 'pick-up',
                    'attack-kick-right', 'jump'];
    emoteIdx = (emoteIdx + 1) % EMOTES.length;
    const name = EMOTES[emoteIdx];
    if (avatar?.playOnce?.(name)) flash(name.toUpperCase());
  }

  // ---- 'd' : THE DEATH GAG -------------------------------------------------
  // The pack's 'die' clip is a full flop-over. The recovery card's last row is
  // "total failure -> narrate over the backup video"; this is the version you
  // can trigger ON PURPOSE for a laugh, and it doubles as a way to show the
  // character is a real rig rather than a video.
  // e.code, not e.key: e.key is 'E' with caps lock on, so a lowercase
  // comparison silently does nothing -- the same trap that made shift+C
  // dead on a real keyboard. e.code is the physical key and ignores both
  // caps lock and shift. (The estop keeps its explicit 'x'||'X' pair; it
  // predates this and changing a safety control needs its own change.)
  if (e.code === 'KeyD' && !e.metaKey && !e.ctrlKey) {
    if (avatar?.playOnce?.('die', 0.1)) {
      flash('OH NO');
      // WITHDRAW THE ARM. Screenshotted the gag at 400ms, 1.5s and 3s: the
      // character is flat on the floor and the robot is still frozen in its
      // scrubbing pose, sponge hovering at the height the forearm used to be,
      // rubbing empty air above a body. At 400ms the sponge visually overlaps
      // the collapsed torso. The joke is the character giving up; a machine
      // carrying on regardless reads as the demo having frozen.
      //
      // 'rest' only, NOT stopScrubChoreography(). If a cycle is live its
      // interval re-issues strokeNow() every 260ms and would fight this, but
      // stopping the choreography from here would change estop-adjacent
      // behaviour for a comedy key. The card documents 'd' as a standalone
      // gag, so park the arm and leave the cycle alone.
      fleetPhase('rest');
    }
  }
});

// DEBUG HANDLE — lets an automated screenshot test verify that a splotch is
// actually ON the limb in screen space, instead of eyeballing it. Harmless in
// production (read-only handle), and it is how the placement bug below got
// caught after three wrong guesses.
window.__wheelgentic = { scene, camera, avatar, recs, renderer,
                     placeSplotches, popNearest, resetAll,
                     // THE ROBOT ARM, so a test can see it. Without this every
                     // check had to hunt the scene for a mesh whose material
                     // colour happened to match the sponge -- which silently
                     // finds nothing the day someone retints it, and the test
                     // then passes by asserting on undefined.
                     robot,
                     // THE FLEET and THE VOICE, for the same reason as the
                     // robot above. Without the fleet a check has to hunt the
                     // scene for a box whose dimensions happen to match a
                     // sponge, which finds nothing the day someone reshapes
                     // it. Without the voice there is no way to drive the
                     // recogniser's error handler at all -- the faults it
                     // reports only happen on a real venue network, which is
                     // exactly where nobody can run a test.
                     fleet,
                     get voice() { return voice; },
                     get voiceFault() { return voiceFault; },
                     // read-only view of the live-cycle flag, so a test can
                     // wait for a REAL cycle instead of guessing at timing
                     isCycleLive: () => cycleLive,
                     // WHETHER THE SCAN IS UP, AND WHETHER ITS PEDESTAL IS
                     // WITH IT. A swap takes the overlay out of service and
                     // puts it back, and "the cloud returned but its plinth
                     // did not" is a real way for that to go wrong that reads
                     // in a screenshot as the measured body floating. Three
                     // separate things carry that state -- territoriesOn, the
                     // plinth and its shadow -- so a test that reads only the
                     // first passes while the picture is wrong.
                     get territoriesOn() { return territoriesOn; },
                     get bodyPlinthVisible() { return !!bodyPlinth?.visible; },
                     get bodyShadowVisible() { return !!bodyShadow?.visible; },
                     // WHICH BODY FILE IS ON SCREEN. A live solve swaps the
                     // file underneath the same overlay object, so identity is
                     // the only way to tell a fresh partition from the bake.
                     get bodyFile() { return bodyFile(); },
                     get liveBody() { return liveBody; },
                     // Whether the measured skeleton is reaching the
                     // character, and what it says. A screenshot cannot
                     // tell a posed arm from an idle one that happens to
                     // be in a similar place.
                     get limbsWorld() { return limbsWorld; },
                     get limbsSrc() { return limbsSrc; },
                     // WHICH FRAMING THE CAMERA IS ON. A swap that quietly
                     // leaves the scan shot points the projector at the wrong
                     // half of the room on the exact beat the partition
                     // re-solves, and nothing else on this object would show
                     // it: the overlay reads as visible either way.
                     get shot() { return shot; },
                     // THE POSE DETECTOR ITSELF, so a test can make the
                     // SHIPPED branch see a person. Without this the only
                     // way to exercise SUBJECT LOCKED is to stand in front
                     // of the camera, so the locked half of that readout
                     // would ship untested while the looking half is the
                     // one a headless run happens to produce.
                     get landmarker() { return landmarker; },
                     set landmarker(v) { landmarker = v; },
                     // THE SHIPPED SPOT RESOLVER, not a copy of it. The voice
                     // path that normally reaches this lives behind a real
                     // microphone and a closure, so a test can only get here
                     // by re-implementing the lookup -- which would pass while
                     // the shipped function was broken. This is the same
                     // function onIntent calls.
                     focusSpokenSpot,
                     // The agent half, reachable without a microphone. Every
                     // voice feature in this file has been broken at least
                     // once in a way only a real call could catch, and
                     // headless Chromium has no SpeechRecognition to drive.
                     answerQuestion,
                     // The scan itself, so a test can wait for the async bake
                     // to land instead of sleeping and hoping. It is null
                     // until makeTerritories resolves.
                     get territories() { return territories; },
                     // so a test can drive the REAL banner path rather than
                     // writing textContent and proving nothing
                     flashHold, releaseHold, isHeld: () => held,
                     // THE SCRUB CHOREOGRAPHY, for the same reason. The 's' key
                     // gates on a live socket (see the keydown handler), so with
                     // no Python running there is NO way to start the stroke
                     // tick -- and the tick is what drives the sponge, the limb
                     // impulses and the suds. A screenshot taken after pressing
                     // 's' with the socket down shows an idle character and
                     // proves nothing, which is exactly the hollow check this
                     // handle exists to prevent.
                     startScrubChoreography, stopScrubChoreography };

// ---- THE SHOT LIST ----------------------------------------------------
//
// Five framings, one per beat of the demo, expressed as OFFSETS from whatever
// fit() computes rather than as absolute positions. That is the whole design:
// fit() owns the aspect arithmetic -- FOV, distance, the 4:3 aim drop -- and
// every one of those numbers was solved by screenshot across four sessions.
// A shot that set absolute values would throw that away and would break on a
// projector whose resolution is not the one it was tuned at.
//
// `dolly` scales the camera's distance from its aim point: 1.0 is the wide
// shot fit() chose, below 1.0 pushes in. `aim` shifts what it looks at, in
// world units. `fov` is added to fit()'s own.
// THE SCREEN SHAKE DOES NOT DRIFT, AND LOSES TO A MOVE ON PURPOSE.
//
// An audit reported that juice.js's pop shake leaves a permanent +0.04 x
// offset per splotch, accumulating to 0.12 over a cycle. Checked against the
// vendored GSAP rather than argued: seeking the tween with progress(1) does
// end at +0.04, which is what made it look real, but a seek is not playback.
// Driven by the actual ticker over its full 0.30s the value returns to
// exactly where it started. No drift.
//
// What IS true: juice.js nudges
// camera.position.x by 0.04 on each splotch pop, and fit() hard-sets the
// whole vector on every frame of a shot move -- so during the 2.2s scrub
// push-in the shake is suppressed. That is 0.04 units on a camera 4.5 units
// away, under 1% of the frame, against a move that IS the beat. Making the
// shake an offset applied after fit() is real plumbing for a jitter nobody
// can see while the camera is travelling.
const SHOTS = {
  // The establishing frame. Exactly what fit() has always produced, so the
  // page still boots to the composition every screenshot was taken against.
  // SWUNG ROUND AND DROPPED, and this is the whole fix for "it does not read
  // as a wheelchair". At the old near-head-on angle the seated body covered
  // the chair almost exactly and nothing added to the chair could be seen;
  // from here the wheel, the blue frame and the footplate all read, and the
  // four arms fan around the chair as one machine instead of standing on
  // four separate pads. The face still reads at this orbit -- further round
  // (0.8+) it turns into a profile and the dignity of the pose goes with it.
  // SOLVED BY SCREENSHOT, and the aim sign is the opposite of the instinct.
  // Raising the aim point pushes the subject DOWN and away (tried +0.34: it
  // left a third of the frame as empty lit wall above the head). Lowering it
  // lifts the subject up into the middle. The swing round to 0.62 also costs
  // screen size, because the arms fan across the frame's width at that angle
  // rather than toward the camera, so the dolly pulls in to pay it back.
  // FRONT-FACING, 2026-09-19. Tyler: "the camera should be more front
  // facing". 0.62 was chosen to make the chair read (see the note above);
  // 0.30 keeps enough of that -- the wheel and footplate still show -- while
  // putting the face square to the audience instead of three-quarter. The
  // dolly goes back out because the arms fan toward the camera at this angle
  // rather than across the frame, so they need the room 0.88 took away.
  // HEAD-ON, AND IT IS THE RIG THAT SAYS SO, not taste. scrub3d's
  // frames.world_from_camera() derives the world frame by taking the
  // subject's facing direction to BE the camera's backward axis -- "the
  // person faces the camera" is the assumption the whole body scan and the
  // four-arm partition are solved under. So the only virtual camera that
  // agrees with the physical rig is one sitting where the D455 sits: orbit 0.
  //
  // The earlier 0.62 swing was chosen to make the chair read as a wheelchair,
  // and it did -- but it put the view somewhere no real sensor is, so the
  // arms on screen no longer matched the arms the governor solved for.
  // applyCameraPose() below overwrites this from the MEASURED pose in
  // body.json when one is present; these numbers are the no-capture fallback.
  wide:   { dolly: 0.96, aim: [0, -0.12, 0],    fov: 0,
            orbit: 0.0, rise: 0.74 },
  // `b`: the measured body appears at x -2.45. Slide left and push in a
  // little so the scan is not a third of a wide shot.
  // 0.86 -> 0.76, BECAUSE WIDE MOVED. Every shot's dolly is relative to the
  // same base distance, so these numbers only ever meant anything next to
  // wide's. Wide came in from 1.00 to 0.88 to pay back the screen size the
  // orbit swing costs, which left scan pushing in by 0.02 -- a move the eye
  // cannot see, and `b` is the beat where the script has the presenter point
  // at the screen. Caught by the degraded-boot check, which requires the
  // camera to travel at least 0.3 on `b` and measured 0.13.
  scan:   { dolly: 0.76, aim: [-0.85, 0.10, 0], fov: 0 },
  // `s`: the scrub. The sponge meets the forearm around x 0.7, y 1.4 -- this
  // is the money shot and it has been playing at 20% of the frame.
  // IT CROPS THE SCAN, ON PURPOSE. Measured at 1600x900: this frame spans
  // x -2.46..2.46 and the measured body sits at x -3.15..-1.75, so the
  // push-in cuts its left edge and its head. The only dolly that keeps the
  // whole scan is 0.92, which is barely a move at all and throws away the
  // reason for the shot.
  //
  // The scrub beat's job is the arms and the dirt: the scan had its own beat
  // 40 seconds earlier and the script points at the governor's verdicts here,
  // not at the cloud. Letting part of the scan sit outside the frame during
  // the one beat that is about the machine working is the right trade.
  // THE HANDOFF, 1:02. The only beat in the run of show with no keypress:
  // the presenter steps out, the volunteer steps in, and the camera used
  // to sit parked in `scan` while two people shuffled in front of it. A
  // still screen is where a judge's eye leaves the projector, and it
  // leaves it for the one thing you least want watched -- the tracker
  // changing subject.
  //
  // Barely a move on purpose. It sits between `scan` and `wide` and is
  // driven with a long tween, so it reads as the camera breathing rather
  // than as a cut. Nothing about the scene changes; only the framing
  // does, which is enough to keep the frame alive for ten seconds.
  handoff:{ dolly: 0.94, aim: [-0.30, 0.12, 0], fov: 0 },
  scrub:  { dolly: 0.70, aim: [0.35, 0.18, 0],  fov: -2 },
  // `8`: feeding is intimate. Closer still, and higher, because the bowl
  // travels to the mouth.
  feed:   { dolly: 0.62, aim: [0.15, 0.34, 0],  fov: -2 },
  // `9`: vitals pulls back to the whole room, which also sells the four arms
  // one last time before the voice beat.
  room:   { dolly: 1.12, aim: [0, -0.05, 0],    fov: 2 },
};

let shot = 'wide';

// The interpolation state, DECLARED ABOVE the functions that read it. `t` runs
// 0 -> 1 across a move and `shotFrom` is the framing the move started from, so
// an interrupted move continues from where the camera actually is. `let` and
// `const` are not hoisted the way `function` is, so a read before this line
// throws -- and this file already records that a temporal-dead-zone throw in
// the wrong place kills the whole keyboard handler.
const shotMix = { t: 1 };
let shotFrom = null;

/** The framing right now: the shot being left, blended toward the shot being
 *  entered, by however far the tween has run. */
function shotNow() {
  // try/catch, NOT typeof. `const shotMix` is initialised further down the
  // module than setMode and the keydown handler are DECLARED, and this file
  // records that one temporal-dead-zone throw takes the whole keyboard with
  // it. The obvious guard does not work:
  //
  //   (function(){ typeof x === 'undefined'; const x = 1; })()
  //   -> ReferenceError: Cannot access 'x' before initialization
  //
  // `typeof` only suppresses a throw for an UNDECLARED identifier; for a
  // const in its dead zone the typeof expression is itself what throws.
  // Verified in node rather than assumed. Nothing reaches this before
  // initialisation today; the catch is so a later top-level call cannot take
  // the keyboard with it.
  let a, b, k;
  try {
    a = shotFrom || SHOTS.wide;
    b = SHOTS[shot] || SHOTS.wide;
    k = Math.min(1, Math.max(0, shotMix.t));
  } catch (_) {
    // Identity, and it must carry orbit/rise for the same reason the blend
    // below does: fit() reads this object directly.
    return { dolly: 1, fov: 0, orbit: 0.30, rise: 1.00, aim: [0, 0, 0] };
  }
  const lerp = (x, y) => x + (y - x) * k;
  // ORBIT AND RISE BLEND TOO. This function returns a FRESH object and fit()
  // reads only what is on it, so a field that is not named here does not
  // merely fail to interpolate -- it is undefined for the whole tween, and
  // fit() falls back to the default on every frame. The camera would sit at
  // the old angle for the entire move and jump to the new one at the end.
  // Both default to the pre-existing values so a shot that omits them is
  // unchanged.
  const at = (o, key, dflt) => (o[key] != null ? o[key] : dflt);
  return {
    dolly: lerp(a.dolly, b.dolly),
    fov:   lerp(a.fov,   b.fov),
    orbit: lerp(at(a, 'orbit', 0.30), at(b, 'orbit', 0.30)),
    rise:  lerp(at(a, 'rise',  1.00), at(b, 'rise',  1.00)),
    aim: [lerp(a.aim[0], b.aim[0]),
          lerp(a.aim[1], b.aim[1]),
          lerp(a.aim[2], b.aim[2])],
  };
}

/** Point the virtual camera where the real D455 actually is.
 *
 *  THE DEPTH CAMERA IS THE REFERENCE. Everything scrub3d solves -- the body
 *  scan, the four-arm partition, the governor's reach verdicts -- is computed
 *  in a world frame built from the camera's own floor fit, under the explicit
 *  assumption that the person is facing it (frames.py::world_from_camera).
 *  A projector view from anywhere else is showing the audience a geometry the
 *  solver never used.
 *
 *  tools/export_body.py writes the measured pose into body.json as `camera`.
 *  When it is absent, or marked measured:false because no capture was on the
 *  machine that baked it, the SHOTS defaults stand and nothing moves -- a
 *  missing measurement must cost the demo nothing.
 */
async function applyCameraPose() {
  let cam = null;
  try {
    const r = await fetch('assets/body.json', { cache: 'no-store' });
    if (r.ok) cam = (await r.json()).camera;
  } catch (_) {
    // No body.json on disk is the normal case before a scan. Stay on defaults.
  }
  if (!cam) return;
  // orbit 0 is head-on. The export always writes 0 today; reading it rather
  // than assuming it means a rig that genuinely mounts the sensor off-axis
  // can say so in one number instead of a code change.
  if (typeof cam.orbit === 'number') SHOTS.wide.orbit = cam.orbit;
  // Height maps to `rise`, the multiplier on the camera's Y. The scene's
  // subject sits with its head near y=1.75, so a sensor at ~1.10m looks at
  // the chest rather than down at the scalp -- which is what a chair-mounted
  // D455 actually sees.
  if (typeof cam.height_m === 'number' && cam.height_m > 0.2) {
    SHOTS.wide.rise = Math.max(0.5, Math.min(1.2, cam.height_m / 1.49));
  }
  if (!cam.measured) {
    console.info('[camera] body.json carries a FALLBACK pose, not a measured '
                 + 'one. Bake with a capture present to use the real rig.');
  }
  // Only re-frame if we are still on the opening shot. Stealing the camera
  // out from under a beat the presenter already moved to would be worse than
  // an approximate opening frame.
  if (shot === 'wide') setShot('wide', 0.0);
}
applyCameraPose();

/** Move to a named shot. Unknown names fall back to wide rather than throwing
 *  inside a keydown handler, which on this page kills the whole keyboard. */
function setShot(name, seconds = 1.4) {
  // CAPTURE BEFORE MUTATING. shotNow() blends from `shotFrom` toward
  // `SHOTS[shot]`, so reading it AFTER assigning `shot` gives the framing
  // partway to the new destination rather than where the camera actually is.
  // The result is the snap the capture below exists to prevent, and it is
  // worst on `b` then `s` -- the script's own sequence -- because scan pulls
  // the aim left and scrub pushes it right, so the error is on both sides at
  // once. Measured at 0.5s into an interrupted move: about 0.24 world units.
  const from = shotNow();
  shot = SHOTS[name] ? name : 'wide';
  // Without gsap the shot still CHANGES, it just arrives instantly. Same rule
  // as the counter and the splotch hide: a missing CDN costs the animation,
  // never the state.
  if (!window.gsap) {
    // LAND ON THE SHOT, do not leave the mixer mid-blend. Calling fit() alone
    // works only by luck: shotMix.t starts at 1, so the first call happens to
    // resolve fully. After any earlier move it would be stuck at whatever t
    // the interrupted tween left, and every later shot would arrive partly
    // blended toward it.
    shotFrom = SHOTS[shot];
    shotMix.t = 1;
    fit();
    return;
  }
  // Tween a scalar rather than the camera directly, and let fit() do the
  // placement on every frame of the tween. That way the moving camera obeys
  // exactly the same aspect rules as the static one, and a resize mid-move is
  // simply the next frame's fit().
  // `from` was captured at the top of this function, before `shot` moved.
  gsap.killTweensOf(shotMix);
  shotFrom = from;
  shotMix.t = 0;
  gsap.to(shotMix, { t: 1, duration: seconds, ease: 'power2.inOut',
                     onUpdate: fit });
}

function fit() {
  camera.aspect = innerWidth / innerHeight;
  // Frame by HEIGHT, not the default horizontal FOV. On a 16:9 projector the
  // fixed 6.6-unit distance left the character small with dead space either
  // side; on 4:3 it nearly overflowed. Pull in as the frame gets wider.
  // FOV 42 -> 26, AND THE AIM POINT MOVED. MEASURED at 1920x1080: at 42 degrees
  // the 1.75-unit character filled 36% of frame height -- a small doll to a
  // judge ten feet back. Narrowing to 26 alone was not enough and introduced
  // two new faults, both caught by screenshot: the feet collided with the
  // cleanliness bar and the robot's base plate ran off the right edge.
  //
  // The cause was aiming at (0, 1.75, 0), the CHARACTER's centre, when the
  // subject is the character AND the arm together: x -1.00..1.96, y 0.55..2.30.
  // That content's true centre is (0.48, 1.42). Aiming there re-centres both.
  //
  // HUD-SAFE BAND, measured from the live DOM: the privacy line ends at y=97
  // and the bottom HUD block starts at y=874, so the usable band is 777 of
  // 1080 px. At FOV 26 / dist 7.0 the frame is 5.75 x 3.23 units, putting the
  // character at 54% of frame height and the full content at 52% of width --
  // comfortably inside that band with room for the arm's travel.
  // RE-SOLVED FOR THE FLEET. Everything above was measured when the subject
  // was one character plus one arm spanning x -1.00..1.96. Four arms ringing
  // a chair span roughly x -2.30..2.45 and z -1.40..0.55, so the old framing
  // ran two arms and two plinths off the edges -- caught by screenshot, which
  // is how every framing decision in this file has been made.
  //
  // FOV widens 26 -> 34 and the aim point returns to x 0 because the content
  // is now SYMMETRIC about the chair: arms left and right, legs front and
  // back. The 0.48 offset existed only to re-centre a single arm sitting off
  // to one side, and keeping it would push the whole ring left.
  // WIDENED AGAIN for the measured body standing at x -2.45. Content now
  // spans about x -2.9..2.45, so the aim point shifts left of the chair to
  // hold both the person and the scan in frame.
  // THE SHOT RIDES ON TOP OF THE ASPECT MATH, never replaces it. Every number
  // below was solved by screenshot; a shot only scales the distance, nudges
  // the aim and trims the FOV, so a resize during a move re-solves the shot
  // the demo is actually in.
  const sh = shotNow();
  camera.fov = 36 + sh.fov;
  const dist = (7.4 * Math.min(1, 1.34 / camera.aspect) + 0.9) * sh.dolly;
  // ORBIT AND HEIGHT ARE PER-SHOT, defaulting to the values every existing
  // screenshot was taken against (0.30 and 1.00), so a shot that does not
  // declare them is bit-identical to what it was before this existed.
  //
  // WHY THIS EXISTS. The chair was invisible from the wide shot and three
  // separate fixes were tried and measured worse -- two model swaps and an
  // aim change -- before anyone checked the camera. It sits at x = dist*0.30,
  // which is about a 17 degree orbit: very nearly head-on. From there the
  // seated body's silhouette (x -0.89..0.80, z -0.62..0.67) covers the chair
  // GLB (x -0.57..0.68, z -0.66..0.60) almost exactly, so no amount of chair
  // geometry can help. A footplate added at the measured foot position
  // disappeared behind the body on the very next screenshot.
  //
  // Swinging round to 0.62 and dropping the height shows the wheel, the blue
  // frame and the footplate, and the face still reads. It is the angle, not
  // the model.
  const orbit = sh.orbit != null ? sh.orbit : 0.30;
  const rise  = sh.rise  != null ? sh.rise  : 1.00;
  camera.position.set(dist * orbit,
                      2.60 * (0.55 + 0.45 * sh.dolly) * rise, dist);
  // AIM LOWER ON A NARROW SCREEN. Framing by height means a 4:3 projector
  // gets the same vertical slice as 16:9 but far less width, and the subject
  // ends up sitting high with a third of the frame empty floor below it --
  // measured at 1024x768, where the content stops around y=520 of 768.
  // Dropping the aim point with the aspect pulls the horizon up and the
  // person down into the middle of the frame.
  const low = camera.aspect < 1.5 ? 0.16 : 0;
  camera.lookAt(-0.35 + sh.aim[0], 1.30 - low + sh.aim[1], -0.10 + sh.aim[2]);
  camera.updateProjectionMatrix();
  // ONLY WHEN THE WINDOW ACTUALLY CHANGED. fit() is the tween's onUpdate now,
  // so it runs about 130 times across the 2.2s scrub push-in. setSize writes
  // canvas.width and canvas.height, and assigning those can re-allocate the
  // drawing buffer even when the value is unchanged -- a per-frame
  // reallocation during the one beat the script says to stop talking for.
  //
  // Resizing the buffer is a response to the window changing, and the window
  // does not change during a camera move.
  if (innerWidth !== _lastW || innerHeight !== _lastH) {
    _lastW = innerWidth;
    _lastH = innerHeight;
    renderer.setSize(innerWidth, innerHeight);
  }
}
addEventListener('resize', fit);
fit();

// ---- THE VIEW HER BUTTONS OPEN ----------------------------------------
//
// Plan §5 phase 4. Crystal's UI has three buttons that stand for three jobs
// this machine does -- data-action="bathe", "meds" and "eat" -- and a nav
// view, data-view="vitals". Phase 4 is the surface those open.
//
// WHY A URL PARAMETER IS THE WHOLE INTERFACE. Prohibition 2: we do not edit
// her files. So the seam cannot be anything she has to write both halves of.
// She already opens a modal on each of those buttons; putting
// `<iframe src="http://localhost:8000/?view=shower">` inside one is a single
// line of her code, added when she wants it, deleted when she does not, and
// it needs nothing from us but an address that already works. Publishing the
// address is our half. That is why this reads location.search and not a
// postMessage listener: a listener is an interface only if somebody writes a
// sender, and writing her sender is the thing we are forbidden to do.
//
// AND IT COSTS THE PROJECTOR NOTHING. The projector page is opened with no
// query string, so VIEWS[null] is undefined, this function returns at its
// first line, and every pixel and every millisecond below here never happens.
// That is the plan's "The main projector view is unaffected" enforced by
// control flow rather than by care.

/** The four task framings, keyed by the words her UI already uses.
 *
 *  KEYED ON HER NAMES, NOT OURS. Our modes are shower/feed/vitals/voice; her
 *  buttons say bathe/eat/meds and her nav says vitals. Translating here, in
 *  one table, is the only place the two vocabularies meet -- so the day she
 *  renames a button this is a one-line edit rather than a hunt. `shower` and
 *  `feed` are accepted as aliases of `bathe`/`eat` because they are the names
 *  in OUR documentation and an operator typing the URL by hand will reach for
 *  the word they have read.
 *
 *  `mode` is the mode we already have; `shot` overrides the framing that mode
 *  would normally choose. The three task views want to be CLOSER than the
 *  projector shots: those were composed for a judge ten feet from a 1920x1080
 *  projection, and this one is a panel in a modal that a person is holding at
 *  arm's length. Same scene, different distance, which is exactly what the
 *  SHOTS table's `dolly` already expresses.
 */
const VIEWS = {
  // Showering. The scrub beat's own framing, which is already the tightest
  // shot in the file and was solved by screenshot against the sponge meeting
  // the forearm -- the one thing this view is for.
  bathe:  { mode: 'shower', shot: 'scrub',  name: 'SHOWERING' },
  shower: { mode: 'shower', shot: 'scrub',  name: 'SHOWERING' },
  // Eating and medication are the SAME JOB to the machine -- lift something
  // to the person's mouth -- and main.js has always modelled them as one mode
  // with two props. `pills` is the flag the voice intent already sets, read by
  // feed's enter() on the very next tick, so setting it here reaches the
  // shipped path rather than a copy of it.
  eat:    { mode: 'feed',   shot: 'feed',   name: 'EATING' },
  feed:   { mode: 'feed',   shot: 'feed',   name: 'EATING' },
  meds:   { mode: 'feed',   shot: 'feed',   name: 'MEDICATION', pills: true },
  // Vitals pulls back to the room, because the honest picture of a vitals
  // reading on this machine is the arms NOT touching the person. Her own
  // Vitals page is a nav view rather than a task button, so this is reachable
  // the same way but is not one of the three actions.
  vitals: { mode: 'vitals', shot: 'room',   name: 'VITALS' },
};

/** What each component of the picture actually is, right now.
 *
 *  Plan §5 4.2, and the sentence that defines it: "Her adapter already models
 *  this distinction; the 3D view must not quietly lose it." Her
 *  robot-adapter.js returns `status:'simulated'` in demo mode and
 *  `{status:'disconnected', readings:null}` for vitals. Those are not error
 *  states, they are the truth about a half-built machine, and a 3D view that
 *  renders a beautiful arm without saying which of the three it is has thrown
 *  away the most important thing the backend knew.
 *
 *  THREE STATES, AND ONLY ONE OF THEM IS REACHABLE TODAY:
 *
 *  - live  -- an encoder measured this. NOTHING returns this yet. Attaching to
 *             the dimOS stack that owns the encoders is forbidden while it is
 *             live on the CAN bus, so no measured angle has ever reached this
 *             page. The branch exists because the day `--attach` is allowed,
 *             Python sends src:'measured', armJointsSrc carries it here, and
 *             this reads LIVE with no edit -- the same one-branch source swap
 *             phase 3 built underneath it.
 *  - cmd   -- we commanded these angles and drew them. Indistinguishable on
 *             screen from measured, which is the entire reason this panel has
 *             to exist. This is what the arms are today.
 *  - none  -- not connected. No socket, or no sensor.
 *
 *  ARMS READ armJointsSrc, WHICH IS THE WIRE'S OWN WORD, never an inference
 *  from the picture. If the socket is down there are no joints and the arms
 *  are running their fallback animation, so the answer is `none` and the arm
 *  on screen is a cartoon -- saying anything else would be describing the
 *  drawing rather than the machine.
 *
 *  VITALS ARE ALWAYS `none`, AND THIS IS NOT A PLACEHOLDER. Prohibition 3 and
 *  plan §7: no vitals sensor is wired. Our own GET /api/vitals answers
 *  {"status":"disconnected","readings":null} and her page renders that as a
 *  dash. There is no vitals key on the 15Hz wire to read, and the honest
 *  reason is that there is nothing to put in one. A plausible heart rate in a
 *  care product is the worst bug available, so this is hard-coded to the true
 *  answer rather than left as a variable somebody could later fill in.
 *
 *  NOTE the projector's vitals MODE does animate a 72 BPM figure, and that is
 *  a known, separate overclaim in MODES.vitals -- its own comment admits the
 *  number is simulated. This panel does not repeat it: when the vitals view is
 *  open, the panel says the sensor is not connected, directly above a readout
 *  that is showing a number. That disagreement is deliberate and is the honest
 *  side winning; the fix to the other side is a MODES.vitals change, outside
 *  this phase's files.
 */
function viewState(key) {
  const armsLinked = !!(sock && sock.readyState === 1);
  const arms = !armsLinked || !armJointsSrc ? 'none'
             : armJointsSrc === 'measured'  ? 'live'
             : 'cmd';
  const rows = [['ARMS', arms]];
  // Only the vitals view claims anything about a sensor. On the shower and
  // feed views the sensor is not part of the picture, and a row saying a
  // thing you are not being shown is disconnected is noise rather than
  // honesty.
  if (key === 'vitals') rows.push(['SENSOR', 'none']);
  return rows;
}

const TASK_WORDS = { live: 'LIVE HARDWARE',
                     cmd:  'COMMANDED, NOT MEASURED',
                     none: 'NOT CONNECTED' };

/** Repaint the honesty banner. Cheap enough to call on every socket message,
 *  but it is not called that way -- see the interval below. */
let taskView = null;          // the VIEWS entry in play, or null on the projector
let _taskLast = '';           // the last string written, so an unchanged state
                              // does not touch the DOM 15 times a second

function paintTaskView() {
  // IN A CARE VIEW THE SCAN'S DOT CLOUD STAYS OFF. It is baked from one scan
  // (tools/export_body.py), and in her UI the cartoon is posed live from the
  // depth camera, so the dots no longer sit on the body they were measured
  // on: from the chair they read as blue spots glitching over the character.
  // The projector page keeps them. Here rather than at each show(), because
  // the cloud loads late and several paths turn it on.
  if (territories) territories.hide();

  if (!taskView) return;
  const rows = viewState(taskView.key);
  // Serialise first and compare, for the same reason the joints handler only
  // repaints the link line when `src` changes: this runs on a timer, the
  // states change perhaps twice in a session, and writing identical innerHTML
  // forces layout every time. Building the string is a few concatenations; the
  // write is the expensive half.
  const sig = rows.map(([k, v]) => k + v).join('|');
  if (sig === _taskLast) return;
  _taskLast = sig;
  const el = document.getElementById('tasksrc');
  if (!el) return;
  el.innerHTML = rows.map(([k, v]) =>
    `<div><span class="k">${k}</span> <span class="${v}">${TASK_WORDS[v]}</span></div>`
  ).join('');
}

/** Read ?view= and, if it names one of hers, become that view. */
function applyTaskView() {
  let key = null;
  try {
    key = new URLSearchParams(location.search).get('view');
  } catch (_) {
    // A URL so malformed that URLSearchParams throws is not a reason to take
    // the page down. No view, projector behaviour, exactly as if no parameter
    // had been passed.
    return;
  }
  if (!key) return;                       // the projector page. Nothing below runs.
  const v = VIEWS[key.toLowerCase()];
  if (!v) {
    // AN UNKNOWN VIEW IS THE PROJECTOR VIEW, not an error page. She may ship a
    // fifth button before we ship a fifth framing, and the failure mode for
    // that must be "shows the whole machine" rather than a blank panel in a
    // modal a caretaker is looking at.
    console.warn(`[view] unknown ?view=${key} — showing the full scene`);
    return;
  }
  taskView = { key: key.toLowerCase(), ...v };

  const host = document.getElementById('taskview');
  if (host) {
    host.classList.add('on');
    const n = document.getElementById('taskname');
    if (n) n.textContent = v.name;
    // .n is on the child rather than the parent so the CSS can size the name
    // and the state rows separately; set here because the element is shared.
    n?.classList.add('n');
    document.getElementById('tasksrc')?.classList.add('s');
  }

  // THE PROP FLAG BEFORE THE MODE, not after. feed's enter() reads
  // pillsRequested on the tick it runs, so setting it afterwards would show a
  // bowl on a view whose banner says MEDICATION -- the voice path already had
  // to get this ordering right for the same reason.
  pillsRequested = !!v.pills;
  drinkRequested = false;

  // GO THROUGH setMode, NOT AROUND IT. It is what marks the mode strip, runs
  // the mode's enter(), relabels the bottom readout and releases the borrowed
  // percentage. Setting `mode` directly would give a view with the right
  // camera and the wrong everything else -- and this page has a comment
  // recording that exact class of bug three times over.
  setMode(v.mode);
  // setMode picks a framing per mode; the task views want their own, and this
  // runs immediately after so it is the last writer. 0 seconds because an
  // embedded panel opens ALREADY on its subject: a 1.4s push-in from a wide
  // shot is a nice move on a projector and a page that looks broken for a
  // second and a half in a modal.
  setShot(v.shot, 0);

  paintTaskView();
  // 500ms, NOT the render loop and NOT the socket handler. The three states
  // change when a socket opens or closes and when Python starts or stops
  // sending joints -- call it twice a session. Polling at 2Hz is below the
  // rate any human reads a label change at, costs one comparison of a short
  // string when nothing moved, and keeps this entirely out of the frame
  // budget. docs/DECISIONS.md records a reflector reverted for costing 40% of
  // the frame rate; nothing in this feature is allowed near that loop.
  setInterval(paintTaskView, 500);
}
applyTaskView();

// The task view, for a screenshot test: which one is up and what it claims.
// Read-only getters, same as every other handle on this object.
window.__wheelgentic.taskView = () => (taskView ? taskView.key : null);
window.__wheelgentic.taskState = () => (taskView ? viewState(taskView.key) : null);
