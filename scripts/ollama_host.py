#!/usr/bin/env python3
"""Manage host-native Ollama for 2markdown. Stdlib only — runs on the host."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
HEALTH_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/tags"
POLL_INTERVAL_S = 1.0
START_TIMEOUT_S = 30.0


def _is_healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_for_healthy(timeout_s: float = START_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _is_healthy():
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


def _require_ollama_cli() -> str:
    path = shutil.which("ollama")
    if not path:
        raise SystemExit(
            "Ollama CLI not found. Install from https://ollama.com and retry."
        )
    return path


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _start_macos() -> None:
    if shutil.which("open"):
        result = _run(["open", "-a", "Ollama"], check=False)
        if result.returncode == 0:
            return
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _start_linux() -> None:
    if shutil.which("systemctl"):
        result = _run(
            ["systemctl", "--user", "start", "ollama"],
            check=False,
        )
        if result.returncode == 0:
            return
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def cmd_start() -> None:
    _require_ollama_cli()
    if _is_healthy():
        print("Ollama is already running.")
        return

    system = platform.system()
    if system == "Darwin":
        _start_macos()
    elif system == "Linux":
        _start_linux()
    else:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    if _wait_for_healthy():
        print("Ollama started.")
        return
    raise SystemExit(
        "Ollama did not become ready in time. "
        "Start it manually and verify: curl http://127.0.0.1:11434/api/tags"
    )


def cmd_stop() -> None:
    system = platform.system()
    if system == "Darwin":
        _run(["killall", "Ollama"], check=False)
    elif system == "Linux" and shutil.which("systemctl"):
        _run(["systemctl", "--user", "stop", "ollama"], check=False)

    _run(["pkill", "-f", "ollama serve"], check=False)

    if _is_healthy():
        print("Warning: Ollama may still be running.")
    else:
        print("Ollama stopped.")


def cmd_ensure() -> None:
    _require_ollama_cli()
    if _is_healthy():
        print("Ollama is ready.")
        return
    print("Ollama is not running; starting...")
    cmd_start()


def cmd_pull(model: str) -> None:
    _require_ollama_cli()
    cmd_ensure()
    print(f"Pulling {model} (first run may take several minutes)...")
    _run(["ollama", "pull", model])
    print(f"Model ready: {model}")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            f"Usage: {sys.argv[0]} ensure|start|stop|pull <model>"
        )

    command = sys.argv[1]
    if command == "ensure":
        cmd_ensure()
    elif command == "start":
        cmd_start()
    elif command == "stop":
        cmd_stop()
    elif command == "pull":
        if len(sys.argv) != 3:
            raise SystemExit(f"Usage: {sys.argv[0]} pull <model>")
        cmd_pull(sys.argv[2])
    else:
        raise SystemExit(f"Unknown command: {command}")


if __name__ == "__main__":
    main()
