"""Speech validation harness for STT/TTS providers.

Measures latency per step (STT -> NLU/response -> TTS) using either:
  - Roundtrip mode: synthesize known phrases with TTS, then transcribe them with STT
  - Samples mode: transcribe user-provided audio samples from a directory

This script is intended for local/staging validation and producing baseline numbers
for issue #104.

Examples (from repo root):
  python backend/scripts/speech_validation.py --mode roundtrip --runs 5
  python backend/scripts/speech_validation.py --mode samples --samples-dir backend/assets/speech_samples --runs 3
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import difflib
import json
import platform
import re
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _ensure_backend_on_path() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))


_ensure_backend_on_path()

from app.config import get_settings  # noqa: E402
from app.services import conversation, sessions  # noqa: E402


@dataclass(frozen=True)
class Sample:
    sample_id: str
    expected_text: str | None
    audio_b64: str | None
    text_fallback: str | None = None


def _normalize(text: str) -> str:
    lowered = (text or "").lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered


def _similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _expected_match(actual: str, expected: str, threshold: float) -> bool:
    norm_actual = _normalize(actual)
    norm_expected = _normalize(expected)
    if norm_actual == norm_expected:
        return True
    return _similarity(norm_actual, norm_expected) >= threshold


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        raise ValueError("empty values")
    idx = int(pct * (len(sorted_vals) - 1))
    return float(sorted_vals[idx])


def _stats(values_ms: list[float]) -> dict[str, Any]:
    if not values_ms:
        return {"count": 0}
    sorted_vals = sorted(values_ms)
    return {
        "count": len(sorted_vals),
        "avg_ms": round(statistics.mean(sorted_vals), 2),
        "p50_ms": round(statistics.median(sorted_vals), 2),
        "p95_ms": round(_percentile(sorted_vals, 0.95), 2),
        "p99_ms": round(_percentile(sorted_vals, 0.99), 2),
        "min_ms": round(sorted_vals[0], 2),
        "max_ms": round(sorted_vals[-1], 2),
    }


def _safe_speech_config(settings: Any) -> dict[str, Any]:
    speech = getattr(settings, "speech", None)
    if speech is None:
        return {}

    return {
        "provider": getattr(speech, "provider", None),
        "openai_api_base": getattr(speech, "openai_api_base", None),
        "openai_stt_model": getattr(speech, "openai_stt_model", None),
        "openai_tts_model": getattr(speech, "openai_tts_model", None),
        "openai_tts_voice": getattr(speech, "openai_tts_voice", None),
        "openai_api_key_configured": bool(getattr(speech, "openai_api_key", None)),
        "gcp_language_code": getattr(speech, "gcp_language_code", None),
        "gcp_stt_model": getattr(speech, "gcp_stt_model", None),
        "gcp_tts_voice": getattr(speech, "gcp_tts_voice", None),
        "gcp_tts_audio_encoding": getattr(speech, "gcp_tts_audio_encoding", None),
        "gcp_timeout_seconds": getattr(speech, "gcp_timeout_seconds", None),
    }


def _load_samples_from_dir(samples_dir: Path) -> list[Sample]:
    wavs = sorted(samples_dir.glob("*.wav"))
    samples: list[Sample] = []
    for wav in wavs:
        audio_b64 = base64.b64encode(wav.read_bytes()).decode("ascii")
        samples.append(
            Sample(sample_id=wav.name, expected_text=None, audio_b64=audio_b64)
        )
    return samples


def _load_samples_from_manifest(samples_dir: Path, manifest_path: Path) -> list[Sample]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("manifest must be a JSON array")
    samples: list[Sample] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("file") or "").strip()
        expected = item.get("expected")
        expected_text = str(expected).strip() if expected is not None else None
        if not rel:
            continue
        wav_path = samples_dir / rel
        audio_b64 = base64.b64encode(wav_path.read_bytes()).decode("ascii")
        samples.append(
            Sample(
                sample_id=rel,
                expected_text=expected_text,
                audio_b64=audio_b64,
            )
        )
    return samples


async def _build_roundtrip_samples(phrases: list[str]) -> list[Sample]:
    samples: list[Sample] = []
    for idx, phrase in enumerate(phrases, start=1):
        audio = await conversation.speech_service.synthesize(phrase)
        if not audio or audio.startswith("audio://"):
            samples.append(
                Sample(
                    sample_id=f"phrase_{idx}",
                    expected_text=phrase,
                    audio_b64=None,
                    text_fallback=phrase,
                )
            )
            continue
        samples.append(
            Sample(
                sample_id=f"phrase_{idx}",
                expected_text=phrase,
                audio_b64=audio,
            )
        )
    return samples


async def run_validation(
    samples: list[Sample],
    runs: int,
    business_id: str,
    voice: str | None,
    match_threshold: float,
) -> dict[str, Any]:
    health = await conversation.speech_service.health()
    settings = get_settings()
    diagnostics = conversation.speech_service.diagnostics()

    turn_results: list[dict[str, Any]] = []
    stt_ms: list[float] = []
    nlu_ms: list[float] = []
    tts_ms: list[float] = []
    total_ms: list[float] = []
    empty_transcripts = 0
    expected_mismatches = 0
    expected_checked = 0

    for run_idx in range(1, runs + 1):
        session = sessions.session_store.create(
            caller_phone="speech_validation",
            business_id=business_id,
            lead_source="speech_validation",
            channel="phone",
        )
        # Prime the conversation so the first measured turn is user input, not greeting.
        await conversation.conversation_manager.handle_input(session, None)

        for sample in samples:
            t0 = time.perf_counter()
            transcript: str | None = None
            stt_elapsed_ms: float | None = None
            stt_skipped = False
            stt_error: str | None = None
            if sample.audio_b64:
                try:
                    stt_t0 = time.perf_counter()
                    transcript = await conversation.speech_service.transcribe(
                        sample.audio_b64
                    )
                    stt_elapsed_ms = (time.perf_counter() - stt_t0) * 1000
                    stt_ms.append(stt_elapsed_ms)
                except Exception as exc:  # pragma: no cover - defensive
                    stt_error = f"{type(exc).__name__}: {exc}"
                    transcript = ""
            else:
                stt_skipped = True
                transcript = sample.text_fallback or ""

            if not transcript:
                empty_transcripts += 1

            expected_ok: bool | None = None
            if sample.expected_text is not None:
                expected_checked += 1
                expected_ok = _expected_match(
                    transcript,
                    sample.expected_text,
                    match_threshold,
                )
                if expected_ok is False:
                    expected_mismatches += 1

            nlu_t0 = time.perf_counter()
            result = await conversation.conversation_manager.handle_input(
                session, transcript
            )
            nlu_elapsed_ms = (time.perf_counter() - nlu_t0) * 1000
            nlu_ms.append(nlu_elapsed_ms)

            tts_t0 = time.perf_counter()
            audio_out = await conversation.speech_service.synthesize(
                result.reply_text, voice=voice
            )
            tts_elapsed_ms = (time.perf_counter() - tts_t0) * 1000
            tts_ms.append(tts_elapsed_ms)

            elapsed_ms = (time.perf_counter() - t0) * 1000
            total_ms.append(elapsed_ms)

            turn_results.append(
                {
                    "run": run_idx,
                    "sample_id": sample.sample_id,
                    "provider": getattr(settings.speech, "provider", "unknown"),
                    "stt_ms": round(stt_elapsed_ms, 2) if stt_elapsed_ms else None,
                    "nlu_ms": round(nlu_elapsed_ms, 2),
                    "tts_ms": round(tts_elapsed_ms, 2),
                    "total_ms": round(elapsed_ms, 2),
                    "stt_skipped": stt_skipped,
                    "stt_error": stt_error,
                    "transcript": transcript,
                    "expected_text": sample.expected_text,
                    "expected_ok": expected_ok,
                    "audio_out_placeholder": bool(
                        (audio_out or "").startswith("audio://")
                    ),
                }
            )

        sessions.session_store.end(session.id)

    report: dict[str, Any] = {
        "provider": getattr(settings.speech, "provider", "unknown"),
        "voice": voice,
        "business_id": business_id,
        "runs": runs,
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "speech_config": _safe_speech_config(settings),
        "match_threshold": match_threshold,
        "samples": [s.sample_id for s in samples],
        "health": health,
        "diagnostics": diagnostics,
        "counts": {
            "turns": len(turn_results),
            "empty_transcripts": empty_transcripts,
            "expected_checked": expected_checked,
            "expected_mismatches": expected_mismatches,
        },
        "latency_ms": {
            "stt": _stats(stt_ms),
            "nlu": _stats(nlu_ms),
            "tts": _stats(tts_ms),
            "total": _stats(total_ms),
        },
        "turns": turn_results,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Speech STT/TTS validation harness")
    parser.add_argument(
        "--mode",
        choices=["roundtrip", "samples"],
        default="roundtrip",
        help="Validation mode (default: roundtrip)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of runs to execute (default: 3)",
    )
    parser.add_argument(
        "--business-id",
        default="default_business",
        help="Business ID used for conversation manager context",
    )
    parser.add_argument(
        "--voice",
        default=None,
        help="Optional voice override for TTS (provider-specific)",
    )
    parser.add_argument(
        "--samples-dir",
        default="backend/assets/speech_samples",
        help="Directory of .wav samples (samples mode only)",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Optional JSON manifest mapping samples to expected transcripts",
    )
    parser.add_argument(
        "--phrase",
        action="append",
        default=None,
        help="Phrase to include in roundtrip mode (repeatable)",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write JSON report",
    )
    parser.add_argument(
        "--match-threshold",
        type=float,
        default=1.0,
        help=(
            "Minimum similarity (0-1) for expected transcript matches "
            "(default: 1.0 for exact normalized matching)"
        ),
    )
    args = parser.parse_args(argv)
    if args.match_threshold < 0 or args.match_threshold > 1:
        raise SystemExit("--match-threshold must be between 0 and 1")

    settings = get_settings()
    provider = getattr(settings.speech, "provider", "unknown")
    print(f"Speech provider: {provider}")

    if args.mode == "samples":
        samples_dir = Path(args.samples_dir)
        if args.manifest:
            samples = _load_samples_from_manifest(samples_dir, Path(args.manifest))
        else:
            samples = _load_samples_from_dir(samples_dir)
        if not samples:
            raise SystemExit(
                f"No .wav files found in {samples_dir}. "
                "Provide samples or use --mode roundtrip."
            )
    else:
        phrases = args.phrase or [
            "John Test",
            "123 Main Street, Merriam Kansas",
            "my water heater is leaking and I need service",
        ]
        samples = asyncio.run(_build_roundtrip_samples(phrases))

    report = asyncio.run(
        run_validation(
            samples=samples,
            runs=args.runs,
            business_id=args.business_id,
            voice=args.voice,
            match_threshold=args.match_threshold,
        )
    )

    counts = report["counts"]
    latency = report["latency_ms"]
    print(
        "Turns: "
        f"{counts['turns']} "
        f"(empty transcripts: {counts['empty_transcripts']}, "
        f"expected mismatches: {counts['expected_mismatches']}/{counts['expected_checked']})"
    )
    print("Latency (ms):")
    print(f"  STT:   {latency['stt']}")
    print(f"  NLU:   {latency['nlu']}")
    print(f"  TTS:   {latency['tts']}")
    print(f"  Total: {latency['total']}")

    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(f"Wrote report: {args.output_json}")

    return 0


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
