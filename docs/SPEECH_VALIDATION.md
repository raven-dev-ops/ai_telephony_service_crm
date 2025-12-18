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
|  |  |  | roundtrip/samples |  |  |  |  |  |  |  |

