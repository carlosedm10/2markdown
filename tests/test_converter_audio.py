"""Tests for optional local Whisper transcription."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from twomarkdown.converter.audio import AudioTranscriptionError, convert_audio, is_audio


class TestAudioWhisper:
    def test_is_audio_for_wav_and_mp3(self) -> None:
        assert is_audio(Path("a.wav"))
        assert is_audio(Path("b.mp3"))
        assert not is_audio(Path("c.pdf"))

    def test_convert_audio_requires_extra(self, tmp_path: Path) -> None:
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"RIFF")
        with patch.dict("sys.modules", {"faster_whisper": None}):
            with pytest.raises(AudioTranscriptionError, match="audio-whisper"):
                convert_audio(wav)

    def test_convert_audio_joins_segments(self, tmp_path: Path) -> None:
        wav = tmp_path / "clip.wav"
        wav.write_bytes(b"RIFF")
        fake_model = MagicMock()
        seg1 = MagicMock(text=" Hello")
        seg2 = MagicMock(text=" world ")
        fake_model.transcribe.return_value = ([seg1, seg2], None)
        fake_mod = MagicMock()
        fake_mod.WhisperModel.return_value = fake_model
        with patch.dict("sys.modules", {"faster_whisper": fake_mod}):
            import twomarkdown.converter.audio as audio_mod

            audio_mod._model = None
            text = convert_audio(wav)
        assert "## Transcript" in text
        assert "Hello" in text
        assert "world" in text
