"""The constraint budget — a build-time guard against prompt bloat.

WHY THIS TEST EXISTS
--------------------
A previous version of the generator prompt carried 39 simultaneous constraints.
It got there honestly: every one was added in response to a real failure, and
each individually made sense. But instruction-following degrades measurably past
roughly ten simultaneous constraints — the model starts triaging which rules to
obey, and the survivors are the most recent or most specific rather than the most
important. Retries stopped converging as a direct result.

The memory system makes this worse by design: its whole job is to add a rule when
something fails. Capping the RULE COUNT is not enough, because the base prompt can
bloat independently. So the budget is enforced on the assembled prompt, in a test,
and the build fails if it is exceeded.

This is the structural answer to "won't the prompt just grow forever?" — it
cannot, because CI says no.
"""

import re

import pytest

from src.config import MEMORY_POLICY, RUBRIC
from src.nodes.generator import _build_system_prompt


def count_constraints(prompt: str) -> int:
    """Count instruction-like statements in a prompt.

    An honest proxy, not a perfect measure: it cannot see a single sentence that
    smuggles in three requirements. A crude bound that always runs beats a
    perfect one that never does.
    """
    numbered = len(re.findall(r"^\s*\d+\.\s+\S", prompt, re.MULTILINE))
    bullets = len(re.findall(r"^\s*[-*]\s+\S", prompt, re.MULTILINE))
    return numbered + bullets


def build(rules: list[str], attempt: int = 1) -> str:
    return _build_system_prompt(
        learner_profile="test reader",
        lessons_learned=rules,
        attempt_number=attempt,
        previous_lesson="",
        evaluation=None,
        snapshots=[],
    )


class TestConstraintBudget:

    def test_base_prompt_is_within_budget(self):
        prompt = build([])
        n = count_constraints(prompt)
        assert n <= RUBRIC.max_prompt_constraints, (
            f"Generator prompt has {n} constraints, budget is "
            f"{RUBRIC.max_prompt_constraints}. Instruction-following degrades "
            f"past ~10. Remove something before adding something."
        )

    def test_budget_holds_with_memory_full(self):
        """Even at maximum memory, the assembled prompt must fit the budget."""
        rules = [f"Learned rule number {i}." for i in range(MEMORY_POLICY.max_active_rules)]
        n = count_constraints(build(rules))
        assert n <= RUBRIC.max_prompt_constraints, (
            f"Base prompt plus {MEMORY_POLICY.max_active_rules} memory rules "
            f"gives {n} constraints, over the {RUBRIC.max_prompt_constraints} "
            f"budget. Either the base prompt or the rule cap must shrink."
        )

    def test_memory_cap_leaves_room_for_the_base_prompt(self):
        """The two caps must be mutually consistent, not set independently."""
        base = count_constraints(build([]))
        assert base + MEMORY_POLICY.max_active_rules <= RUBRIC.max_prompt_constraints, (
            f"Base prompt ({base}) + max rules ({MEMORY_POLICY.max_active_rules}) "
            f"exceeds the budget ({RUBRIC.max_prompt_constraints})."
        )


class TestRevisionPrompt:
    """A retry must NOT re-send the rulebook."""

    def test_revision_omits_the_base_rules(self):
        """The fix must not compete with every standing instruction.

        Re-sending all the rules on a retry is what made revisions fail to land:
        the specific fix became one demand among many, and lost.
        """
        from src.schemas.evaluation import CheckpointResult, EvaluationResult

        evaluation = EvaluationResult(
            checkpoints=[
                CheckpointResult(
                    name="covers_key_points", evidence="quote",
                    reason="How it works is missing.", passed=False,
                )
            ] + [
                CheckpointResult(name=n, evidence="q", reason="fine", passed=True)
                for n in ["accurate_and_grounded", "beginner_friendly_language",
                          "teaches_by_example", "no_unexplained_jargon",
                          "coherent_teaching_flow"]
            ],
            overall_pass=False,
            summary="one failure",
        )
        revision = _build_system_prompt(
            learner_profile="test reader",
            lessons_learned=[],
            attempt_number=2,
            previous_lesson="THE PREVIOUS DRAFT",
            evaluation=evaluation,
            snapshots=[],
        )
        assert "THE PREVIOUS DRAFT" in revision, "revision must include the draft"
        assert "covers_key_points" in revision, "revision must name the failure"
        assert "How it works is missing" in revision, "revision must carry the reason"
        # The five passing checkpoints must not be recapped — that is noise.
        assert revision.count("fine") == 0, "passing checkpoints must not be echoed"


@pytest.mark.parametrize("attempt", [1, 2, 3])
def test_prompt_never_exceeds_budget_at_any_attempt(attempt):
    rules = [f"Rule {i}." for i in range(MEMORY_POLICY.max_active_rules)]
    assert count_constraints(build(rules, attempt)) <= RUBRIC.max_prompt_constraints
