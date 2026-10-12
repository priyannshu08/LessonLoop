"""Memory Node — records what happened, then consolidates what repeats.

This node runs after the pipeline completes (pass or fail). It does TWO things,
and the split between them is the whole design:

1. RECORD    — write one observation per failed checkpoint into episodic memory.
               Cheap, unbounded, and never shown to any model.
2. CONSOLIDATE — ask whether any failure pattern has now recurred often enough
               to deserve a permanent rule, and if so put it through
               contradiction-checking, capacity limits and probation.

WHY IT IS SPLIT
---------------
The previous version wrote 1-3 permanent rules after EVERY failing run. One
unlucky generation therefore became law forever, with only exact-string dedup to
stop repeats. The result was 20 rules holding four incompatible sentence-length
limits and two different word counts — and because a long instruction list loses
attention weight, the generator followed none of them. Measured output quality
fell as memory grew.

The brief asks the system to "learn from REPEATED failures". Observations are how
the system can tell what actually repeats. Everything else follows from that.

The real work lives in `src.memory.consolidation`; this node is the wiring.
"""

from src.memory.consolidation import consolidate
from src.memory.store import MemoryStore
from src.schemas.evaluation import RejectionEntry


def optimize_prompts(state: dict) -> dict:
    """LangGraph node: record this run's failures, then consolidate.

    Reads from state:
        - run_id, topic, rejection_log

    Writes to state:
        - new_lessons_learned: rule texts promoted on this run (usually empty —
          promotion is supposed to be rare)
        - memory_report: what consolidation did, for the audit trail
    """
    rejection_log: list[RejectionEntry] = state.get("rejection_log", [])
    run_id = state.get("run_id", "unknown")
    topic = state.get("topic", "unknown")

    memory = MemoryStore()

    # --- 1. Record ----------------------------------------------------
    # Runs that passed cleanly have nothing to record, but consolidation still
    # runs below: probation reviews and evictions depend on outcomes, not
    # failures, so a passing run is exactly when a rule earns its keep.
    # Record failures AND passes. A signature that stops recurring after a rule
    # went active is the strongest evidence that rule works — recording only
    # failures discarded half the signal.
    evaluation = state.get("evaluation")
    passed = (
        [cp.name for cp in evaluation.checkpoints if cp.passed] if evaluation else []
    )
    if rejection_log or passed:
        memory.record_observations(
            run_id=run_id,
            topic=topic,
            rejection_log=rejection_log,
            passed_signatures=passed,
        )

    # --- 2. Consolidate ----------------------------------------------
    report = consolidate(memory, run_id)

    promoted = [r["insight"] for r in report["promoted"]]
    merged = [r["insight"] for r in report["merged"]]
    new_lessons = promoted + merged

    if any(report[k] for k in ("promoted", "merged", "evicted", "dropped", "escalated")):
        memory.log_prompt_evolution(
            run_id=run_id,
            change_description=_describe(report),
            new_lessons_added=new_lessons,
        )

    return {
        "new_lessons_learned": new_lessons,
        "memory_report": report,
    }


def _describe(report: dict) -> str:
    """One-line human summary of what consolidation changed, for the timeline."""
    parts = []
    if report["promoted"]:
        parts.append(f"{len(report['promoted'])} rule(s) promoted after recurring")
    for merge in report["merged"]:
        parts.append(
            f"merged into {merge['id']} ({merge['relation']}, "
            f"replacing {', '.join(merge['replaced'])})"
        )
    if report["confirmed"]:
        parts.append(f"{len(report['confirmed'])} confirmed after probation")
    if report["dropped"]:
        parts.append(f"{len(report['dropped'])} dropped after failing probation")
    if report["evicted"]:
        parts.append(f"{len(report['evicted'])} evicted at capacity")
    if report["rejected"]:
        parts.append(f"{len(report['rejected'])} candidate(s) rejected as redundant")
    for signature in report.get("escalated", []):
        parts.append(f"ESCALATED {signature} — not fixable by prompting")
    return "; ".join(parts) or "no change"
