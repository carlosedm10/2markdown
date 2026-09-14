"""A blind proofreading pass over a page's transcription."""

from twomarkdown.agents.page_review import (
    describe_changes,
    is_light_proofreading,
    review_enabled,
    review_page,
)

RULES = "\n".join(
    [
        "# Tema 4",
        "",
        "- $\\int c \\cdot f(x) dx = c \\cdot \\int f(x) dx$",
        "- $\\int a^{f(x)} \\cdot f'(x) dx = \\frac{a^{f(x)}}{\\ln a} + C$",
        "- $\\int \\frac{f'(x)}{f(x)} dx = \\ln |f(x)| + C$",
    ]
)


class TestReviewEnabled:
    def test_no_model_means_no_pass(self, monkeypatch) -> None:
        """review_enabled() — the pass is opt-in; empty disables it."""
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "llm_enabled", True)
        monkeypatch.setattr(llm_config, "review_model", "")

        assert review_enabled() is False

    def test_a_model_turns_it_on(self, monkeypatch) -> None:
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "llm_enabled", True)
        monkeypatch.setattr(llm_config, "review_model", "openai:gpt-4o-mini")

        assert review_enabled() is True


class TestIsLightProofreading:
    """The reviewer never saw the page, so it may only fix characters."""

    def test_a_swapped_symbol_is_accepted(self, monkeypatch) -> None:
        """is_light_proofreading() — g for f on one line is the whole point."""
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "review_min_similarity", 0.97)
        fixed = RULES.replace("c \\cdot f(x)", "c \\cdot g(x)")

        assert is_light_proofreading(RULES, fixed)

    def test_an_added_rule_is_rejected(self, monkeypatch) -> None:
        """is_light_proofreading() — it cannot know what the page holds.

        Anything supplied without the page is invention, however plausible the
        missing rule looks from the ones around it.
        """
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "review_min_similarity", 0.97)
        invented = RULES + "\n- $\\int \\sec^2(x) dx = \\tan(x) + C$"

        assert not is_light_proofreading(RULES, invented)

    def test_a_removed_line_is_rejected(self, monkeypatch) -> None:
        """is_light_proofreading() — deleting the student's content is not a fix."""
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "review_min_similarity", 0.97)
        trimmed = "\n".join(RULES.splitlines()[:-1])

        assert not is_light_proofreading(RULES, trimmed)

    def test_rewording_within_the_same_line_count_is_rejected(
        self, monkeypatch
    ) -> None:
        """is_light_proofreading() — a reworded page keeps its shape.

        The line count alone would let it through, so similarity is checked too.
        """
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "review_min_similarity", 0.97)
        reworded = "\n".join(
            "- reglas de integración reescritas por completo aquí"
            if line.startswith("- ")
            else line
            for line in RULES.splitlines()
        )

        assert not is_light_proofreading(RULES, reworded)

    def test_an_empty_answer_is_rejected(self) -> None:
        """is_light_proofreading() — losing the page is the worst outcome."""
        assert not is_light_proofreading(RULES, "   ")


class TestDescribeChanges:
    """What the reviewer says it did is not evidence of what it did."""

    def test_changes_are_read_from_the_texts(self) -> None:
        """describe_changes() — the log reports the diff, not the claim.

        Asked on one page, a reviewer reported fixing a symbol that was already
        correct and reported a second change that never happened.
        """
        changes = describe_changes("- rule f\n- rule two\n", "- rule g\n- rule two\n")

        assert len(changes) == 1
        assert "rule f" in changes[0] and "rule g" in changes[0]

    def test_an_unchanged_page_reports_nothing(self) -> None:
        assert describe_changes(RULES, RULES) == []


class TestReviewPage:
    """However the pass fails, the transcription must survive it."""

    def test_disabled_review_returns_the_transcription(self, monkeypatch) -> None:
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "review_model", "")

        assert review_page(RULES) == (RULES, [])

    def test_a_failing_reviewer_does_not_lose_the_page(self, monkeypatch) -> None:
        """review_page() — an API error must cost nothing but the correction."""
        from twomarkdown.agents import page_review
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "llm_enabled", True)
        monkeypatch.setattr(llm_config, "review_model", "openai:gpt-4o-mini")
        monkeypatch.setattr(
            page_review, "_agent", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        assert review_page(RULES) == (RULES, [])

    def test_an_empty_transcription_is_not_sent(self, monkeypatch) -> None:
        """review_page() — nothing to proofread means no call and no cost."""
        from twomarkdown.agents import page_review
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "llm_enabled", True)
        monkeypatch.setattr(llm_config, "review_model", "openai:gpt-4o-mini")
        called: list[int] = []
        monkeypatch.setattr(page_review, "_agent", lambda: called.append(1) or None)

        assert review_page("  ") == ("  ", [])
        assert called == []

    def _stub(self, monkeypatch, markdown: str, changes: list[str]):
        import twomarkdown.agents.image_ocr as image_ocr
        from twomarkdown.agents import page_review
        from twomarkdown.config import llm_config

        monkeypatch.setattr(llm_config, "llm_enabled", True)
        monkeypatch.setattr(llm_config, "review_model", "openai:gpt-4o-mini")
        monkeypatch.setattr(llm_config, "review_min_similarity", 0.97)

        class _Result:
            output = page_review.PageReview(markdown=markdown, changes=changes)

        monkeypatch.setattr(page_review, "_agent", lambda: object())
        monkeypatch.setattr(
            image_ocr, "_call_with_backoff", lambda run, **kw: _Result()
        )

    def test_an_unchanged_answer_discards_the_claimed_changes(
        self, monkeypatch
    ) -> None:
        """review_page() — identical text means nothing was corrected.

        The model still lists corrections when asked what it corrected, and those
        would otherwise reach the log as work done.
        """
        self._stub(monkeypatch, RULES, ["fixed a missing sin", "adjusted a bracket"])

        assert review_page(RULES) == (RULES, [])

    def test_an_invented_rule_is_discarded(self, monkeypatch) -> None:
        """review_page() — the guard holds even when the model claims a fix."""
        invented = RULES + "\n- $\\int \\sec^2(x) dx = \\tan(x) + C$"
        self._stub(monkeypatch, invented, ["restored a missing rule"])

        assert review_page(RULES) == (RULES, [])

    def test_a_real_character_fix_is_kept_and_logged(self, monkeypatch) -> None:
        """review_page() — the case the pass exists for."""
        fixed = RULES.replace("c \\cdot f(x)", "c \\cdot g(x)")
        self._stub(monkeypatch, fixed, ["f -> g"])

        text, changes = review_page(RULES)

        assert text == fixed
        assert len(changes) == 1

    def test_out_of_credit_is_reported_as_a_soft_failure(self, monkeypatch) -> None:
        """review_page() — M5: an exhausted OpenAI balance must not read as
        plain success; the reviewer skipped the pass and that has to show up
        somewhere the job can surface, not only in the server's stderr."""
        from pydantic_ai.exceptions import ModelAPIError

        from twomarkdown.agents import page_review
        from twomarkdown.config import llm_config
        from twomarkdown.converter import ocr as ocr_mod

        monkeypatch.setattr(llm_config, "llm_enabled", True)
        monkeypatch.setattr(llm_config, "review_model", "openai:gpt-4o-mini")
        monkeypatch.setattr(
            page_review,
            "_agent",
            lambda: (_ for _ in ()).throw(
                ModelAPIError(
                    "openai:gpt-4o-mini",
                    "status_code: 429, body: {'type': 'insufficient_quota', "
                    "'code': 'credit_balance_exhausted'}",
                )
            ),
        )

        ocr_mod.begin_engine_record()
        assert review_page(RULES) == (RULES, [])

        reason = ocr_mod.soft_failure_reason()
        assert reason is not None
        assert "saldo agotado" in reason


class TestLatexCommandsSurfaced:
    """The reviewer knows \\arccot is not a command; it just does not notice."""

    def test_commands_are_listed_for_the_reviewer(self) -> None:
        """latex_commands_used() — extraction only, no opinion on validity.

        Encoding which names are real is the table this replaced: it went stale
        the moment a model invented a name nobody had listed.
        """
        from twomarkdown.agents.page_review import latex_commands_used

        text = r"- $\int -\frac{f'(x)}{1+f^2} dx = \arccot(f) + C$"

        assert latex_commands_used(text) == ["arccot", "frac", "int"]

    def test_prose_backslashes_are_not_commands(self) -> None:
        """latex_commands_used() — only maths spans, so C:\\temp is not a macro."""
        from twomarkdown.agents.page_review import latex_commands_used

        assert latex_commands_used(r"ruta C:\temp y $\alpha$") == ["alpha"]

    def test_display_maths_is_searched_too(self) -> None:
        from twomarkdown.agents.page_review import latex_commands_used

        assert latex_commands_used(r"$$\sum_{n=1}^{\infty} a_n$$") == ["infty", "sum"]

    def test_the_request_carries_the_list(self) -> None:
        """_review_request() — attention is directed at the names actually used."""
        from twomarkdown.agents.page_review import _review_request

        request = _review_request(r"$\arccot(x) + \int y$")

        assert r"\arccot" in request and r"\int" in request
        assert "operatorname" in request

    def test_text_without_maths_gets_no_checklist(self) -> None:
        """_review_request() — no formulas, nothing to verify, no wasted tokens."""
        from twomarkdown.agents.page_review import _review_request

        assert "LaTeX commands" not in _review_request("Solo prosa, sin formulas.")


class TestReviewerIsDeterministic:
    def test_the_reviewer_decodes_greedily(self) -> None:
        """The pass must give the same answer twice over the same text.

        Left sampling, it corrected a dropped prime on one run and missed it on
        the next, which makes a correction indistinguishable from noise.
        """
        import inspect

        from twomarkdown.agents import page_review

        source = inspect.getsource(page_review._agent)

        assert '"temperature": 0.0' in source
