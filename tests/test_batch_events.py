"""`twomarkdown.batch.events`'s cost tracking: `add_cost`/`usd_unknown`.

See N16: a job that spent nothing (every cloud call priced, total genuinely
$0 — or no cloud calls at all) must be told apart from one where some call's
price could not be determined at all. `_usd_so_far` alone conflates both
into the same falsy `0.0`; `usd_unknown()` is the fix.
"""

from __future__ import annotations

from twomarkdown.batch import events


def _reset() -> None:
    """Same reset `set_sink` does on every call — used here to isolate each
    test from ordering, since `_usd_so_far`/`_unpriced_call_seen` are
    process-wide (see the module's own docstring)."""
    events.set_sink(None)


class TestUsdUnknown:
    def test_starts_unknown_false(self) -> None:
        _reset()
        assert events.usd_unknown() is False

    def test_a_priced_zero_call_stays_known(self) -> None:
        _reset()
        events.add_cost(0.0)
        assert events.usd_unknown() is False

    def test_a_priced_positive_call_stays_known(self) -> None:
        _reset()
        events.add_cost(0.002)
        assert events.usd_unknown() is False
        assert events._usd_so_far == 0.002

    def test_an_unpriced_call_flips_it_true(self) -> None:
        _reset()
        events.add_cost(None, unknown=True)
        assert events.usd_unknown() is True
        # Nothing to add for a call with no known price.
        assert events._usd_so_far == 0.0

    def test_set_sink_resets_both_for_the_next_job(self) -> None:
        _reset()
        events.add_cost(None, unknown=True)
        assert events.usd_unknown() is True

        events.set_sink(None)  # a fresh job's sink attach/detach

        assert events.usd_unknown() is False
        assert events._usd_so_far == 0.0
