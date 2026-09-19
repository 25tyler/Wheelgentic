# carechair voice + care dashboard

A light-blue care dashboard with a small Node.js voice pipeline. Plain browser JavaScript, Node built-ins, no npm dependencies, no robot movement implementation.

## Run

Requires Node.js 22 or later.

1. Copy `.env.example` to `.env` and set `DEEPGRAM_API_KEY`, `META_API_KEY`, and optionally `TOKEN_COMPANY_API_KEY`.
2. Run `npm run dev` and open http://127.0.0.1:5173.
3. Use the inline **Talk to carechair** panel on Overview. Click **Start microphone**, speak, then **Stop microphone**. Recording automatically finishes after 20 seconds. You can also type a message.
4. Replies play aloud automatically through Deepgram Aura-2. Use **Voice replies on/off** to mute automatic playback or **Listen again** to replay. If the browser blocks autoplay, click **Listen again**. Starting a new recording or leaving Overview stops speech.
5. Run `npm test` for mocked integration and error-path tests; `npm run check` for syntax checks.

`.env` is ignored by Git and never served over HTTP. Restart the server after changing it. The current machine has local credentials configured; they are not included in this repository. Other teammates must supply their own environment.

## Flow

Browser MediaRecorder → POST /api/transcribe → Deepgram Nova-3 → transcript shown in frontend → POST /api/command → optional older-history compression → Meta structured JSON → server validation → robot adapter → transcript, a brief understanding summary, and the assistant reply shown inline → POST /api/speak → Deepgram Aura-2 → MP3 playback.

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

Simple stop/pause phrases bypass Meta and compression, even if those providers are down. The inline voice panel has one microphone toggle: Start microphone begins recording, Stop microphone finishes and submits it. It has no separate robot pause/stop buttons. A stop invalidates earlier interpretations still awaiting dispatch. A command already delivered to the robot cannot be recalled by this app: the backend must enforce stop ordering and physical safety. This voice stop is not a hardware emergency stop.

## API / teammate integration

| Endpoint | Input | Behavior |
| --- | --- | --- |
| `POST /api/transcribe` | Raw audio, Content-Type audio/webm, audio/mp4, audio/ogg or audio/wav | Returns `{transcript}`; 5 MB limit |
| `POST /api/speak` | `{text}` (1–1,000 characters) | Returns `audio/mpeg`; uses the server-side Deepgram key |
| `POST /api/command` | `{transcript, history:[{role,content}]}` | Interprets, validates, dispatches through adapter, returns transcript/command/understanding/suggestion/response/delivery/compression |
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

Meta uses the current Model API at `META_API_URL` (default `https://api.meta.ai/v1/chat/completions`) with `META_MODEL=muse-spark-1.3`. The older `api.llama.com` endpoint rejected this machine's Model API key with HTTP 401. The request uses strict JSON output, minimal reasoning, and a 1,600-token completion budget covering both reasoning and the JSON answer. The response is read from `choices[0].message.content`; the legacy `completion_message.content` shape is also understood. Credentials remain in `META_API_KEY` on the server.

## Modules

- `voice-client.js`: inline microphone, transcript/understanding/reply display, suggestions, typed input, session history
- `voice-speech.js`: speech requests, MP3 playback, mute/interruption and resource cleanup
- `voice-providers.js`: Deepgram, Meta and Token Company HTTP clients
- `voice-commands.js`: schema, validation, routing prompt, direct controls
- `voice-service.js`: pipeline coordination and pending-command cancellation
- `robot-adapter.js`: demo/live task, stop and vitals boundary
- `voice-config.js`: environment configuration
- `server.js`: local HTTP routes and static allowlist
- `voice.test.js`, `voice-client.test.js`, and `voice-speech.test.js`: 23 API, microphone, suggestion and playback tests with no credentials or external network required

## Verification on this machine

- Automated integration tests pass using provider fixtures; these verify plumbing and failure behavior, not live Meta classification quality.
- Live Deepgram transcribed its public sample audio successfully.
- Live The Token Company compressed synthetic history successfully.
- Live Deepgram Aura-2 returned MP3 audio for a spoken reply. The inline frontend also transcribed a microphone request and answered conversationally.
- Live Meta checks passed for drinking water, washing a specific arm again, prescribed medication assistance, a shower suggestion, ordinary conversation, accepting an eating offer, and declining to recommend extra pills for a symptom.
- Live Meta Model API accepted the locally configured key: "Take a bath." returned `showering/start/body` in the frontend; water and medication requests classified correctly through `/api/command`; vitals and a negated washing request stayed conversation with no task dispatched. The earlier 401 came from using the older Llama endpoint. Robot delivery remains simulated until the backend is connected.

Provider references: [Deepgram prerecorded audio](https://developers.deepgram.com/docs/pre-recorded-audio), [Meta Model API chat completions](https://dev.meta.ai/docs/protocols/chat-completions), [Meta structured output](https://dev.meta.ai/docs/structured-output), [The Token Company SDK](https://github.com/TheTokenCompany/the-token-company-node).


## Care interface

The inline voice panel keeps provider configuration, structured JSON, compression details, and delivery mode out of the user interface. The API still returns those fields for integration. Prepared task replies say the request is ready, without claiming movement or completion. Vitals show an empty state until readings are available. Shower check-ins record an already completed shower, like the meal tracker; older synthetic bathing records are excluded from the displayed journal, counts, and report. Original stored records are preserved.

## Conversation and spoken replies

Meta returns a brief `understanding` summary and an optional `suggestion` (`eating`, `showering`, `take_meds`, or `none`) alongside the five existing command fields. These UI fields are validated separately and never added to robot payloads. Understanding describes the user's need; it is not a reasoning trace. Ordinary conversation stays `talk_to_me/none`. Hunger or wanting to freshen up can produce an offer, but an offer never dispatches a robot task. An explicit request or an unambiguous acceptance of a single recent offer can dispatch one of the three allowed task categories. Medication suggestions are limited to a user-mentioned prescribed routine; symptoms are not grounds to recommend a dose.

Spoken replies use the same final text shown on screen. Set optional `DEEPGRAM_TTS_MODEL` (default `aura-2-thalia-en`) to change the voice. Synthesis happens server-side with the existing `DEEPGRAM_API_KEY`; MP3 audio is kept in memory, responses use `no-store`, and browser object URLs are revoked after playback. Speech failures leave the text reply available. Autoplay is best-effort; blocked playback offers a user-initiated retry.

Text-to-speech reference: [Deepgram Aura REST API](https://developers.deepgram.com/docs/text-to-speech).
