import json
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set_ocr_mode.py"
_SPEC = importlib.util.spec_from_file_location("set_ocr_mode", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
apply_mode = _MOD.apply_mode
llm_enabled = _MOD.llm_enabled
load_mode = _MOD.load_mode
write_mode = _MOD.write_mode


class TestSetOcrMode:
    def test_ollama_enables_llm_and_backend(self) -> None:
        data = apply_mode({}, "ollama")
        assert data["backend"] == "ollama"
        assert data["llm_enabled"] is True
        assert llm_enabled(data) is True

    def test_ollama_updates_vision_model(self) -> None:
        data = apply_mode({}, "ollama", vision_model="llava")
        assert data["vision_model"] == "ollama:llava"

    def test_tesseract_disables_llm(self) -> None:
        enabled = apply_mode({}, "ollama")
        data = apply_mode(enabled, "tesseract")
        assert data["backend"] == "tesseract"
        assert data["llm_enabled"] is False
        assert llm_enabled(data) is False

    def test_write_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / ".ocr-mode"
        write_mode(apply_mode({}, "ollama", "moondream"), path)
        loaded = load_mode(path)
        assert loaded["backend"] == "ollama"
        assert llm_enabled(loaded) is True
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["vision_model"] == "ollama:moondream"
