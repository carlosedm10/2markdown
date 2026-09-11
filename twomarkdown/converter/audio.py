"""Optional local audio transcription via faster-whisper."""

from __future__ import annotations

from pathlib import Path

AUDIO_SUFFIXES = frozenset({".wav", ".mp3"})

_model = None


class AudioTranscriptionError(Exception):
    """Raised when local Whisper transcription cannot run."""


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_SUFFIXES


def convert_audio(path: Path, *, model_size: str = "tiny") -> str:
    """Transcribe wav/mp3 locally. Requires extra ``audio-whisper``."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AudioTranscriptionError(
            "local Whisper requires: make uv-sync EXTRA=audio-whisper"
        ) from exc

    global _model
    if _model is None:
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")

    segments, _info = _model.transcribe(str(path.resolve()))
    text = " ".join(segment.text.strip() for segment in segments).strip()
    if not text:
        raise AudioTranscriptionError(f"whisper produced no text: {path.name}")
    return f"## Transcript\n\n{text}\n"
