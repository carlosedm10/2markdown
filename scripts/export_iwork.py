#!/usr/bin/env python3
"""Export Apple iWork documents to PDF using the Apple apps, on the host.

iCloud-synced ``.pages``/``.key``/``.numbers`` files carry only a first-page
``preview.jpg``; the real content lives in a proprietary IWA protobuf. The
converter container cannot read it and cannot drive the Apple apps, so the
complete fix is to export real PDFs here on macOS first, then convert those.

    python3 scripts/export_iwork.py "/path/to/folder"          # export missing
    python3 scripts/export_iwork.py "/path/to/folder" --force  # re-export all
    python3 scripts/export_iwork.py "/path/to/folder" --dry-run

Each ``Notes.pages`` becomes ``Notes.pdf`` beside it, which the normal
``make process`` run then picks up through the ordinary PDF pipeline.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

SUFFIX_TO_APP = {
    ".pages": "Pages",
    ".key": "Keynote",
    ".numbers": "Numbers",
}

# The app name must be a literal inside `tell`: with a variable, AppleScript cannot
# resolve the app's terminology and `as PDF` fails to compile. The app also needs
# `activate` and a moment before the document appears — `open` itself returns
# `missing value`, and `front document` is not yet set when it returns.
APPLESCRIPT_TEMPLATE = """
on run argv
    set srcPath to item 1 of argv
    set outPath to item 2 of argv
    tell application "{app}"
        activate
        open (POSIX file srcPath)
        set waited to 0
        repeat until (count of documents) > 0
            delay 0.5
            set waited to waited + 0.5
            if waited > 60 then error "document never opened: " & srcPath
        end repeat
        set theDoc to document 1
        export theDoc to (POSIX file outPath) as PDF
        close theDoc saving no
    end tell
end run
"""


# Driving a GUI app is inherently racy: exporting back-to-back, the next `open`
# can arrive while the app is still closing the previous document, and it answers
# "Operation not permitted". A short settle plus a retry clears it.
EXPORT_ATTEMPTS = 3
SETTLE_SECONDS = 1.5


def app_is_available(app: str) -> bool:
    """True when the iWork app is installed and scriptable on this Mac."""
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{app}" to get version'],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


def export_one(source: Path, target: Path, app: str, *, timeout: int = 180) -> None:
    last: Exception | None = None
    for attempt in range(EXPORT_ATTEMPTS):
        if attempt:
            time.sleep(SETTLE_SECONDS * (attempt + 1))
        try:
            subprocess.run(
                ["osascript", "-", str(source), str(target)],
                input=APPLESCRIPT_TEMPLATE.format(app=app),
                text=True,
                check=True,
                capture_output=True,
                timeout=timeout,
            )
            if target.exists():
                return
            last = RuntimeError("export reported success but produced no file")
        except subprocess.CalledProcessError as exc:
            last = exc
    assert last is not None
    raise last


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="file or folder to scan")
    parser.add_argument("--force", action="store_true", help="re-export existing PDFs")
    parser.add_argument("--dry-run", action="store_true", help="list without exporting")
    parser.add_argument(
        "--if-any",
        action="store_true",
        help="stay silent and succeed when there is nothing to export (used by make process)",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        if args.if_any:
            return 0
        print("This exporter needs macOS with Pages/Keynote/Numbers installed.")
        return 2

    root: Path = args.root.expanduser()
    if not root.exists():
        print(f"No such path: {root}")
        return 2

    if root.is_file():
        candidates = [root] if root.suffix.lower() in SUFFIX_TO_APP else []
    else:
        candidates = sorted(
            p
            for suffix in SUFFIX_TO_APP
            for p in root.rglob(f"*{suffix}")
            if not any(part.startswith(".") for part in p.relative_to(root).parts)
        )

    if not candidates:
        if not args.if_any:
            print("No iWork documents found.")
        return 0

    pending = [
        p for p in candidates if args.force or not p.with_suffix(".pdf").exists()
    ]
    if args.if_any:
        if not pending:
            return 0
        print(f":: export-iwork: {len(pending)} iWork document(s) -> PDF")

    exported = skipped = failed = 0
    missing_apps: set[str] = set()
    checked: dict[str, bool] = {}
    for source in candidates:
        target = source.with_suffix(".pdf")
        app = SUFFIX_TO_APP[source.suffix.lower()]

        if app not in checked:
            checked[app] = args.dry_run or app_is_available(app)
        if not checked[app]:
            missing_apps.add(app)
            failed += 1
            continue

        if target.exists() and not args.force:
            skipped += 1
            continue
        if args.dry_run:
            print(f"would export: {source.name} -> {target.name}")
            exported += 1
            continue

        print(f"exporting: {source.name} -> {target.name}", flush=True)
        try:
            export_one(source, target, app)
            exported += 1
            # Let the app settle before the next document.
            time.sleep(SETTLE_SECONDS)
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT: {source}", file=sys.stderr)
            failed += 1
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip().splitlines()
            print(f"  FAILED: {source}: {detail[-1] if detail else exc}", file=sys.stderr)
            failed += 1

    verb = "would export" if args.dry_run else "exported"
    print(f"\n{verb}={exported} skipped(existing)={skipped} failed={failed}")
    if missing_apps:
        names = ", ".join(sorted(missing_apps))
        print(f"Not installed or not scriptable on this Mac: {names}.")
        print("Install it from the App Store, or rely on the LibreOffice route")
        print("inside the container (make process handles iWork without this script).")
    elif failed:
        print("If macOS blocked the automation, allow Terminal to control")
        print("Pages/Keynote/Numbers in System Settings > Privacy & Security > Automation.")
    return 0 if args.if_any else (1 if failed else 0)


if __name__ == "__main__":
    raise SystemExit(main())
