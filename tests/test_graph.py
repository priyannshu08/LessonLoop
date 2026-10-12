"""Unit tests for routing, merging, and best-attempt selection.

These tests use NO network calls. The previous version invoked the real
pipeline, which made the suite slow, costly, non-deterministic, and impossible
to run without an API key — so it was effectively never run. Control-flow logic
is exactly the part that should be tested deterministically.
"""

import pytest

from src.config import CHECKPOINT_NAMES
from src.graph import build_graph, route_after_evaluation, select_best
from src.nodes.evaluator import _merge
from src.schemas.evaluation import CheckpointResult, EvaluationResult


def checkpoint(name: str, passed: bool) -> CheckpointResult:
    return CheckpointResult(
        name=name, evidence="quoted text", reason="because", passed=passed
    )


def evaluation(failed: set[str]) -> EvaluationResult:
    checkpoints = [checkpoint(n, n not in failed) for n in CHECKPOINT_NAMES]
    return EvaluationResult(
        checkpoints=checkpoints,
        overall_pass=not failed,
        summary="summary",
    )


def snapshot(attempt: int, failed: set[str]) -> dict:
    return {
        "attempt_number": attempt,
        "full_lesson": f"lesson v{attempt}",
        "checkpoints": [
            {"name": n, "passed": n not in failed, "reason": "r", "evidence": "e"}
            for n in CHECKPOINT_NAMES
        ],
        "passed_count": len(CHECKPOINT_NAMES) - len(failed),
        "overall_pass": not failed,
        "evaluation_summary": "s",
        "metrics": {"word_count": 800},
    }


class TestRouting:
    """The loop must always terminate, and terminate at the right time."""

    def test_passing_evaluation_ships_immediately(self):
        state = {"evaluation": evaluation(set()), "attempt_number": 1, "max_attempts": 3}
        assert route_after_evaluation(state) == "select_best"

    def test_failure_with_attempts_remaining_retries(self):
        state = {
            "evaluation": evaluation({"accurate_and_grounded"}),
            "attempt_number": 1,
            "max_attempts": 3,
        }
        assert route_after_evaluation(state) == "compile_feedback"

    @pytest.mark.parametrize("attempt", [3, 4, 99])
    def test_exhausted_attempts_always_terminate(self, attempt):
        """Regression: routing once allowed max_attempts + 1 generations."""
        state = {
            "evaluation": evaluation({"accurate_and_grounded"}),
            "attempt_number": attempt,
            "max_attempts": 3,
        }
        assert route_after_evaluation(state) == "select_best"

    def test_missing_evaluation_still_terminates(self):
        """An evaluator exception must not spin the loop forever."""
        state = {"evaluation": None, "attempt_number": 3, "max_attempts": 3}
        assert route_after_evaluation(state) == "select_best"


class TestMerge:
    """Deterministic verdicts are authoritative and must never be lost."""

    def test_deterministic_failure_overrides_llm_pass(self):
        llm = [checkpoint(n, True) for n in CHECKPOINT_NAMES]
        det = [checkpoint("covers_key_points", False)]
        merged = {cp.name: cp.passed for cp in _merge(llm, det)}
        assert merged["covers_key_points"] is False

    def test_deterministic_failure_survives_llm_misnaming(self):
        """Regression: a misnamed LLM checkpoint used to swallow a hard failure."""
        llm = [checkpoint("key_concepts", True)]  # wrong name
        det = [checkpoint("covers_key_points", False)]
        merged = {cp.name: cp.passed for cp in _merge(llm, det)}
        assert merged["covers_key_points"] is False

    def test_unevaluated_checkpoints_fail_closed(self):
        """A checkpoint nobody judged must never count as a pass."""
        merged = _merge([], [])
        assert len(merged) == len(CHECKPOINT_NAMES)
        assert all(not cp.passed for cp in merged)

    def test_always_returns_exactly_six(self):
        llm = [checkpoint(n, True) for n in CHECKPOINT_NAMES]
        assert len(_merge(llm, [])) == 6


class TestSelectBest:
    """Refinement is not monotonic, so we select rather than take the last.

    `select_best` re-enriches the final attempt's snapshot from the live
    evaluation, so each fixture below keeps `state["evaluation"]` consistent
    with the final snapshot — as the real graph always does.
    """

    @staticmethod
    def build(snapshots: list[tuple[int, set]]) -> dict:
        last_attempt, last_failed = snapshots[-1]
        return {
            "attempt_number": last_attempt,
            "evaluation": evaluation(last_failed),
            "attempt_snapshots": [snapshot(n, f) for n, f in snapshots],
        }

    def test_prefers_earlier_attempt_when_later_regressed(self):
        state = self.build([
            (1, {"accurate_and_grounded"}),                                   # 5 passed
            (2, {"accurate_and_grounded", "teaches_by_example"}),              # 4 passed
            (3, {"accurate_and_grounded", "teaches_by_example",
                 "covers_key_points"}),                                # 3 passed
        ])
        result = select_best(state)
        assert result["selected_attempt"] == 1
        assert result["lesson_content"] == "lesson v1"

    def test_prefers_the_passing_attempt(self):
        state = self.build([(1, {"accurate_and_grounded"}), (2, set())])
        result = select_best(state)
        assert result["selected_attempt"] == 2
        assert result["lesson_content"] == "lesson v2"

    def test_ties_break_toward_the_earlier_attempt(self):
        state = self.build([(1, {"accurate_and_grounded"}), (2, {"accurate_and_grounded"})])
        assert select_best(state)["selected_attempt"] == 1

    def test_shipped_evaluation_describes_the_shipped_draft(self):
        """If we ship attempt 1, the saved evaluation must be attempt 1's."""
        state = self.build([
            (1, {"accurate_and_grounded"}),
            (2, {"accurate_and_grounded", "teaches_by_example", "covers_key_points"}),
        ])
        result = select_best(state)
        assert result["selected_attempt"] == 1
        failed = {cp.name for cp in result["evaluation"].checkpoints if not cp.passed}
        assert failed == {"accurate_and_grounded"}


def test_graph_compiles():
    assert build_graph() is not None
