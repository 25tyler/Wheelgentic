"""scrub3d/sapiens.py -- body-part segmentation, and the sleeve guard.

WHAT THIS IS FOR, IN ONE LINE
------------------------------
It supplies the limb SILHOUETTE that the thickness measurement depends on.
That is the whole reason it is here, and it is worth being precise about why
nothing already in the project can do the job:

  - MediaPipe gives 33 joint points. Points have no width.
  - MediaPipe's segmentation mask is person-versus-background, single channel.
    It cannot tell you where an arm stops and a torso starts.
  - Depth is at its WORST exactly at a limb's outline, because stereo matching
    fattens edges by 5-15mm. The outline is precisely where width is measured.

A body-part segmentation boundary is computed from RGB, so it does not inherit
the stereo edge error. Width from the silhouette plus front profile from depth
determines an elliptical cross-section, and that is how scrub3d knows how thick
someone's forearm is. See scrub3d/girth.py.

LICENCE -- READ THIS BEFORE PACKAGING ANYTHING
-----------------------------------------------
Sapiens v1 is **CC-BY-NC-4.0 on BOTH the code and the weights**. That is
non-commercial only. It is used here because the user confirmed this project
stays non-commercial, and because the permissive alternatives genuinely cannot
do this job: SAM 2 is Apache-2.0 but class-agnostic, so it will happily segment
an arm without being able to tell you it is an arm.

Weights are NEVER committed. scrub3d/weights/ is gitignored and
scrub3d/fetch_models.py makes each user accept the terms and download their
own copy.

**Sapiens2 is deliberately NOT used**, despite being newer and better. Its
bespoke Meta licence prohibits use "for biometric processing" and in activities
presenting "a risk of death or bodily harm... operation of... machinery". A
robot arm in contact with a person is arguably both, regardless of commercial
status. v1's CC-BY-NC carries no such acceptable-use clause.

THE FAILURE MODE THAT MATTERS
------------------------------
`Apparel`, `Upper_Clothing` and `Lower_Clothing` are classes SEPARATE from the
limbs. There is no "arm under clothing" class. A person in long sleeves has
their arms labelled as clothing, and the silhouette this module exists to
provide silently becomes the outline of a shirt.

For a bathing robot bare arms are the normal case, but "normally fine" is not
a safety argument. `check_sleeves()` compares the mask against where MediaPipe
says the arms are, and the caller must fall back to landmark-defined limb
segments when it fires. Never scrub a silhouette you do not trust.
"""
import os
import threading

import numpy as np

WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights")
SEG_CKPT = "sapiens_0.3b_goliath_best_goliath_mIoU_7673_epoch_194_torchscript.pt2"

# The Goliath 28-class vocabulary, from seg/mmseg/datasets/goliath.py.
CLASSES = [
    "Background", "Apparel", "Face_Neck", "Hair", "Left_Foot", "Left_Hand",
    "Left_Lower_Arm", "Left_Lower_Leg", "Left_Shoe", "Left_Sock",
    "Left_Upper_Arm", "Left_Upper_Leg", "Lower_Clothing", "Right_Foot",
    "Right_Hand", "Right_Lower_Arm", "Right_Lower_Leg", "Right_Shoe",
    "Right_Sock", "Right_Upper_Arm", "Right_Upper_Leg", "Torso",
    "Upper_Clothing", "Lower_Lip", "Upper_Lip", "Lower_Teeth", "Upper_Teeth",
    "Tongue",
]
IDX = {n: i for i, n in enumerate(CLASSES)}

# What scrub3d cares about. Note left/right and upper/lower are all distinct,
# which is exactly the four-way split the territory assignment needs.
LIMB_CLASSES = {
    "upper_arm_L": IDX["Left_Upper_Arm"],
    "forearm_L": IDX["Left_Lower_Arm"],
    "upper_arm_R": IDX["Right_Upper_Arm"],
    "forearm_R": IDX["Right_Lower_Arm"],
}
TORSO_CLASSES = (IDX["Torso"],)
CLOTHING_CLASSES = (IDX["Apparel"], IDX["Upper_Clothing"], IDX["Lower_Clothing"])

# Native input resolution of the shipped checkpoints (H, W).
INPUT_HW = (1024, 768)
MEAN = np.array([123.5, 116.5, 103.5], np.float32)
STD = np.array([58.5, 57.0, 57.5], np.float32)


class SapiensSeg:
    """TorchScript body-part segmentation. GPU if there is one.

    Loaded lazily so that importing scrub3d does not require torch, a GPU, or
    a 1.4GB file on disk. Everything upstream of this must keep working when
    Sapiens is absent -- that is what the fallback path is for.
    """

    def __init__(self, ckpt=None, device=None, dtype=None):
        import torch
        self.torch = torch
        path = ckpt or os.path.join(WEIGHTS, SEG_CKPT)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Run scrub3d/fetch_models.py, which will "
                f"show you the CC-BY-NC-4.0 terms and download it.")
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        # bf16 on GPU roughly halves both memory and time on this card and the
        # class argmax is unaffected by that precision. fp32 on CPU, where
        # bf16 is usually slower rather than faster.
        self.dtype = dtype or (torch.bfloat16 if device == "cuda" else torch.float32)
        self.model = torch.jit.load(path, map_location=device).eval().to(self.dtype)

    def _prep(self, rgb):
        """Letterbox into the network's 1024x768, preserving aspect ratio.

        NOT a plain resize. The network's input is 1024x768 PORTRAIT and a
        RealSense colour frame is 1280x720 LANDSCAPE, so resizing straight into
        it squashes the image by a factor of 2.4 in aspect. The segmentation
        still comes out looking correct -- the model is robust to it -- but
        pixels stop being square, and every geometric quantity derived from the
        mask is then wrong without looking wrong. girth.py converts pixels to
        millimetres with a single scalar; that scalar does not exist for a
        non-uniformly scaled image.

        Returns the tensor plus the mapping needed to undo it.
        """
        import cv2
        H, W = INPUT_HW
        h0, w0 = rgb.shape[:2]
        s = min(W / w0, H / h0)
        nw, nh = int(round(w0 * s)), int(round(h0 * s))
        img = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.zeros((H, W, 3), np.uint8)
        ox, oy = (W - nw) // 2, (H - nh) // 2
        canvas[oy:oy + nh, ox:ox + nw] = img
        x = (canvas.astype(np.float32) - MEAN) / STD
        x = self.torch.from_numpy(x.transpose(2, 0, 1)[None])
        return x.to(self.device, self.dtype), (ox, oy, nw, nh, w0, h0)

    def __call__(self, rgb):
        """RGB uint8 (H, W, 3) -> class-id map (H, W) int16, SAME SIZE AS INPUT.

        Returning it in the caller's own frame is deliberate. The mask exists
        to be paired with a depth image pixel for pixel; handing back the
        network's internal 512x384 letterboxed grid pushes the un-letterboxing
        onto every call site, and getting it wrong there is silent.

        MEASURED: the network takes 1024x768 and emits logits at 512x384, half
        resolution. The LOGITS are upsampled before the argmax, not the labels
        after it -- resizing a label map quantises the class boundary to the
        coarse grid, and that boundary is exactly what girth.py measures the
        limb width from. Costs about 4ms.
        """
        import torch
        import cv2
        x, (ox, oy, nw, nh, w0, h0) = self._prep(rgb)
        with torch.inference_mode():
            logits = self.model(x)
            logits = torch.nn.functional.interpolate(
                logits.float(), size=INPUT_HW, mode="bilinear",
                align_corners=False)
        seg = logits[0].argmax(0).cpu().numpy().astype(np.int16)
        seg = seg[oy:oy + nh, ox:ox + nw]            # strip the letterbox
        return cv2.resize(seg, (w0, h0), interpolation=cv2.INTER_NEAREST)


NORMAL_CKPT = "sapiens_0.3b_normal_render_people_epoch_66_torchscript.pt2"
DEPTH_CKPT = "sapiens_0.3b_render_people_epoch_100_torchscript.pt2"


class SapiensNormal:
    """Predicted surface normals from RGB. The second AI model in the stack.

    WHY A MODEL AND NOT THE DEPTH WE ALREADY HAVE
    ----------------------------------------------
    A normal differentiated from depth is a difference of noisy numbers, so it
    carries more noise than the depth it came from, and it degrades exactly
    where it matters most: at grazing angles, on the SIDE of a limb, where the
    surface turns away from the camera. That is precisely where a sponge has to
    know which way to press, and it is the same region where this rig's stereo
    already under-reads curvature by a factor of three.

    A network predicting normals from colour has neither problem. It reads
    shading and texture rather than disparity, so a surface turning away is a
    cue instead of a failure.

    OPTIONAL BY DESIGN. Another 1.4GB under the same CC-BY-NC-4.0 terms, so
    scan.py falls back to normals fitted from the local depth neighbourhood
    when it is absent, and reports which it used. Everything works without it.
    This is accuracy, not a dependency.
    """

    def __init__(self, ckpt=None, device=None, dtype=None):
        import torch
        self.torch = torch
        path = ckpt or os.path.join(WEIGHTS, NORMAL_CKPT)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Run scrub3d/fetch_models.py, which shows "
                f"the CC-BY-NC-4.0 terms and downloads it.")
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.dtype = dtype or (torch.bfloat16 if device == "cuda"
                               else torch.float32)
        self.model = torch.jit.load(path, map_location=device).eval().to(self.dtype)

    _prep = SapiensSeg._prep

    def __call__(self, rgb):
        """RGB uint8 (H,W,3) -> unit normals (H,W,3) float32, camera frame.

        Letterboxed exactly as the segmentation is, and for the same reason:
        the network takes 1024x768 portrait while a RealSense colour frame is
        1280x720 landscape, and squashing the aspect makes every geometric
        quantity derived from the output wrong without looking wrong.
        """
        import torch
        import cv2
        x, (ox, oy, nw, nh, w0, h0) = self._prep(rgb)
        with torch.inference_mode():
            out = self.model(x)
            out = torch.nn.functional.interpolate(
                out.float(), size=INPUT_HW, mode="bilinear",
                align_corners=False)
        n = out[0].permute(1, 2, 0).cpu().numpy()
        n = n[oy:oy + nh, ox:ox + nw]
        n = cv2.resize(n, (w0, h0), interpolation=cv2.INTER_LINEAR)
        ln = np.linalg.norm(n, axis=2, keepdims=True)
        return (n / np.maximum(ln, 1e-9)).astype(np.float32)


class SapiensDepth:
    """Depth from colour. The third AI model, and the most tightly fenced.

    WHAT IT IS FOR, AND WHAT IT MUST NEVER BE USED FOR
    ---------------------------------------------------
    Stereo drops out on dark clothing and on hair -- the two things a seated
    clothed person is largely made of -- and those dropouts are holes in the
    reconstruction. A network predicting depth from colour has no such
    failure: it reads shading and context, so a black t-shirt is a surface
    rather than an absence.

    But what it returns is AFFINE-INVARIANT: correct up to one scale and one
    offset per image, which means it is a shape, not a measurement. Fitting
    those two numbers against the stereo depth that IS measured turns it into
    millimetres, and that fit is only as good as its residual, so the residual
    is reported and a bad one refuses.

    THE FENCE. Filled pixels go only to the VISUAL surface. They never reach
    scan.py, the region cells, the obstacle cloud or the collision model,
    because an inferred surface is not one a robot arm may press against. The
    rule the rest of this package follows is that we never scrub what we never
    saw; this fills in what we never saw so a person can look at it, and
    changes nothing about what gets touched.
    """

    def __init__(self, ckpt=None, device=None, dtype=None):
        import torch
        self.torch = torch
        path = ckpt or os.path.join(WEIGHTS, DEPTH_CKPT)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Run scrub3d/fetch_models.py, which shows "
                f"the CC-BY-NC-4.0 terms and downloads it.")
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.dtype = dtype or (torch.bfloat16 if device == "cuda"
                               else torch.float32)
        self.model = torch.jit.load(path, map_location=device).eval().to(self.dtype)

    _prep = SapiensSeg._prep

    def __call__(self, rgb):
        """RGB uint8 (H,W,3) -> relative depth (H,W) float32, larger = further.

        Letterboxed exactly as the segmentation and the normals are, and undone
        the same way, because a squashed aspect makes every geometric quantity
        derived from the output wrong without looking wrong.
        """
        import torch
        import cv2
        x, (ox, oy, nw, nh, w0, h0) = self._prep(rgb)
        with torch.inference_mode():
            out = self.model(x)
            out = torch.nn.functional.interpolate(
                out.float(), size=INPUT_HW, mode="bilinear",
                align_corners=False)
        d = out[0, 0].cpu().numpy()
        d = d[oy:oy + nh, ox:ox + nw]
        return cv2.resize(d, (w0, h0),
                          interpolation=cv2.INTER_LINEAR).astype("float32")


def depth_available():
    """Is the depth checkpoint on disk? Cheap, no torch import."""
    return os.path.exists(os.path.join(WEIGHTS, DEPTH_CKPT))

def normal_available():
    """Is the normal checkpoint on disk? Cheap, no torch import."""
    return os.path.exists(os.path.join(WEIGHTS, NORMAL_CKPT))


def limb_masks(seg):
    """Class map -> {region name: boolean mask} for the four scrubbable limbs."""
    return {name: (seg == cid) for name, cid in LIMB_CLASSES.items()}


def check_sleeves(seg, landmarks_px, arm_radius_px=45):
    """Is the segmentation actually seeing skin where the arms are?

    -> (ok, detail). ok=False means fall back to landmark-defined limb
    segments and tell the operator, loudly.

    `landmarks_px` is {name: (x, y)} for elbows and wrists in the SAME pixel
    frame as `seg`. We sample along each forearm and upper arm and ask what the
    model thinks is there. Clothing where an arm should be means the silhouette
    is the outline of a shirt, and every thickness derived from it is the
    thickness of a sleeve.
    """
    h, w = seg.shape
    limb_px, cloth_px, other_px = 0, 0, 0
    for a, b in (("l_elbow", "l_wrist"), ("r_elbow", "r_wrist"),
                 ("l_shoulder", "l_elbow"), ("r_shoulder", "r_elbow")):
        if a not in landmarks_px or b not in landmarks_px:
            continue
        p, q = np.array(landmarks_px[a], float), np.array(landmarks_px[b], float)
        for t in np.linspace(0.15, 0.85, 12):
            c = p + (q - p) * t
            x0, x1 = int(max(0, c[0] - arm_radius_px)), int(min(w, c[0] + arm_radius_px))
            y0, y1 = int(max(0, c[1] - arm_radius_px)), int(min(h, c[1] + arm_radius_px))
            if x1 <= x0 or y1 <= y0:
                continue
            patch = seg[y0:y1, x0:x1]
            limb_px += int(np.isin(patch, list(LIMB_CLASSES.values())).sum())
            cloth_px += int(np.isin(patch, CLOTHING_CLASSES).sum())
            other_px += patch.size
    total = max(limb_px + cloth_px, 1)
    frac = limb_px / total
    ok = frac >= 0.70
    return ok, {
        "limb_px": limb_px, "clothing_px": cloth_px, "sampled_px": other_px,
        "limb_fraction": float(frac),
        "message": ("ok" if ok else
                    f"SLEEVES DETECTED: only {100 * frac:.0f}% of the arm "
                    f"region is labelled skin. The silhouette would be the "
                    f"outline of clothing. Falling back to landmark limb "
                    f"segments; bare arms are required for a measured fit."),
    }


def available():
    """Is the checkpoint actually on disk? Cheap, no torch import."""
    return os.path.exists(os.path.join(WEIGHTS, SEG_CKPT))


# ONE OF EACH MODEL PER PROCESS
#
# Building a model reads 1.2-1.3GB from disk and moves it to the GPU, a few
# seconds each time. The scan built the normal model, the reconstruction built
# it again and the depth model besides, and a replay that scanned twice built
# the normal model three times: the console counted the loads. The weights do
# not change while a process runs, so every caller asks for the one instance.
_SHARED = {}
_SHARED_LOCK = threading.Lock()


def shared(cls, ckpt=None, device=None, dtype=None):
    """The process's one instance of a model class. -> cls instance.

    Keyed on everything the constructor takes, so asking for a different
    checkpoint, device or precision still builds a separate model. A failed
    construction is not remembered, so a missing file is reported every time
    rather than once.
    """
    key = (cls, ckpt, device, dtype)
    with _SHARED_LOCK:
        model = _SHARED.get(key)
        if model is None:
            model = _SHARED[key] = cls(ckpt=ckpt, device=device, dtype=dtype)
        return model


if __name__ == "__main__":
    print("sapiens body-part segmentation")
    print(f"  classes: {len(CLASSES)}")
    for n, i in LIMB_CLASSES.items():
        print(f"    {n:12s} -> class {i:2d}  {CLASSES[i]}")
    print(f"  checkpoint present: {available()}")

    # The sleeve guard is pure logic and testable with no model and no GPU.
    print("\n  sleeve guard, on synthetic class maps:")
    lm = {"l_elbow": (300, 400), "l_wrist": (300, 600),
          "r_elbow": (500, 400), "r_wrist": (500, 600)}
    bare = np.zeros((1024, 768), np.int16)
    bare[:, :] = IDX["Background"]
    bare[350:650, 250:350] = IDX["Left_Lower_Arm"]
    bare[350:650, 450:550] = IDX["Right_Lower_Arm"]
    ok, d = check_sleeves(bare, lm)
    print(f"    bare arms   -> ok={ok}  limb fraction {d['limb_fraction']:.2f}")
    assert ok, "the guard rejected bare arms"

    sleeved = bare.copy()
    sleeved[sleeved == IDX["Left_Lower_Arm"]] = IDX["Upper_Clothing"]
    sleeved[sleeved == IDX["Right_Lower_Arm"]] = IDX["Upper_Clothing"]
    ok, d = check_sleeves(sleeved, lm)
    print(f"    long sleeves-> ok={ok}  limb fraction {d['limb_fraction']:.2f}")
    print(f"    {d['message']}")
    assert not ok, "the guard PASSED a sleeved subject -- it is decoration"
    print("\n  guard fires on sleeves and not on skin.")

    # One of each model per process, with no weights needed to show it.
    built = []

    class _Counted:
        def __init__(self, ckpt=None, device=None, dtype=None):
            built.append((ckpt, device, dtype))

    a = shared(_Counted)
    b = shared(_Counted)
    c = shared(_Counted, device="cpu")
    print(f"  asked for a model three times, two alike: built {len(built)}")
    assert a is b and a is not c and len(built) == 2, \
        "the shared model was rebuilt, or two settings shared one model"

    class _Missing:
        def __init__(self, ckpt=None, device=None, dtype=None):
            raise FileNotFoundError("no weights")

    for _ in range(2):
        try:
            shared(_Missing)
            raise AssertionError("a model with no weights was handed out")
        except FileNotFoundError:
            pass
    print("  a missing model is reported on every request, not cached. OK")
