# Wheelgentic voice + care dashboard

A light-blue care dashboard with a small Node.js voice pipeline. Plain browser JavaScript, Node built-ins, no npm dependencies, no robot movement implementation.

## Run

Requires Node.js 22 or later.

1. Copy `.env.example` to `.env` and set `DEEPGRAM_API_KEY`, `META_API_KEY`, and optionally `TOKEN_COMPANY_API_KEY`.
2. Run `npm run dev` and open http://127.0.0.1:5173.
3. Open **Talk to me**, click **Start microphone**, allow microphone access, speak, then click **Finish recording**. Recording automatically finishes after 20 seconds. The typed input uses the same intent pipeline.
4. Run `npm test` for mocked integration and error-path tests; `npm run check` for syntax checks.

`.env` is ignored by Git and never served over HTTP. Restart the server after changing it. The current machine has local credentials configured; they are not included in this repository. Other teammates must supply their own environment.

## Flow

Browser MediaRecorder → POST /api/transcribe → Deepgram Nova-3 → transcript shown in frontend → POST /api/command → optional older-history compression → Meta structured JSON → server validation → robot adapter → assistant response and selected category shown in frontend.

This is turn-based recording, not an always-listening stream. Compressed audio is uploaded with its real MIME type; no ffmpeg, PCM conversion, or exposed browser API key is needed. Audio is held in memory for the request and not written by the server. Voice conversation history lives in browser memory for this page session. Daily care logs remain separate in localStorage. A voice request never falsely marks a meal, shower, or medication as completed.

## Intent contract

```json
{
  "category": "showering",
  "action": "repeat",
  "target": "right_arm",
  "item": "none",
  "response": "You would like your right arm washed again."
}
```

- `category`: `eating`, `showering`, `take_meds`, `talk_to_me`
- `action`: `start`, `repeat`, `pause`, `stop`, `none`
- `target`: `left_arm`, `right_arm`, `both_arms`, `body`, `none`
- `item`: `food`, `water`, `medication`, `none`

Only valid category/action/target/item combinations are accepted. Extra fields, arbitrary functions, medication dosages, and motor coordinates are rejected. Water maps to eating. Vitals and other unsupported requests map to talk_to_me, not a robot task. Past activity, negated requests, ambiguity, or multiple tasks prompt conversation/clarification. Medication repeat commands are not supported.

Simple stop/pause phrases and dedicated buttons bypass Meta and compression, even if those providers are down. A stop invalidates earlier interpretations still awaiting dispatch. A command already delivered to the robot cannot be recalled by this app: the backend must enforce stop ordering and physical safety. This voice stop is not a hardware emergency stop.

## API / teammate integration

| Endpoint | Input | Behavior |
| --- | --- | --- |
| `POST /api/transcribe` | Raw audio, Content-Type audio/webm, audio/mp4, audio/ogg or audio/wav | Returns `{transcript}`; 5 MB limit |
| `POST /api/command` | `{transcript, history:[{role,content}]}` | Interprets, validates, dispatches through adapter, returns transcript/command/response/delivery/compression |
| `POST /api/task` | `{command: <validated intent object>}` | Explicit task adapter boundary |
| `POST /api/stop` | `{action:"stop"}` or `{action:"pause"}` | Bypasses AI; forwards control through adapter |
| `GET /api/vitals` | None | Demo returns disconnected/null; live adapter can proxy teammate readings |
| `GET /api/voice/status` | None | Boolean credential presence and robot mode only; not a provider health check |

Default `ROBOT_MODE=demo` prepares payloads and returns `delivery.status="simulated"`. No robot endpoint is contacted. To connect later, explicitly set `ROBOT_MODE=live`, `ROBOT_BACKEND_URL=http://127.0.0.1:8000`, and optional `ROBOT_API_KEY`.

The adapter sends the following payload to the teammate's `POST /api/task` or `POST /api/stop`:

```json
{
  "command_id": "generated-uuid",
  "category": "showering",
  "action": "start",
  "target": "left_arm",
  "item": "none",
  "source": "voice"
}
```

The same UUID is sent as `Idempotency-Key`. The backend should deduplicate it and return a 2xx JSON acknowledgment, e.g. `{ "accepted": true }`. Non-2xx, timeout, or invalid JSON are treated as failures. There are no automatic robot retries. Accepted means receipt, never completion. The backend owns motion planning, interlocks, medication eligibility, and stop semantics; this application only forwards intent.

The local server binds only to 127.0.0.1, rejects foreign origins and unexpected Host headers, and serves only frontend assets. It has no multiuser authentication; do not expose it publicly without adding authentication, TLS, request limits and a backend authorization policy. For a phone demo, localhost on the phone is not this laptop; deployment requires an HTTPS host and adapting the origin policy.

## Compression and provider behavior

The Token Company compresses only older history over 3,000 characters. The system instructions, current transcript, and last four turns stay intact. No key, short history, or `TOKEN_COMPANY_ENABLED=false` skips compression. Compression failures use original bounded context. Meta/Deepgram failures surface in the UI and do not fabricate success. Transcripts are limited to 1,000 characters; history to 20 messages of 1,000 characters each. API timeouts are 15 seconds (compression 5 seconds).

Meta uses `META_API_URL` (default `https://api.llama.com/v1/chat/completions`) and configurable `META_MODEL`. It supports Meta's `completion_message.content` text object as well as an OpenAI-compatible choices envelope.

## Modules

- `voice-client.js`: microphone lifecycle, transcript display, typed fallback, pause/stop, session history
- `voice-providers.js`: Deepgram, Meta and Token Company HTTP clients
- `voice-commands.js`: schema, validation, routing prompt, direct controls
- `voice-service.js`: pipeline coordination and pending-command cancellation
- `robot-adapter.js`: demo/live task, stop and vitals boundary
- `voice-config.js`: environment configuration
- `server.js`: local HTTP routes and static allowlist
- `voice.test.js` and `voice-client.test.js`: 16 API and microphone-lifecycle tests with no credentials or external network required

## Verification on this machine

- Automated integration tests pass using provider fixtures; these verify plumbing and failure behavior, not live Meta classification quality.
- Live Deepgram transcribed its public sample audio successfully.
- Live The Token Company compressed synthetic history successfully.
- Live Meta rejected the supplied key with HTTP 401. Update `META_API_KEY` in `.env` and restart to enable real classification. No substitute AI or guessed successful classification is used.

Provider references: [Deepgram prerecorded audio](https://developers.deepgram.com/docs/pre-recorded-audio), [Meta official SDK and API schema](https://github.com/meta-llama/llama-api-typescript), [The Token Company SDK](https://github.com/TheTokenCompany/the-token-company-node).

