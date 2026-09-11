"""Tests for the text-only markdown judge (twomarkdown.judge)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from twomarkdown import judge as judge_module
from twomarkdown.judge import Flag, format_review_queue, judge_markdown, judge_tree

_LONG_BODY = "Contenido de ejemplo suficientemente largo para no ser omitido. " * 3


def _stub_completion(monkeypatch, reply: str | None) -> None:
    """Replace the network call with a fixed reply, no HTTP involved."""
    monkeypatch.setattr(judge_module, "_request_completion", lambda *a, **k: reply)


class TestJudgeMarkdownParsing:
    """judge_markdown() — a well-formed JSON reply becomes a Flag."""

    def test_parses_well_formed_json_reply(self, monkeypatch) -> None:
        """judge_markdown() — quote/issue/suggestion map onto Flag fields."""
        reply = json.dumps(
            [
                {
                    "quote": "H + g + 4 = 0",
                    "issue": "4 is not a constant of integration here",
                    "suggestion": "H + g + C = 0",
                }
            ]
        )
        _stub_completion(monkeypatch, reply)
        text = f"## Page 1\n\nSolución general: H + g + 4 = 0. {_LONG_BODY}"

        assert judge_markdown(text) == [
            Flag(
                page=1,
                quoted_text="H + g + 4 = 0",
                suggestion="H + g + C = 0",
                reason="4 is not a constant of integration here",
            )
        ]

    def test_multiple_flags_in_one_reply(self, monkeypatch) -> None:
        """judge_markdown() — a reply with several objects yields several Flags."""
        reply = json.dumps(
            [
                {"quote": "a", "issue": "bad a", "suggestion": ""},
                {"quote": "b", "issue": "bad b", "suggestion": "b-fixed"},
            ]
        )
        _stub_completion(monkeypatch, reply)
        flags = judge_markdown(f"## Page 1\n\n{_LONG_BODY}")

        assert [f.quoted_text for f in flags] == ["a", "b"]
        assert flags[1].suggestion == "b-fixed"

    def test_clean_signal_yields_no_flags(self, monkeypatch) -> None:
        """judge_markdown() — the 'sin incidencias' clean signal means no flags."""
        _stub_completion(monkeypatch, "sin incidencias")
        assert judge_markdown(f"## Page 1\n\n{_LONG_BODY}") == []

    def test_empty_json_array_yields_no_flags(self, monkeypatch) -> None:
        """judge_markdown() — an explicit empty JSON array is also a clean signal."""
        _stub_completion(monkeypatch, "[]")
        assert judge_markdown(f"## Page 1\n\n{_LONG_BODY}") == []

    def test_json_wrapped_in_code_fence_is_parsed(self, monkeypatch) -> None:
        """judge_markdown() — a fenced ```json ...``` reply still parses."""
        payload = json.dumps([{"quote": "x", "issue": "bad x", "suggestion": ""}])
        _stub_completion(monkeypatch, f"```json\n{payload}\n```")
        flags = judge_markdown(f"## Page 1\n\n{_LONG_BODY}")

        assert flags == [Flag(page=1, quoted_text="x", suggestion="", reason="bad x")]


class TestGracefulDegradation:
    """judge_markdown() — replies a small model mangles never raise or crash."""

    def test_prose_reply_yields_no_flags(self, monkeypatch) -> None:
        """A model ignoring the JSON instruction degrades to no flags."""
        _stub_completion(monkeypatch, "This all looks fine to me, no concerns!")
        assert judge_markdown(f"## Page 1\n\n{_LONG_BODY}") == []

    def test_garbage_reply_yields_no_flags(self, monkeypatch) -> None:
        """Unparseable garbage degrades to no flags instead of raising."""
        _stub_completion(monkeypatch, "{not: valid json,,, ]]]")
        assert judge_markdown(f"## Page 1\n\n{_LONG_BODY}") == []

    def test_items_missing_required_keys_are_dropped(self, monkeypatch) -> None:
        """A flag object missing 'quote' or 'issue' is skipped, not crashed on."""
        reply = json.dumps(
            [
                {"quote": "x = 1"},
                {"issue": "no quote given"},
                {"quote": "y = 2", "issue": "valid entry", "suggestion": ""},
            ]
        )
        _stub_completion(monkeypatch, reply)
        flags = judge_markdown(f"## Page 1\n\n{_LONG_BODY}")

        assert [f.quoted_text for f in flags] == ["y = 2"]

    def test_none_reply_yields_no_flags(self, monkeypatch) -> None:
        """A None reply (network layer already failed) yields no flags."""
        _stub_completion(monkeypatch, None)
        assert judge_markdown(f"## Page 1\n\n{_LONG_BODY}") == []


class TestNetworkFailuresDoNotRaise:
    """judge_markdown() — timeouts and connection errors degrade, never crash."""

    def test_timeout_yields_no_flags(self, monkeypatch) -> None:
        """A request timeout must not raise out of judge_markdown()."""

        def raise_timeout(self, url, json=None, **kwargs):
            raise httpx.TimeoutException("timed out")

        monkeypatch.setattr(httpx.Client, "post", raise_timeout)
        assert judge_markdown(f"## Page 1\n\n{_LONG_BODY}") == []

    def test_connection_error_yields_no_flags(self, monkeypatch) -> None:
        """A dropped connection must not raise out of judge_markdown()."""

        def raise_conn_error(self, url, json=None, **kwargs):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx.Client, "post", raise_conn_error)
        assert judge_markdown(f"## Page 1\n\n{_LONG_BODY}") == []

    def test_bad_status_yields_no_flags(self, monkeypatch) -> None:
        """A non-2xx HTTP response degrades to no flags."""

        def fake_post(self, url, json=None, **kwargs):
            return httpx.Response(500, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        assert judge_markdown(f"## Page 1\n\n{_LONG_BODY}") == []

    def test_malformed_response_body_yields_no_flags(self, monkeypatch) -> None:
        """A 200 response shaped unlike a chat completion degrades to no flags."""

        def fake_post(self, url, json=None, **kwargs):
            return httpx.Response(
                200,
                json={"unexpected": "shape"},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        assert judge_markdown(f"## Page 1\n\n{_LONG_BODY}") == []


class TestPageSplitting:
    """judge_markdown() — flags are tagged with the page number of their section."""

    def test_flags_carry_their_own_page_number(self, monkeypatch) -> None:
        """Each '## Page N' section gets its own request and its own page tag."""
        replies = [
            json.dumps([{"quote": "a", "issue": "bad a", "suggestion": ""}]),
            json.dumps([{"quote": "b", "issue": "bad b", "suggestion": ""}]),
        ]
        calls: list[str] = []

        def fake(user_message, **kwargs):
            calls.append(user_message)
            return replies[len(calls) - 1]

        monkeypatch.setattr(judge_module, "_request_completion", fake)

        text = f"## Page 1\n\n{_LONG_BODY}\n\n## Page 2\n\n{_LONG_BODY}"
        flags = judge_markdown(text)

        assert [(f.page, f.quoted_text) for f in flags] == [(1, "a"), (2, "b")]
        assert len(calls) == 2

    def test_document_without_page_headings_is_treated_as_page_one(
        self, monkeypatch
    ) -> None:
        """Non-paginated markdown (no '## Page N') is still judged, as page 1."""
        _stub_completion(
            monkeypatch,
            json.dumps([{"quote": "x", "issue": "bad x", "suggestion": ""}]),
        )
        flags = judge_markdown(_LONG_BODY)

        assert [f.page for f in flags] == [1]


class TestShortSectionsSkipped:
    """judge_markdown() — sections with very little text never reach the model."""

    def test_short_section_is_never_sent_to_the_model(self, monkeypatch) -> None:
        """A near-empty page section is skipped without a network call."""
        called = False

        def fake(*args, **kwargs):
            nonlocal called
            called = True
            return "sin incidencias"

        monkeypatch.setattr(judge_module, "_request_completion", fake)

        assert judge_markdown("## Page 1\n\nOK.\n") == []
        assert called is False

    def test_long_section_is_sent_to_the_model(self, monkeypatch) -> None:
        """A section past the threshold does trigger a request."""
        called = False

        def fake(*args, **kwargs):
            nonlocal called
            called = True
            return "sin incidencias"

        monkeypatch.setattr(judge_module, "_request_completion", fake)

        judge_markdown(f"## Page 1\n\n{_LONG_BODY}")
        assert called is True


class TestFormatReviewQueue:
    """format_review_queue() — renders flags grouped by file, then by page."""

    def test_empty_results_reports_nothing_flagged(self) -> None:
        """No flagged files produces a short, clearly empty report."""
        output = format_review_queue({})
        assert "No files were flagged" in output

    def test_groups_by_file_then_page_in_order(self) -> None:
        """Pages appear in ascending order regardless of input order."""
        results = {
            Path("notes/topic3.md"): [
                Flag(page=2, quoted_text="x", suggestion="y", reason="why-2"),
                Flag(page=1, quoted_text="z", suggestion="", reason="why-1"),
            ],
        }
        output = format_review_queue(results)

        assert "notes/topic3.md" in output
        assert output.index("Page 1") < output.index("Page 2")
        assert output.index("why-1") < output.index("why-2")

    def test_suggestion_line_omitted_when_blank(self) -> None:
        """A Flag with no suggestion does not print a dangling 'Suggested fix'."""
        results = {
            Path("a.md"): [Flag(page=1, quoted_text="z", suggestion="", reason="w")],
        }
        output = format_review_queue(results)
        assert "Suggested fix" not in output

    def test_never_touches_the_source_files(self, tmp_path: Path) -> None:
        """format_review_queue() only returns text; it must not write anything."""
        source = tmp_path / "topic.md"
        original = "## Page 1\n\nH + g + 4 = 0\n"
        source.write_text(original)

        flag = Flag(page=1, quoted_text="H + g + 4 = 0", suggestion="", reason="w")
        format_review_queue({source: [flag]})

        assert source.read_text() == original


class TestJudgeTree:
    """judge_tree() — walks a *_2markdown directory, skipping generated files."""

    def test_skips_dot_dirs_and_the_review_queue(self, tmp_path, monkeypatch) -> None:
        """.unzipped/ members and review-queue.md are never judged as input."""
        root = tmp_path / "doc_2markdown"
        root.mkdir()
        (root / "doc.md").write_text('---\nlanguage: "es"\n---\n\n## Page 1\n\ntexto\n')

        hidden = root / ".unzipped"
        hidden.mkdir()
        (hidden / "inner.md").write_text("## Page 1\n\ntexto\n")

        (root / "review-queue.md").write_text("# Review queue\n")

        seen_languages: list[str | None] = []

        def fake_judge_markdown(text, *, language=None, **kwargs):
            seen_languages.append(language)
            return [Flag(page=1, quoted_text="q", suggestion="", reason="r")]

        monkeypatch.setattr(judge_module, "judge_markdown", fake_judge_markdown)

        results = judge_tree(root)

        assert list(results.keys()) == [root / "doc.md"]
        assert seen_languages == ["es"]

    def test_files_with_no_flags_are_excluded_from_results(
        self, tmp_path, monkeypatch
    ) -> None:
        """A judged file with an empty flag list does not appear in the mapping."""
        root = tmp_path / "doc_2markdown"
        root.mkdir()
        (root / "doc.md").write_text("## Page 1\n\ntexto\n")

        monkeypatch.setattr(judge_module, "judge_markdown", lambda *a, **k: [])

        assert judge_tree(root) == {}

    def test_unreadable_file_is_skipped_not_raised(self, tmp_path, monkeypatch) -> None:
        """An OSError reading one file must not abort the rest of the tree."""
        root = tmp_path / "doc_2markdown"
        root.mkdir()
        good = root / "good.md"
        good.write_text("## Page 1\n\ntexto\n")
        bad = root / "bad.md"
        bad.write_text("## Page 1\n\ntexto\n")

        real_read_text = Path.read_text

        def flaky_read_text(self, *args, **kwargs):
            if self.name == "bad.md":
                raise OSError("simulated read failure")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", flaky_read_text)
        monkeypatch.setattr(
            judge_module,
            "judge_markdown",
            lambda *a, **k: [Flag(page=1, quoted_text="q", suggestion="", reason="r")],
        )

        results = judge_tree(root)
        assert list(results.keys()) == [good]
