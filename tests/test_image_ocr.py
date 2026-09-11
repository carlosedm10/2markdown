"""Test cases for Ollama vision OCR (twomarkdown.agents.image_ocr)."""

import threading
import time
from unittest.mock import MagicMock, patch

from twomarkdown.agents import image_ocr


class TestOllamaVisionSerial:
    def test_ocr_image_bytes_llm_calls_do_not_overlap(self) -> None:
        current = 0
        max_seen = 0
        lock = threading.Lock()

        def slow_run(*_args: object, **_kwargs: object) -> MagicMock:
            nonlocal current, max_seen
            with lock:
                current += 1
                max_seen = max(max_seen, current)
            time.sleep(0.05)
            with lock:
                current -= 1
            result = MagicMock()
            result.output = "text"
            return result

        agent = MagicMock()
        agent.run_sync.side_effect = slow_run

        with (
            patch("twomarkdown.agents.image_ocr.llm_config.llm_enabled", True),
            patch(
                "twomarkdown.agents.image_ocr.prepare_image_for_vision_llm",
                return_value=(b"img", "image/png"),
            ),
            patch(
                "twomarkdown.agents.image_ocr.get_image_ocr_agent",
                return_value=agent,
            ),
        ):
            threads = [
                threading.Thread(target=image_ocr.ocr_image_bytes_llm, args=(b"x",))
                for _ in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        assert max_seen == 1
        assert agent.run_sync.call_count == 4
