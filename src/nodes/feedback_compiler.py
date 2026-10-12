"""Feedback Compiler — records what failed, and what changed since last time.

The generator's revision prompt is built in `src.nodes.generator` directly from
the evaluation, so this node no longer assembles a feedback *string*. Its job is
the audit trail:

- append a rejection-log entry (what failed, why)
- compute what CHANGED versus the previous attempt (what was fixed, what
  regressed, how the measurements moved)

The brief asks for "a rejection log — what failed, why, and what you changed on
retry". The first two are the evaluation; the third is computed here.

`checkpoints_regressed` is recorded deliberately rather than suppressed:
iterative refinement is not monotonic, and an audit log that hides a regression
misrepresents how the loop behaves.
"""

from src.schemas.evaluation import EvaluationResult, RejectionEntry

# Metrics worth showing a before/after for. Reading grade is included because it
# is useful context, even though it gates nothing.
TRACKED_METRICS = [
    ("word_count", "words", 0),
    ("long_sentence_ratio", "long-sentence ratio", 2),
    ("fk_grade", "reading grade", 1),
]


def _compute_deltas(
    snapshots: list[dict],
    attempt_number: int,
    current_failed: set[str],
    metrics: dict,
) -> dict:
    """Diff this attempt against the previous one."""
    previous = next(
        (s for s in snapshots if s.get("attempt_number") == attempt_number - 1), None
    )
    if previous is None or not previous.get("checkpoints"):
        return {}

    prev_failed = {c["name"] for c in previous["checkpoints"] if not c["passed"]}
    prev_metrics = previous.get("metrics", {})

    deltas = {}
    for key, label, places in TRACKED_METRICS:
        if key in prev_metrics and key in metrics:
            before, after = prev_metrics[key], metrics[key]
            deltas[key] = (
                f"{label}: {before:.{places}f} → {after:.{places}f} "
                f"({after - before:+.{places}f})"
            )

    return {
        "checkpoints_fixed": sorted(prev_failed - current_failed),
        "checkpoints_regressed": sorted(current_failed - prev_failed),
        "checkpoints_still_failing": sorted(prev_failed & current_failed),
        "metric_deltas": deltas,
    }


def _enrich(
    snapshots: list[dict],
    attempt_number: int,
    evaluation: EvaluationResult,
    metrics: dict,
    is_final: bool,
) -> list[dict]:
    """Fill checkpoint results and deltas into this attempt's snapshot."""
    checkpoint_data = [
        {
            "name": cp.name,
            "passed": cp.passed,
            "reason": cp.reason,
            "evidence": cp.evidence,
        }
        for cp in evaluation.checkpoints
    ]
    passed_count = sum(1 for cp in evaluation.checkpoints if cp.passed)
    current_failed = {cp.name for cp in evaluation.checkpoints if not cp.passed}
    deltas = _compute_deltas(snapshots, attempt_number, current_failed, metrics)

    updated = []
    for snap in snapshots:
        if snap["attempt_number"] == attempt_number:
            enriched = dict(snap)
            enriched.update({
                "checkpoints": checkpoint_data,
                "metrics": metrics,
                "passed_count": passed_count,
                "failed_count": len(evaluation.checkpoints) - passed_count,
                "overall_pass": evaluation.overall_pass,
                "evaluation_summary": evaluation.summary,
                "is_final_attempt": is_final,
                **deltas,
            })
            updated.append(enriched)
        else:
            updated.append(snap)
    return updated


def compile_feedback(state: dict) -> dict:
    """LangGraph node: record the rejection and the change deltas.

    Reads:  evaluation, attempt_number, rejection_log, attempt_snapshots, metrics
    Writes: rejection_log, attempt_snapshots
    """
    evaluation: EvaluationResult = state["evaluation"]
    attempt_number = state["attempt_number"]
    rejection_log = state.get("rejection_log", [])
    snapshots = state.get("attempt_snapshots", [])
    metrics = state.get("metrics", {})

    enriched = _enrich(snapshots, attempt_number, evaluation, metrics, is_final=False)
    current = next(
        (s for s in enriched if s["attempt_number"] == attempt_number), {}
    )

    failed = [cp for cp in evaluation.checkpoints if not cp.passed]
    entry = RejectionEntry(
        attempt_number=attempt_number,
        failed_checkpoints=[cp.name for cp in failed],
        failure_reasons={cp.name: cp.reason for cp in failed},
        feedback_given="\n".join(f"{cp.name}: {cp.reason}" for cp in failed),
        checkpoints_fixed=current.get("checkpoints_fixed", []),
        checkpoints_regressed=current.get("checkpoints_regressed", []),
        checkpoints_still_failing=current.get("checkpoints_still_failing", []),
        metric_deltas=current.get("metric_deltas", {}),
    )

    return {
        "rejection_log": rejection_log + [entry],
        "attempt_snapshots": enriched,
    }


def enrich_final_snapshot(state: dict) -> dict:
    """Fill in the last attempt's snapshot when the pipeline ends.

    Called from select_best, since compile_feedback does not run on the final
    attempt.
    """
    evaluation = state.get("evaluation")
    if not evaluation:
        return {}
    return {
        "attempt_snapshots": _enrich(
            state.get("attempt_snapshots", []),
            state.get("attempt_number", 1),
            evaluation,
            state.get("metrics", {}),
            is_final=True,
        )
    }
