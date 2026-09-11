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
from pathlib import Path

SUFFIX_TO_APP = {
    ".pages": "Pages",
    ".key": "Keynote",
    ".numbers": "Numbers",
}

# The app name must be a literal inside `tell`: with a variable, AppleScript cannot
# resolve the app's terminology and `as PDF` fails to compile. So the name is
# interpolated into the template while paths stay as arguments.
APPLESCRIPT_TEMPLATE = """
on run argv
    set srcPath to item 1 of argv
    set outPath to item 2 of argv
    tell application "{app}"
        open (POSIX file srcPath)
        -- `open` returns missing value here, so take the document it just fronted.
        repeat 60 times
            if (count of documents) > 0 then exit repeat
            delay 0.5
        end repeat
        set doc to front document
        export doc to (POSIX file outPath) as PDF
        close doc saving no
    end tell
end run
"""


def export_one(source: Path, target: Path, app: str, *, timeout: int = 180) -> None:
    subprocess.run(
        ["osascript", "-", str(source), str(target)],
        input=APPLESCRIPT_TEMPLATE.format(app=app),
        text=True,
        check=True,
        capture_output=True,
        timeout=timeout,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="file or folder to scan")
    parser.add_argument("--force", action="store_true", help="re-export existing PDFs")
    parser.add_argument("--dry-run", action="store_true", help="list without exporting")
    args = parser.parse_args()

    if sys.platform != "darwin":
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
        print("No iWork documents found.")
        return 0

    exported = skipped = failed = 0
    for source in candidates:
        target = source.with_suffix(".pdf")
        app = SUFFIX_TO_APP[source.suffix.lower()]

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
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT: {source}", file=sys.stderr)
            failed += 1
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip().splitlines()
            print(f"  FAILED: {source}: {detail[-1] if detail else exc}", file=sys.stderr)
            failed += 1

    verb = "would export" if args.dry_run else "exported"
    print(f"\n{verb}={exported} skipped(existing)={skipped} failed={failed}")
    if failed:
        print("Grant Terminal permission to control Pages/Keynote/Numbers in")
        print("System Settings > Privacy & Security > Automation, then retry.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
