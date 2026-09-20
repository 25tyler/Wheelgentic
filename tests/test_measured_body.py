"""tests/test_measured_body.py -- an unmeasured dimension never reaches the solver.

WHY THIS TEST EXISTS. docs/MEASURED-NOT-TYPED.md names one failure as the
thing the whole document is written against: "a generic body drawn as if it
were the person in the chair". The wire makes that failure easy to commit by
accident, because EVENT["body"]["mm"] carries a value for every dimension
whether or not anybody measured it -- scrub3d/live/scene_out.py says so in
its own header, "a dimension with no samples is reported as the prior WITH
confidence 0.0".

So the only thing standing between the depth camera and that failure is
scrubbot.measured_kwargs() dropping the zero-confidence keys. Copying `mm`
into the solver instead would look correct, run correctly, produce a body,
and silently claim the population's chest as this person's.

MEASURED ON THE REAL RECORDING (scrub3d/data/live_rec_sample, a D455 capture
replayed through the whole stack): three dimensions reach confidence 0.882
and six sit at exactly 0.0, because nothing samples a circumference off 3D
joints. The solved body's torso and head come out BYTE-IDENTICAL to the
bake's while the upper arms shorten 77mm and the shoulder span narrows 83mm.
That is the shape of a correct partial measurement and it is what these
checks pin down.

Run: ./venv/bin/python tests/test_measured_body.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py"))

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


# The exact shape the wire carried on the sample recording, copied from a
# websocket client rather than invented -- the numbers a synthetic fixture
# would have used are the numbers whoever wrote it assumed.
WIRE = {
    "src": "depth",
    "n_frames": 300,
    "mm": {
        "biacromial_mm": 340.3,
        "upper_arm_len_mm": 245.6,
        "forearm_len_mm": 235.3,
        "torso_len_mm": 470.0,
        "upper_arm_circ_mm": 300.0,
        "forearm_circ_mm": 280.0,
        "wrist_circ_mm": 170.0,
        "chest_circ_mm": 980.0,
        "waist_circ_mm": 860.0,
    },
    "confidence": {
        "biacromial_mm": 0.882,
        "upper_arm_len_mm": 0.882,
        "forearm_len_mm": 0.882,
        "torso_len_mm": 0.0,
        "upper_arm_circ_mm": 0.0,
        "forearm_circ_mm": 0.0,
        "wrist_circ_mm": 0.0,
        "chest_circ_mm": 0.0,
        "waist_circ_mm": 0.0,
    },
}


def main():
    print("the measured body reaches the solver, the prior does not")

    import scrubbot as SB

    m, names = SB.measured_kwargs(WIRE)

    # THE CENTRAL CLAIM. Only what was measured survives.
    check("only the confident dimensions are passed to the solver",
          names == ["biacromial_mm", "forearm_len_mm", "upper_arm_len_mm"],
          f"{names}")

    # AND THE VALUES ARE NOT ROUNDED, RESCALED OR BLENDED on the way through.
    # MEASURED-NOT-TYPED §3: "a measurement replaces a default outright,
    # never blends". A second blend on our side is the one thing that rule
    # forbids by name.
    check("a measured value passes through unchanged",
          m.get("biacromial_mm") == 340.3,
          f"{m.get('biacromial_mm')}")

    # THE ZERO-CONFIDENCE VALUES ARE THE POPULATION TABLE, and shipping them
    # would hand anatomy.ADULT back to the function that already holds it.
    # Checked against the table itself rather than a literal, so this still
    # fails if somebody edits the table and not the filter.
    try:
        from scrub3d import anatomy
        prior_chest = anatomy.ADULT["chest_circ_mm"]
        check("the dropped values really were the population prior",
              WIRE["mm"]["chest_circ_mm"] == prior_chest
              and "chest_circ_mm" not in m,
              f"chest {WIRE['mm']['chest_circ_mm']} == ADULT {prior_chest}, "
              f"dropped")
    except Exception as e:                                   # noqa: BLE001
        print(f"  SKIP: anatomy unavailable ({e})")

    # A BODY NOBODY MEASURED PRODUCES NOTHING. src "prior" means every value
    # is the typical adult; passing any of it would light up "MEASURED" for a
    # body nobody measured.
    prior_only = dict(WIRE, src="prior")
    m2, n2 = SB.measured_kwargs(prior_only)
    check("src 'prior' yields no measurements at all", m2 == {} and n2 == [],
          f"{m2}")

    # NONE AND GARBAGE ARE NOT A CRASH. This is read from a solve thread
    # whose failure mode must be "fell back to the population table".
    ok = True
    for bad in (None, {}, {"src": "depth"}, {"src": "depth", "mm": None},
                {"src": "depth", "mm": {"a": None}, "confidence": {}},
                "not a dict", 7):
        try:
            r = SB.measured_kwargs(bad)
            if r != ({}, []):
                ok = False
        except Exception:                                    # noqa: BLE001
            ok = False
    check("malformed input returns empty, never raises", ok)

    # THE THRESHOLD IS A REAL GATE, not a value that happens to pass
    # everything. A dimension just under it must not get through.
    edge = {"src": "depth",
            "mm": {"biacromial_mm": 111.0, "forearm_len_mm": 222.0},
            "confidence": {"biacromial_mm": SB._MEASURE_MIN_CONF - 0.01,
                           "forearm_len_mm": SB._MEASURE_MIN_CONF}}
    m3, n3 = SB.measured_kwargs(edge)
    check("a dimension below the threshold is refused, one at it is kept",
          n3 == ["forearm_len_mm"], f"{n3}")

    # THE PAGE AND THE BACKEND MUST AGREE ON THE THRESHOLD, or the banner
    # describes a different body than the solver built. web/main.js hard-codes
    # it in the m.body handler; this is the only thing that can catch a drift.
    js = open(os.path.join(ROOT, "web", "main.js"), encoding="utf-8").read()
    needle = f"?? 0) >= {SB._MEASURE_MIN_CONF}"
    check("web/main.js counts dimensions at the same threshold",
          needle in js, needle)

    return 1 if fails else 0


if __name__ == "__main__":
    rc = main()
    print(f"\n*** {len(fails)} FAILED: {fails}" if fails
          else "\nALL CHECKS PASSED")
    sys.exit(rc)
