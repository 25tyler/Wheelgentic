#!/usr/bin/env bash
# vendor.sh — RUN THE NIGHT BEFORE. ~18 MB. Commit all of it.
# Nothing may be fetched from a CDN at runtime: venue WiFi is hostile, and
# @mediapipe/tasks-vision@0.10.22 (the version in nearly every tutorial) was
# NEVER published to npm -- jsdelivr returns a 52-byte error stub with HTTP
# 200 that fails as an opaque syntax error.
set -e
cd "$(dirname "$0")"
mkdir -p web/vendor/addons web/vendor/wasm web/vendor/fonts \
         web/assets/Textures web/models models

B=https://unpkg.com/three@0.175.0
curl -sfL -o web/vendor/three.module.min.js  $B/build/three.module.min.js
curl -sfL -o web/vendor/three.core.min.js    $B/build/three.core.min.js   # THE ONE PEOPLE MISS
curl -sfL -o web/vendor/addons/GLTFLoader.js          $B/examples/jsm/loaders/GLTFLoader.js
curl -sfL -o web/vendor/addons/BufferGeometryUtils.js $B/examples/jsm/utils/BufferGeometryUtils.js
curl -sfL -o web/vendor/addons/OutlineEffect.js       $B/examples/jsm/effects/OutlineEffect.js
# GLTFLoader imports '../utils/BufferGeometryUtils.js' relatively -- flatten it:
sed -i '' "s#'../utils/BufferGeometryUtils.js'#'./BufferGeometryUtils.js'#" \
  web/vendor/addons/GLTFLoader.js

M=https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1
curl -sfL -o web/vendor/vision_bundle.mjs $M/vision_bundle.mjs
curl -sfL -o web/vendor/wasm/vision_wasm_internal.js   $M/wasm/vision_wasm_internal.js
curl -sfL -o web/vendor/wasm/vision_wasm_internal.wasm $M/wasm/vision_wasm_internal.wasm

curl -sfL -o web/models/pose_landmarker_lite.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
curl -sfL -o models/pose_landmarker_full.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task
# HEAVY TOO, because the MediaPipe compatibility shim maps the legacy
# `model_complexity=2` onto it. Without this file that request silently
# substitutes `full`, and the substitution is not harmless: measured shoulder
# width differs 184px vs 199px between the two, about 8%, which is exactly
# the identity tolerance the body store uses to decide whether it is looking
# at the same person.
curl -sfL -o models/pose_landmarker_heavy.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task

curl -sfL -o web/vendor/confetti.browser.js https://cdn.jsdelivr.net/npm/canvas-confetti@1.9.4/dist/confetti.browser.js
curl -sfL -o web/vendor/gsap.min.js         https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js
curl -sfL -o web/vendor/zzfx.js             https://cdn.jsdelivr.net/npm/zzfx@1.3.2/ZzFXMicro.min.js
# ZzFXMicro declares zzfxX/zzfx with `let` at SCRIPT scope, so they never land
# on window -- unlockAudio() then resumes nothing and every sound is silent on
# a browser that suspends audio before a user gesture. Verified: window.zzfxX
# was undefined. Publish them.
cat >> web/vendor/zzfx.js <<'ZZEOF'

// PATCHED by vendor.sh -- see the comment above this curl.
window.zzfx = zzfx; window.zzfxX = zzfxX; window.zzfxV = zzfxV;
ZZEOF
curl -sfL -o web/vendor/fonts/PressStart2P.woff2 \
  "https://fonts.gstatic.com/s/pressstart2p/v15/e3t4euO8T-267oIAQAu6jDQyK3nVivM.woff2"

# Kenney MINI Characters 1.0 (CC0, www.kenney.nl). THE PACK THE APP ACTUALLY
# LOADS. This used to fetch the BLOCKY pack and the sanity check below
# validated character-a.glb -- a file avatar.js stopped loading when the model
# was swapped. vendor.sh would run to completion, print all-ok, and have
# verified nothing about the character on screen.
#
# WHY THE MINI PACK: rounded chunky proportions (the Fall Guys / PEAK family),
# a SKINNED rig, and 32 authored animations -- the animations are bought, not
# written. The pack also ships canes, crutches and wheelchairs -- but see the
# SEATED MODE note in web/avatar.js before reaching for them: the sit clips
# only rotate limbs, they never lower the root, so a chair cannot be placed
# under the character without authoring a pose by hand.
curl -sfL -o /tmp/kenney-mini.zip \
  'https://kenney.nl/media/pages/assets/mini-characters/bfc7e272b4-1774770718/kenney_mini-characters.zip'
unzip -o -j /tmp/kenney-mini.zip 'Models/GLB format/character-male-a.glb' -d web/assets/
mv -f web/assets/character-male-a.glb web/assets/mini-character.glb
# THE WHOLE CAST. 'v' cycles five people and they share ONE rig, so every
# spring, splotch and animation clip works on any of them unchanged. Keep this
# list in sync with CAST in web/avatar.js -- test_docs_match_code.py checks
# that every name in CAST has a file on disk.
# NOT character-male-a: line 62 above already unzips it and renames it to
# mini-character.glb. Adding it here ships the same 246916-byte file twice
# (md5 ae446c76df2ca77d86329f7bc09d38c0) and puts one face in 'v' twice.
for _c in character-female-a character-female-b character-female-c \
          character-female-d character-female-e character-female-f \
          character-male-b character-male-c character-male-d \
          character-male-e character-male-f; do
  unzip -o -j /tmp/kenney-mini.zip "Models/GLB format/${_c}.glb" -d web/assets/
done
# PROPS: VENDORED BUT NEVER LOADED. Nothing in web/ references any of these
# models, and that is deliberate.
#
# A wheelchair briefly stood beside the character. Tyler had it removed:
# "why is there a wheelchair? i didnt ask for that", then "i want it gone".
# It was my initiative, not a request. A mobility prop next to the volunteer
# says who the project is for ABOUT THEM, and they never agreed to that.
#
# The models stay vendored because re-fetching is free and the zip ships them
# anyway. Do NOT wire any of them into the scene: not the chairs, not the
# canes, not the crutch. A cane instead of the chair is the same decision with
# a smaller model. docs/DECISIONS.md carries the full reversal.
#
# The seat clips stay unused for a second, older reason: 'sit' and
# 'wheelchair-sit' only rotate limbs (rootY measured at 0.550 in all three
# states), so seating needs an authored pose, which is the build-it-yourself
# this project is told not to reach for.
for _p in wheelchair wheelchair-deluxe wheelchair-power wheelchair-power-deluxe \
          aid-cane aid-cane-blind aid-cane-low-vision aid-crutch \
          aid-glasses aid-sunglasses aid-mask aid_hearing; do
  unzip -o -j /tmp/kenney-mini.zip "Models/GLB format/${_p}.glb" -d web/assets/
done
unzip -o -j /tmp/kenney-mini.zip 'Models/GLB format/Textures/colormap.png' -d web/assets/Textures/

# ---- FOOD PROPS, for the feeding capability -------------------------------
# Kenney Food Kit (CC0, www.kenney.nl), the same artist as the characters and
# the wheelchair, so the props match the flat-toon language already on stage
# instead of looking like a different show.
#
# THE URL CARRIES A CONTENT HASH and Kenney changes it when the pack is
# updated. A hardcoded guess 404s silently and you ship a chair with nothing
# to eat. Scrape the real link off the asset page instead.
_FOODZIP=$(curl -sfL "https://kenney.nl/assets/food-kit" \
  | grep -oE 'https://kenney\.nl/media/pages/assets/food-kit/[a-z0-9-]+/[a-z_-]+\.zip' \
  | head -1)
if [ -n "$_FOODZIP" ]; then
  curl -sfL -o /tmp/kenney-food.zip "$_FOODZIP"
  for _f in bowl-soup cup-coffee cooking-spoon glass; do
    unzip -o -j /tmp/kenney-food.zip "Models/GLB format/${_f}.glb" -d web/assets/
  done
else
  echo "WARNING: could not find the Kenney food kit; feeding props will be missing"
fi
#                                'Models/GLB format/Textures/colormap.png' EXTERNAL.
#                                Skip it and you ship an untextured white blob.

echo ""
echo "vendored:"
du -sh web/vendor web/assets web/models models 2>/dev/null
echo ""
echo "SANITY (all must exist and be non-trivial):"
for f in web/vendor/three.module.min.js web/vendor/three.core.min.js \
         web/vendor/vision_bundle.mjs web/vendor/wasm/vision_wasm_internal.wasm \
         web/assets/mini-character.glb web/assets/Textures/colormap.png \
         web/models/pose_landmarker_lite.task models/pose_landmarker_full.task; do
  if [ -s "$f" ]; then printf "  ok   %8s  %s\n" "$(du -h "$f"|cut -f1)" "$f"
  else                 printf "  MISSING           %s\n" "$f"; fi
done
