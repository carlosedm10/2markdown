"""Convert RFC 822 .eml email files to markdown."""

from __future__ import annotations

import re
from email import policy
from email.parser import BytesParser
from pathlib import Path


def is_email(path: Path) -> bool:
    """Return True for .eml files; .msg stays on the MarkItDown path."""
    return path.suffix.lower() == ".eml"


def _decode_payload(part: object) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return str(raw or "")
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _header_value(msg: object, name: str) -> str:
    value = msg.get(name)
    if value is None:
        return ""
    return str(value).strip()


def _extract_body(msg: object) -> str:
    if msg.is_multipart():
        plain_parts: list[str] = []
        html_parts: list[str] = []
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain_parts.append(_decode_payload(part).strip())
            elif content_type == "text/html":
                html_parts.append(_decode_payload(part).strip())
        if plain_parts:
            return "\n\n".join(p for p in plain_parts if p)
        if html_parts:
            return _strip_html("\n\n".join(p for p in html_parts if p))
        return ""

    content_type = msg.get_content_type()
    body = _decode_payload(msg).strip()
    if content_type == "text/html":
        return _strip_html(body)
    return body


def _attachment_names(msg: object) -> list[str]:
    names: list[str] = []
    if not msg.is_multipart():
        return names
    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue
        filename = part.get_filename()
        if filename:
            names.append(str(filename))
    return names


def convert_eml(path: Path) -> str:
    """Convert a .eml file to markdown with YAML-like headers and body."""
    raw = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)

    lines = [
        f"From: {_header_value(msg, 'From')}",
        f"To: {_header_value(msg, 'To')}",
        f"Subject: {_header_value(msg, 'Subject')}",
        f"Date: {_header_value(msg, 'Date')}",
        "",
    ]

    body = _extract_body(msg)
    if body:
        lines.append(body)

    attachments = _attachment_names(msg)
    if attachments:
        lines.append("")
        lines.append("## Attachments")
        for name in attachments:
            lines.append(f"- {name}")

    return "\n".join(lines).strip()
