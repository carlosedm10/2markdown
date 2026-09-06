import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "set_ocr_mode.py"
_SPEC = importlib.util.spec_from_file_location("set_ocr_mode", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
apply_mode = _MOD.apply_mode
llm_enabled = _MOD.llm_enabled

SAMPLE = """
# --- OCR mode (rewritten by scripts/set_ocr_mode.py via make build) ---
OCR_BACKEND: Literal["tesseract", "ollama"] = "tesseract"
LLM_ENABLED = False
OLLAMA_VISION_MODEL = "ollama:moondream"
# --- end OCR mode ---
"""


class TestSetOcrMode:
    def test_ollama_enables_llm_and_backend(self) -> None:
        text = apply_mode(SAMPLE, "ollama")
        assert 'OCR_BACKEND: Literal["tesseract", "ollama"] = "ollama"' in text
        assert "LLM_ENABLED = True" in text
        assert llm_enabled(text) is True

    def test_ollama_updates_vision_model(self) -> None:
        text = apply_mode(SAMPLE, "ollama", vision_model="llava")
        assert 'OLLAMA_VISION_MODEL = "ollama:llava"' in text

    def test_tesseract_disables_llm(self) -> None:
        enabled = apply_mode(SAMPLE, "ollama")
        text = apply_mode(enabled, "tesseract")
        assert 'OCR_BACKEND: Literal["tesseract", "ollama"] = "tesseract"' in text
        assert "LLM_ENABLED = False" in text
        assert llm_enabled(text) is False

    def test_repo_config_py_matches_rewrite_patterns(self) -> None:
        config = Path(__file__).resolve().parents[1] / "src" / "config.py"
        text = config.read_text(encoding="utf-8")
        ollama = apply_mode(text, "ollama", vision_model="llava")
        assert llm_enabled(ollama) is True
        restored = apply_mode(ollama, "tesseract")
        assert llm_enabled(restored) is False
        assert 'OCR_BACKEND: Literal["tesseract", "ollama"] = "tesseract"' in restored
