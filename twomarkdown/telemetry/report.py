"""Self-contained HTML and PDF export reports."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

import fitz

REPORT_HTML = "2markdown-report.html"
REPORT_PDF = "2markdown-report.pdf"


def _esc(value: object) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _pct_bar(pct: float, color: str) -> str:
    width = max(0.0, min(pct, 100.0))
    return (
        f'<div class="bar"><span style="width:{width:.1f}%;background:{color}"></span>'
        f"<em>{width:.1f}%</em></div>"
    )


def _status_label(status: str) -> str:
    return {
        "ok": "Exported",
        "failed": "Failed",
        "skipped": "Skipped",
    }.get(status, status)


def _ms(value: object) -> str:
    if value is None:
        return "—"
    try:
        ms = float(value)
    except (TypeError, ValueError):
        return "—"
    if ms >= 1000:
        return f"{ms / 1000:.2f}s"
    return f"{ms:.0f}ms"


def _rel(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _short_path(path: str, output_dir: str) -> str:
    for prefix in (output_dir,):
        if path.startswith(prefix):
            return path[len(prefix) :].lstrip("/")
    parts = path.split("/")
    if len(parts) > 4:
        return "/".join(["…", *parts[-3:]])
    return path


def render_html(summary: dict[str, Any]) -> str:
    run = summary.get("this_run") or {}
    reliability = summary.get("reliability") or {}
    timing = summary.get("timing") or {}
    tools = summary.get("tools") or []
    stages = (summary.get("stages") or [])[:12]
    files = summary.get("files") or []
    failed = [row for row in files if row.get("status") == "failed"]
    mean_rel = reliability.get("mean")
    success = float(run.get("success_rate") or 0) * 100
    accent = "#0f766e"

    tool_rows = ""
    palette = ["#0f766e", "#1d4ed8", "#a16207", "#be123c", "#6d28d9", "#334155"]
    for index, tool in enumerate(tools):
        color = palette[index % len(palette)]
        tool_rows += (
            f"<tr><td><code>{_esc(tool.get('name'))}</code></td>"
            f"<td>{int(tool.get('count') or 0)}</td>"
            f"<td>{_pct_bar(float(tool.get('pct') or 0), color)}</td></tr>"
        )
    if not tool_rows:
        tool_rows = "<tr><td colspan='3'>No converter/OCR events this run.</td></tr>"

    stage_rows = ""
    max_stage = max((float(s.get("duration_ms") or 0) for s in stages), default=1.0)
    for stage in stages:
        ms = float(stage.get("duration_ms") or 0)
        pct = 100.0 * ms / max_stage if max_stage else 0
        stage_rows += (
            f"<tr><td><code>{_esc(stage.get('name'))}</code></td>"
            f"<td>{_ms(ms)}</td>"
            f"<td>{_pct_bar(pct, accent)}</td></tr>"
        )
    if not stage_rows:
        stage_rows = "<tr><td colspan='3'>No timed stages.</td></tr>"

    file_rows = ""
    output_dir = str(summary.get("output_dir") or "")
    for row in files:
        status = str(row.get("status") or "")
        twomarkdown.= _short_path(str(row.get("source") or ""), output_dir)
        file_rows += (
            f'<tr class="st-{_esc(status)}">'
            f"<td>{_esc(_status_label(status))}</td>"
            f"<td><code>{_esc(twomarkdown.}</code></td>"
            f"<td>{_esc(row.get('converter') or '—')}</td>"
            f"<td>{_esc(row.get('ocr_backend') or '—')}</td>"
            f"<td>{_ms(row.get('duration_ms'))}</td>"
            f"<td>{_rel(row.get('reliability'))}</td>"
            f"<td>{_esc(row.get('char_count') if row.get('char_count') is not None else '—')}</td>"
            f"</tr>"
        )
        if row.get("error"):
            file_rows += (
                f'<tr class="err"><td></td><td colspan="6">'
                f"{_esc(row.get('error'))}</td></tr>"
            )
    if not file_rows:
        file_rows = "<tr><td colspan='7'>No files in the manifest.</td></tr>"

    fail_block = ""
    if failed:
        items = "".join(
            f"<li><code>{_esc(row.get('source'))}</code> — {_esc(row.get('error') or 'error')}</li>"
            for row in failed
        )
        fail_block = f'<section class="card fail"><h2>Failures</h2><ul>{items}</ul></section>'

    mean_rel_label = _rel(mean_rel) if mean_rel is not None else "—"
    cfg = summary.get("config") or {}
    cfg_bits = " · ".join(f"{_esc(k)}={_esc(v)}" for k, v in cfg.items())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>2markdown export report</title>
<style>
:root {{ --bg:#f4f1ea; --ink:#1c1917; --muted:#57534e; --card:#fffcf7; --line:#e7e0d4; --ok:#0f766e; --bad:#be123c; --skip:#a16207; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:15px/1.5 "Iowan Old Style", Palatino, "Palatino Linotype", serif; color:var(--ink); background:var(--bg); }}
header {{ padding:2.2rem 8vw 1.2rem; border-bottom:1px solid var(--line); background:#1c1917; color:#fafaf9; }}
header p {{ color:#d6d3d1; margin:0.4rem 0 0; }}
h1 {{ font-size:1.8rem; font-weight:600; margin:0; letter-spacing:-0.02em; }}
main {{ padding:1.6rem 8vw 4rem; max-width:1100px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:0.8rem; margin:1.2rem 0 1.6rem; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:0.9rem 1rem; }}
.kpi b {{ display:block; font-size:1.45rem; font-family:ui-sans-serif, system-ui, sans-serif; }}
.kpi span {{ color:var(--muted); font-size:0.82rem; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:1.1rem 1.2rem; margin:0 0 1.1rem; }}
h2 {{ font-size:1.05rem; margin:0 0 0.8rem; }}
table {{ width:100%; border-collapse:collapse; font-family:ui-sans-serif, system-ui, sans-serif; font-size:0.86rem; }}
th, td {{ text-align:left; padding:0.45rem 0.35rem; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:600; }}
code {{ font-size:0.82rem; }}
.bar {{ position:relative; height:0.7rem; background:#efe8dc; border-radius:99px; overflow:hidden; min-width:6rem; }}
.bar span {{ display:block; height:100%; }}
.bar em {{ display:none; }}
.st-ok td:first-child {{ color:var(--ok); font-weight:600; }}
.st-failed td:first-child {{ color:var(--bad); font-weight:600; }}
.st-skipped td:first-child {{ color:var(--skip); font-weight:600; }}
.err td {{ color:var(--bad); font-size:0.8rem; border-bottom:1px solid var(--line); }}
.fail {{ border-color:#fecdd3; }}
.note {{ color:var(--muted); font-size:0.85rem; }}
@media print {{
  body {{ background:#fff; }}
  header {{ background:#111; -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
  .card, .kpi {{ break-inside:avoid; }}
}}
</style>
</head>
<body>
<header>
  <h1>2markdown export report</h1>
  <p>{_esc(summary.get("generated_at"))} · {_esc(summary.get("input_dir"))}</p>
</header>
<main>
  <section class="kpis">
    <div class="kpi"><b>{int(run.get("converted") or 0)}</b><span>Exported</span></div>
    <div class="kpi"><b>{int(run.get("failed") or 0)}</b><span>Failed</span></div>
    <div class="kpi"><b>{int(run.get("skipped") or 0)}</b><span>Skipped</span></div>
    <div class="kpi"><b>{success:.0f}%</b><span>Success rate</span></div>
    <div class="kpi"><b>{mean_rel_label}</b><span>Mean reliability</span></div>
    <div class="kpi"><b>{_ms(summary.get("wall_ms"))}</b><span>Wall time</span></div>
  </section>
  <section class="card">
    <h2>Reliability</h2>
    <p>{_esc(reliability.get("how"))}</p>
    <p class="note">Timed files: {int(timing.get("file_count_timed") or 0)} · mean {_ms(timing.get("mean_file_ms"))} · slowest {_ms(timing.get("max_file_ms"))}</p>
  </section>
  <section class="card">
    <h2>Tools this run</h2>
    <table><thead><tr><th>Tool</th><th>Uses</th><th>Share</th></tr></thead><tbody>{tool_rows}</tbody></table>
  </section>
  <section class="card">
    <h2>Where time went</h2>
    <p class="note">Inclusive span totals (a parent stage also includes its children). Use this to spot OCR vs MarkItDown vs cleanup.</p>
    <table><thead><tr><th>Stage</th><th>Time</th><th>Relative</th></tr></thead><tbody>{stage_rows}</tbody></table>
  </section>
  {fail_block}
  <section class="card">
    <h2>Files</h2>
    <table>
      <thead><tr><th>Status</th><th>Source</th><th>Converter</th><th>OCR</th><th>Time</th><th>Reliability</th><th>Chars</th></tr></thead>
      <tbody>{file_rows}</tbody>
    </table>
  </section>
  <p class="note">{cfg_bits}</p>
</main>
</body>
</html>
"""


def write_html(summary: dict[str, Any], output_dir: Path) -> Path:
    path = output_dir / REPORT_HTML
    path.write_text(render_html(summary), encoding="utf-8")
    return path


def write_pdf(summary: dict[str, Any], output_dir: Path) -> Path:
    path = output_dir / REPORT_PDF
    run = summary.get("this_run") or {}
    reliability = summary.get("reliability") or {}
    files = summary.get("files") or []
    stages = (summary.get("stages") or [])[:10]
    tools = summary.get("tools") or []

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 48.0

    def ensure_space(need: float = 40.0) -> None:
        nonlocal page, y
        if y + need < 800:
            return
        page = doc.new_page(width=595, height=842)
        y = 48.0

    def line(text: str, *, size: float = 11, color: tuple[float, ...] = (0.1, 0.1, 0.1), indent: float = 48) -> None:
        nonlocal y
        ensure_space(18)
        page.insert_text((indent, y), text[:110], fontsize=size, color=color)
        y += size + 6

    line("2markdown export report", size=18)
    line(str(summary.get("generated_at") or ""), size=9, color=(0.35, 0.35, 0.35))
    line(str(summary.get("input_dir") or ""), size=9, color=(0.35, 0.35, 0.35))
    y += 8
    mean_rel = reliability.get("mean")
    mean_label = f"{float(mean_rel) * 100:.0f}%" if mean_rel is not None else "—"
    success = float(run.get("success_rate") or 0) * 100
    line(
        f"Exported {run.get('converted', 0)}  ·  Failed {run.get('failed', 0)}  ·  "
        f"Skipped {run.get('skipped', 0)}  ·  Success {success:.0f}%  ·  "
        f"Reliability {mean_label}  ·  Wall {_ms(summary.get('wall_ms'))}",
        size=10,
    )
    y += 10
    line("Tools", size=13)
    if tools:
        for tool in tools[:12]:
            line(
                f"  {tool.get('name')}  {tool.get('count')} uses  ({tool.get('pct')}%)",
                size=10,
            )
    else:
        line("  (none recorded this run)", size=10)
    y += 8
    line("Slowest stages", size=13)
    if stages:
        for stage in stages:
            line(
                f"  {stage.get('name')}  {_ms(stage.get('duration_ms'))}",
                size=10,
            )
    else:
        line("  (none)", size=10)
    y += 8
    line("Files", size=13)
    for row in files:
        status = _status_label(str(row.get("status") or ""))
        twomarkdown.= str(row.get("source") or "")
        if len(twomarkdown. > 70:
            twomarkdown.= "…" + twomarkdown.-69:]
        line(
            f"  {status:10}  {_ms(row.get('duration_ms')):>8}  {_rel(row.get('reliability')):>4}  {twomarkdown.",
            size=8,
        )
        if row.get("error"):
            line(f"             {row.get('error')}", size=8, color=(0.7, 0.1, 0.2))

    doc.save(path)
    doc.close()
    return path
