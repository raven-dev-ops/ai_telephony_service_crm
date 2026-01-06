# Speech Validation (STT/TTS) - Issue #104

This repo includes a repeatable harness for validating speech providers and collecting latency/error baselines for:

- STT latency (audio -> transcript)
- Assistant latency (transcript -> reply text)
- TTS latency (reply text -> audio)

## Provider configuration

### OpenAI

Set:

- `SPEECH_PROVIDER=openai`
- `OPENAI_API_KEY=...`

Optional:

- `OPENAI_API_BASE` (default `https://api.openai.com/v1`)
- `OPENAI_STT_MODEL` (default `gpt-4o-mini-transcribe`)
- `OPENAI_TTS_MODEL` (default `gpt-4o-mini`)
- `OPENAI_TTS_VOICE` (default `alloy`)

### Google Cloud (GCP)

Set:

- `SPEECH_PROVIDER=gcp`
- One of:
  - `GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json` (local), or
  - Application Default Credentials (Cloud Run/GCE)

Optional:

- `GCP_SPEECH_LANGUAGE_CODE` (default `en-US`)
- `GCP_STT_MODEL` (default `default`)
- `GCP_TTS_VOICE` (optional; full voice name, e.g. `en-US-Standard-C`)
- `GCP_TTS_AUDIO_ENCODING` (default `MP3`)
- `GCP_SPEECH_TIMEOUT_SECONDS` (default `12`)

Notes:
- The Google STT path supports common encodings (WAV/LINEAR16, MP3, FLAC, OGG/Opus) via best-effort detection.
- For **samples mode**, use `.wav` files for predictable results.
- When using local Application Default Credentials, set the quota project to avoid 403s:
  `gcloud auth application-default set-quota-project <project-id>`.

## Running the harness

The harness runs in-process (no server required):

### Roundtrip mode (default)

Synthesizes known phrases with TTS, then transcribes them with STT.

From repo root:

```bash
python backend/scripts/speech_validation.py --mode roundtrip --runs 5 --output-json speech-report.json
```

Add or override phrases:

```bash
python backend/scripts/speech_validation.py --mode roundtrip --phrase "John Test" --phrase "123 Main Street, Merriam Kansas"
```

Relax expected transcript matching (fuzzy similarity threshold):

```bash
python backend/scripts/speech_validation.py --mode roundtrip --match-threshold 0.94
```

Default matching is exact (normalized) at `1.0`; lower the threshold to tolerate minor drift.

### Samples mode

Provide `.wav` samples (recommended for accent/noise testing). By default it looks in:

- `backend/assets/speech_samples/*.wav`

Run:

```bash
python backend/scripts/speech_validation.py --mode samples --samples-dir backend/assets/speech_samples --runs 3
```

Optional manifest for expected transcripts (JSON array):

```json
[
  {"file": "name.wav", "expected": "John Test"},
  {"file": "address.wav", "expected": "123 Main Street Merriam Kansas"}
]
```

Then:

```bash
python backend/scripts/speech_validation.py --mode samples --samples-dir backend/assets/speech_samples --manifest backend/assets/speech_samples/manifest.json --runs 3
```

## Recording baselines

Run the harness in staging with representative audio samples and record:

- Provider + region
- Sample set (accent/noise conditions)
- p50/p95/p99 for STT, NLU, TTS, and Total
- Empty transcript rate
- Expected mismatch rate (when using a manifest)

Store JSON reports as build artifacts or attach them to the issue/PR discussion.

## Baseline results (staging)

Fill this in after running the harness in staging:

| Date (UTC) | Provider | Region | Mode | Runs | p50 Total (ms) | p95 Total (ms) | p99 Total (ms) | Empty transcript % | Mismatch % | Notes |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 2026-01-06 | gcp | us-central1 | roundtrip | 5 | 4406.88 | 4531.09 | 4531.09 | 0.00 | 33.33 | Local ADC with quota project; 3 phrases, 5 runs; match threshold 1.0 |

## Twilio streaming validation (staging)

Twilio voice flow modes:

- Default: `<Gather input="speech">` when `TWILIO_STREAMING_ENABLED=false` (Twilio handles speech recognition).
- Streaming: `<Start><Stream>` when `TWILIO_STREAMING_ENABLED=true` (Twilio streams audio to `TWILIO_STREAM_BASE_URL`).

Current staging config (2026-01-06):

- `SPEECH_PROVIDER=gcp` (ADC)
- `TWILIO_STREAMING_ENABLED` not set (streaming disabled)

Gaps before production STT validation:

- `/v1/twilio/voice-stream` is an HTTP POST endpoint expecting `transcript`; there is no WebSocket handler for Twilio Media Streams.
- Twilio Media Streams send 8k mu-law audio; `speech_service` auto-detects WAV/MP3/FLAC/OGG only, so mu-law needs conversion or explicit handling.

Validation steps once streaming ingest exists:

1) Deploy a WebSocket ingest that converts Twilio media frames to base64 audio and calls the STT provider.
2) Set `TWILIO_STREAMING_ENABLED=true` and `TWILIO_STREAM_BASE_URL=wss://<ingest-host>/v1/twilio/voice-stream` in staging.
3) Place test calls and record transcript quality, latency, and no-input handling.

Twilio streaming results (staging):

| Date (UTC) | Provider | Ingest | Outcome | Notes |
|---|---|---|---|---|
| TBD | gcp/openai | TBD | TBD | Awaiting streaming ingest |

