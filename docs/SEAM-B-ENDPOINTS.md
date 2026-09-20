# Seam B: the three endpoints Crystal's UI calls

The gap `docs/LIVE-3D-PLAN.md` §2 calls "the single biggest gap in the
product". Her `robot-adapter.js` has always known how to call us. We had
nothing listening. Now `py/careapi.py` does.

Served by `py/scrubbot.py` on `127.0.0.1:8770`, started right after the
safety governor is built and never before it.

## Pointing her UI at us

Two lines in her carechair `.env`. Nothing else of hers changes:

```
ROBOT_MODE=live
ROBOT_BACKEND_URL=http://127.0.0.1:8770
```

`ROBOT_API_KEY` is optional on her side and we do not require one,
because this binds to loopback only. Her timeout is 15 seconds; our
slowest measured response is 6.5 milliseconds.

Our port comes from `care_api_port` in `config.json`. It is read once at
startup, so unlike the rest of that file it does not hot-reload.

## What every task goes through, in this order

1. Idempotency. A `command_id` we have already answered replays the
   stored verdict and touches nothing. Her adapter says why in its own
   comment: "No automatic retries: a lost response must not duplicate a
   physical task." Two sends, one task.
2. Validation against her own enums, checked again on our side.
3. The governor. `py/governor.py`, before anything downstream exists.
4. The state machine, reached only by what step 3 cleared.

A refused task does not reach step 4. That ordering is the reason our
backend sits between her UI and Justin's arms at all.

## POST /api/task

Body: `{command_id, category, action, target, item, source}`. The
`Idempotency-Key` header carries the same id; if both are present and
disagree we reject rather than guess which task it is.

| status | meaning |
|---|---|
| `accepted` | the governor cleared it, the state machine has it |
| `refused` | the governor said no. `reason` is its own wording |
| `unavailable` | we could not attempt it |

**A refusal is HTTP 200, not 4xx, and that is deliberate.** Her
`requestJson` throws on any non-2xx and replaces the body with a fixed
string about keys and quota. The governor's actual words would be lost.
A refusal is a successful safety decision, not a transport failure.
Non-2xx is kept for what it means to her code: a malformed request.

Measured, against a live governor:

```
target left_arm    -> accepted | clear
target above_head  -> refused  | "above the head ceiling"
```

The second one never set the arming flag. That is the property worth
testing, not the status string.

## POST /api/stop

Shares nothing. No lock, no cache, no governor, no config. It sets the
same flag the projector's SPACE key sets, where a stop already wins over
everything else in the same tick. It works with a malformed body,
because a stop that depends on parsing one is a stop a bad body can
block.

It is also not idempotency-cached. Replaying a cached "ok" would mean a
re-sent stop stopped nothing. Stopping twice is free; stopping zero
times is not.

## GET /api/vitals

```json
{"status": "disconnected", "readings": null}
```

No vitals sensor is wired. Her page already renders this honestly as a
dash and "No readings yet", and her adapter returns the same shape in
demo mode. This is not a stub waiting for a plausible number. In a care
product an invented heart rate is the worst bug available.

## What this does not claim

It proves a command survives the trip from her UI, through the guard,
into the state machine. It does not claim an arm moved. No motor has
been observed moving, and this file does not change that.

The coordinates each of her words maps to come from `py/arm.py`'s own
HOME and box. They are real, reachable, judgeable points. They are not a
guess at where a person's arm is, which only vision can answer.
