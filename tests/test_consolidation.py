"""Tests for the two-tier memory policy.

Every test here corresponds to an observed failure of an earlier design:
one run wrote up to three permanent rules with only exact-string dedup, which
produced 20 rules containing four incompatible sentence-length limits — and
measured output quality fell as memory grew.

The escalation tests cover a bug that would otherwise have shipped: a dropped
rule left its signature uncovered, the same observations regenerated the same
rule, and the system oscillated forever.

No network calls. The LLM stages (distil, reconcile) are exercised separately;
what is tested here is the POLICY — what may enter memory, and what leaves.
"""

import pytest

from src.config import MEMORY_POLICY
from src.memory.consolidation import (
    enforce_capacity,
    find_promotable_patterns,
    review_probation,
)
from src.memory.signatures import failure_signature
from src.memory.store import MemoryStore


@pytest.fixture
def memory(tmp_path):
    return MemoryStore(
        lessons_file=tmp_path / "lessons.json",
        history_file=tmp_path / "history.json",
        evolution_file=tmp_path / "evolution.json",
    )


def fail(memory, run_id, topic, checkpoint, reason, attempts=1):
    memory.record_observations(
        run_id=run_id,
        topic=topic,
        rejection_log=[
            {"attempt_number": i + 1, "failure_reasons": {checkpoint: reason}}
            for i in range(attempts)
        ],
    )


JARGON = ("no_unexplained_jargon", "terms appear with no explanation")


class TestSignatures:
    """Recurrence can only be counted if the same cause yields the same label."""

    def test_same_cause_different_wording_shares_a_signature(self):
        a = failure_signature("no_unexplained_jargon", "3 terms appear with no explanation")
        b = failure_signature("no_unexplained_jargon", "1 term appears with no explanation")
        assert a == b == "no_unexplained_jargon::undefined_terms"

    def test_different_causes_are_distinguished(self):
        """One signature = one root cause = one fix. Anything needing a
        different fix must get a different signature."""
        missing = failure_signature("covers_key_points", "how it works is missing")
        short = failure_signature("covers_key_points", "only 90 words, too short")
        assert missing != short

    def test_unrecognised_reason_falls_back_to_general(self):
        assert failure_signature("coherent_teaching_flow", "odd") == \
            "coherent_teaching_flow::general"


class TestRecurrenceGate:
    """The brief says learn from REPEATED failures. One sample is not a pattern."""

    def test_promotion_requires_threshold_distinct_topics(self, memory):
        for i in range(MEMORY_POLICY.promotion_topic_threshold - 1):
            fail(memory, f"run{i}", f"Topic {i}", *JARGON)
        assert find_promotable_patterns(memory) == []

        fail(memory, "final", "Topic Final", *JARGON)
        assert [s for s, _ in find_promotable_patterns(memory)] == [
            "no_unexplained_jargon::undefined_terms"
        ]

    def test_same_topic_repeated_does_not_promote(self, memory):
        """THE GENERALISATION GUARD. Three failures on three RAG runs prove
        nothing about Photosynthesis — a rule distilled from them would be
        fitted to one subject. Topic diversity makes it universal by
        construction rather than by hoping the distiller writes something
        general."""
        for i in range(MEMORY_POLICY.promotion_topic_threshold + 2):
            fail(memory, f"run{i}", "RAG", *JARGON)
        assert find_promotable_patterns(memory) == []

    def test_one_run_failing_repeatedly_is_still_one_data_point(self, memory):
        """Regression: three failed attempts in ONE run must not self-promote."""
        fail(memory, "solo", "RAG", *JARGON, attempts=5)
        assert len(memory.get_observations()) == 5
        assert find_promotable_patterns(memory) == []

    def test_pattern_already_covered_is_not_promoted_again(self, memory):
        for i in range(MEMORY_POLICY.promotion_topic_threshold):
            fail(memory, f"run{i}", f"Topic {i}", *JARGON)
        memory.add_rule(
            insight="Explain every domain noun on first use.",
            signature="no_unexplained_jargon::undefined_terms",
            source_run_id="r", evidence_runs=["r"],
        )
        assert find_promotable_patterns(memory) == []


class TestEscalation:
    """A dropped rule must not regenerate itself forever."""

    def seed_recurrence(self, memory, signature_reason=JARGON):
        for i in range(MEMORY_POLICY.promotion_topic_threshold):
            fail(memory, f"run{i}", f"Topic {i}", *signature_reason)

    def drop_a_rule(self, memory, text):
        rid = memory.add_rule(
            text, "no_unexplained_jargon::undefined_terms", "r", ["a", "b", "c"]
        )
        for _ in range(MEMORY_POLICY.probation_runs):
            memory.record_rule_outcomes([rid], {"final_passed": False, "total_attempts": 3})
        return review_probation(memory)

    def test_failed_attempts_are_remembered(self, memory):
        """So the next attempt can be DIFFERENT rather than a re-derivation."""
        self.seed_recurrence(memory)
        self.drop_a_rule(memory, "first attempt at a fix")
        failed = memory.failed_fix_attempts("no_unexplained_jargon::undefined_terms")
        assert len(failed) == 1
        assert failed[0]["insight"] == "first attempt at a fix"

    def test_signature_stays_open_after_one_failed_fix(self, memory):
        """The failure is still real — only the rule was a bad attempt at it."""
        self.seed_recurrence(memory)
        self.drop_a_rule(memory, "first attempt")
        assert [s for s, _ in find_promotable_patterns(memory)] == [
            "no_unexplained_jargon::undefined_terms"
        ]

    def test_escalates_after_the_attempt_budget(self, memory):
        """Regression for the promote-drop-repromote loop. After enough
        different rules have failed, the honest conclusion is that prompting
        cannot fix this — so it stops and tells a human."""
        self.seed_recurrence(memory)
        for i in range(MEMORY_POLICY.max_fix_attempts_per_signature):
            self.drop_a_rule(memory, f"attempt {i}")

        signature = "no_unexplained_jargon::undefined_terms"
        assert memory.is_escalated(signature)
        assert find_promotable_patterns(memory) == [], "must stop retrying"

    def test_escalation_records_why(self, memory):
        self.seed_recurrence(memory)
        for i in range(MEMORY_POLICY.max_fix_attempts_per_signature):
            self.drop_a_rule(memory, f"attempt {i}")
        notes = memory.get_escalations()
        assert "no_unexplained_jargon::undefined_terms" in notes
        reason = notes["no_unexplained_jargon::undefined_terms"]["reason"].lower()
        assert "prompting" in reason and "human" in reason


class TestCapacity:
    """Diluted instructions stop being followed, so the rule set must be bounded."""

    def seed(self, memory, count, pass_rates):
        for i in range(count):
            rid = memory.add_rule(f"rule {i}", f"sig::{i}", "r", ["r"])
            memory.set_rule_status(rid, "active")
            for _ in range(MEMORY_POLICY.probation_runs):
                memory.record_rule_outcomes(
                    [rid], {"final_passed": pass_rates[i], "total_attempts": 1}
                )

    def test_cap_is_enforced(self, memory):
        over = MEMORY_POLICY.max_active_rules + 3
        self.seed(memory, over, [True] * over)
        enforce_capacity(memory)
        assert len(memory.get_active_rules()) == MEMORY_POLICY.max_active_rules

    def test_lowest_utility_is_evicted_first(self, memory):
        over = MEMORY_POLICY.max_active_rules + 1
        self.seed(memory, over, [False] + [True] * (over - 1))
        assert [r["id"] for r in enforce_capacity(memory)] == ["rule_001"]

    def test_new_rules_are_protected_from_eviction(self, memory):
        self.seed(memory, MEMORY_POLICY.max_active_rules, [True] * MEMORY_POLICY.max_active_rules)
        fresh = memory.add_rule("brand new", "sig::new", "r", ["r"])
        assert fresh not in [r["id"] for r in enforce_capacity(memory)]

    def test_nothing_is_ever_deleted(self, memory):
        over = MEMORY_POLICY.max_active_rules + 2
        self.seed(memory, over, [False] * over)
        enforce_capacity(memory)
        assert len(memory.get_all_rules()) == over
        assert len(memory.get_active_rules()) == MEMORY_POLICY.max_active_rules


class TestProbation:
    """Keep only if it improves — the discipline that makes this evolution."""

    def test_useless_rule_is_dropped(self, memory):
        rid = memory.add_rule("useless", "sig::x", "r", ["r"])
        for _ in range(MEMORY_POLICY.probation_runs):
            memory.record_rule_outcomes([rid], {"final_passed": False, "total_attempts": 3})
        _, dropped, _ = review_probation(memory)
        assert [r["id"] for r in dropped] == [rid]

    def test_helpful_rule_is_confirmed(self, memory):
        rid = memory.add_rule("helpful", "sig::y", "r", ["r"])
        for _ in range(MEMORY_POLICY.probation_runs):
            memory.record_rule_outcomes([rid], {"final_passed": True, "total_attempts": 1})
        confirmed, _, _ = review_probation(memory)
        assert [r["id"] for r in confirmed] == [rid]

    def test_rule_is_not_judged_before_probation_elapses(self, memory):
        rid = memory.add_rule("too early", "sig::z", "r", ["r"])
        memory.record_rule_outcomes([rid], {"final_passed": False, "total_attempts": 3})
        confirmed, dropped, _ = review_probation(memory)
        assert confirmed == [] and dropped == []


class TestLearningFromSuccess:
    """Recording only failures threw away half the signal."""

    def test_passes_are_recorded(self, memory):
        memory.record_observations(
            run_id="r1", topic="RAG", rejection_log=[],
            passed_signatures=["accurate_and_grounded", "coherent_teaching_flow"],
        )
        passes = [o for o in memory.get_observations() if o.get("outcome") == "pass"]
        assert len(passes) == 2

    def test_passes_do_not_count_toward_promotion(self, memory):
        memory.record_observations(
            run_id="r1", topic="RAG", rejection_log=[],
            passed_signatures=["accurate_and_grounded"] * 5,
        )
        assert find_promotable_patterns(memory) == []


class TestSupersedeChain:
    """Evolution must stay auditable, so retirement is a link, not a delete."""

    def test_superseded_rule_leaves_the_prompt(self, memory):
        old = memory.add_rule("old rule", "sig::a", "r", ["r"])
        memory.add_rule("new rule", "sig::a", "r", ["r"], supersedes=[old])
        active = memory.load_lessons_learned()
        assert "new rule" in active and "old rule" not in active

    def test_supersede_pointer_is_recorded_both_ways(self, memory):
        old = memory.add_rule("old rule", "sig::a", "r", ["r"])
        new = memory.add_rule("new rule", "sig::a", "r", ["r"], supersedes=[old])
        by_id = {r["id"]: r for r in memory.get_all_rules()}
        assert by_id[old]["superseded_by"] == new
        assert by_id[new]["supersedes"] == [old]
