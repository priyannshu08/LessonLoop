"""LangGraph Workflow — The Core Agentic Pipeline.

Assembles the Generate → Evaluate → (Conditional) → Revise loop into a stateful,
cyclical graph with memory persistence and a full audit trail.

Architecture:
    ┌──────────┐     ┌──────────┐     ┌──────────────┐
    │ Generate │────→│ Evaluate │────→│ Route        │
    └──────────┘     └──────────┘     │ (Conditional)│
         ↑                            └──────┬───────┘
         │                                   │
         │              ┌────────────┐       │
         └──────────────│ Compile    │←──────┘ (FAIL + attempts left)
           (revises)    │ Feedback   │
                        └────────────┘
                                             │
                                             ↓ (PASS or attempts exhausted)
                                    ┌────────────────┐
                                    │  Select Best   │ ← ships the best attempt,
                                    └────────────────┘   not merely the last
                                             │
                                    ┌────────────────┐
                                    │ Prompt         │
                                    │ Optimizer      │ ← distils lessons to memory
                                    └────────────────┘
                                             │
                                          [END]

TERMINATION GUARANTEE
---------------------
`generate` increments `attempt_number` on every pass, and the router sends the
run to `select_best` as soon as `attempt_number >= max_attempts`. The counter is
monotonic and never reset, so the cycle executes at most `max_attempts` times.
An evaluator exception cannot stall the loop either — the evaluator fails closed
and the counter still advances.
"""

import time
import uuid

from langgraph.graph import StateGraph, END

from src.config import MAX_ATTEMPTS, TARGET_LEARNER_PROFILE
from src.memory.store import MemoryStore
from src.nodes.evaluator import evaluate_lesson
from src.nodes.feedback_compiler import compile_feedback, enrich_final_snapshot
from src.nodes.generator import generate_lesson
from src.nodes.prompt_optimizer import optimize_prompts
from src.schemas.state import LessonState


def route_after_evaluation(state: dict) -> str:
    """Conditional edge: ship the result, or go round again.

    Counts ATTEMPTS rather than retries. The previous version compared
    `attempt_number > max_retries`, which allowed max_retries + 1 regenerations
    — four total attempts when the setting said three.
    """
    evaluation = state.get("evaluation")
    attempt_number = state.get("attempt_number", 1)
    max_attempts = state.get("max_attempts", MAX_ATTEMPTS)

    if evaluation and evaluation.overall_pass:
        return "select_best"

    if attempt_number >= max_attempts:
        return "select_best"

    return "compile_feedback"


def initialize_state(state: dict) -> dict:
    """Entry node: seed state and load long-term memory.

    Mints the run_id ONCE here and threads it through state. Previously the
    graph and the optimizer each generated their own uuid for the same run, and
    the optimizer attributed its new rules to whichever run happened to be last
    on disk — which was the PREVIOUS run, since this run is only persisted after
    the graph returns. The audit trail was silently wrong as a result.
    """
    memory = MemoryStore()

    # `use_memory=False` is the control arm of the A/B harness: the pipeline runs
    # identically but with no learned rules injected. Without a control there is
    # no way to answer "is memory helping?" — and under the previous design the
    # honest answer turned out to be no.
    if state.get("use_memory", True):
        active_rules = memory.get_active_rules()
    else:
        active_rules = []
    lessons_learned = [r["insight"] for r in active_rules]

    return {
        "topic": state.get("topic", "RAG (Retrieval-Augmented Generation)"),
        "run_id": str(uuid.uuid4())[:8],
        "use_memory": state.get("use_memory", True),
        # Recorded so this run's outcome can be attributed to exactly the rules
        # that shaped it.
        "active_rule_ids": [r["id"] for r in active_rules],
        "learner_profile": state.get("learner_profile", TARGET_LEARNER_PROFILE),
        "lesson_content": "",
        "evaluation": None,
        "metrics": {},
        "rejection_log": [],
        "attempt_number": 0,
        "max_attempts": state.get("max_attempts", MAX_ATTEMPTS),
        "feedback_for_regeneration": "",
        "lessons_learned": lessons_learned,
        "memory_before": list(lessons_learned),   # Snapshot for audit trail
        "attempt_snapshots": [],
        "selected_attempt": 0,
        "is_final": False,
    }


def select_best(state: dict) -> dict:
    """Ship the strongest attempt produced, which is not always the last one.

    Iterative refinement is not monotonic — a later attempt can be worse than an
    earlier one, and our own run history shows exactly that (a RAG run went from
    one failed checkpoint to four across three attempts, and the four-failure
    version was the one written to disk).

    Ranking: a passing attempt beats a failing one; otherwise more passed
    checkpoints wins; ties break toward the EARLIER attempt, because if a later
    revision achieved nothing we prefer the draft that got there first.
    """
    enriched = enrich_final_snapshot(state)
    snapshots = enriched.get("attempt_snapshots") or state.get("attempt_snapshots", [])

    scored = [s for s in snapshots if s.get("checkpoints")]
    if not scored:
        return {"is_final": True, **enriched}

    best = max(
        scored,
        key=lambda s: (
            s.get("overall_pass", False),
            s.get("passed_count", 0),
            -s.get("attempt_number", 0),
        ),
    )

    result = {
        "is_final": True,
        "selected_attempt": best["attempt_number"],
        "lesson_content": best["full_lesson"],
        **enriched,
    }

    # If we rejected a later attempt in favour of an earlier one, the shipped
    # evaluation and metrics must describe the draft we actually ship.
    if best["attempt_number"] != state.get("attempt_number"):
        from src.schemas.evaluation import CheckpointResult, EvaluationResult

        result["evaluation"] = EvaluationResult(
            checkpoints=[CheckpointResult(**cp) for cp in best["checkpoints"]],
            overall_pass=best.get("overall_pass", False),
            summary=best.get("evaluation_summary", ""),
        )
        result["metrics"] = best.get("metrics", {})

    return result


def build_graph() -> StateGraph:
    """Build and compile the LangGraph workflow."""
    workflow = StateGraph(LessonState)

    workflow.add_node("initialize", initialize_state)
    workflow.add_node("generate", generate_lesson)
    workflow.add_node("evaluate", evaluate_lesson)
    workflow.add_node("compile_feedback", compile_feedback)
    workflow.add_node("select_best", select_best)
    workflow.add_node("prompt_optimizer", optimize_prompts)

    workflow.set_entry_point("initialize")
    workflow.add_edge("initialize", "generate")
    workflow.add_edge("generate", "evaluate")

    workflow.add_conditional_edges(
        "evaluate",
        route_after_evaluation,
        {
            "compile_feedback": "compile_feedback",
            "select_best": "select_best",
        },
    )

    workflow.add_edge("compile_feedback", "generate")
    workflow.add_edge("select_best", "prompt_optimizer")
    workflow.add_edge("prompt_optimizer", END)

    return workflow.compile()


def stream_pipeline(
    topic: str,
    max_attempts: int = MAX_ATTEMPTS,
    learner_profile: str = TARGET_LEARNER_PROFILE,
    use_memory: bool = True,
):
    """Yield (node_name, state) as each node completes.

    The UI renders the loop as it runs rather than freezing until the end. A
    run can take a minute across three attempts, and a progress spinner tells
    the viewer nothing about WHY a retry happened — which is the whole point of
    the system. Streaming lets each attempt's verdict appear as it is decided.

    The final yield carries the complete state, so callers can use this in place
    of `run_pipeline` and still persist the run.
    """
    graph = build_graph()
    start_time = time.time()
    final_state: dict = {}

    for chunk in graph.stream(
        {
            "topic": topic,
            "learner_profile": learner_profile,
            "max_attempts": max_attempts,
            "use_memory": use_memory,
        },
        stream_mode="values",
    ):
        final_state = chunk
        yield chunk.get("_node", ""), chunk

    _persist_run(final_state, topic, time.time() - start_time)
    yield "__done__", final_state


def _persist_run(result: dict, topic: str, duration: float) -> None:
    """Write the run to memory: rule utility first, then the audit entry."""
    memory = MemoryStore()

    failed_per_attempt = []
    for entry in result.get("rejection_log", []):
        if isinstance(entry, dict):
            failed_per_attempt.append(entry.get("failed_checkpoints", []))
        else:
            failed_per_attempt.append(entry.failed_checkpoints)

    evaluation = result.get("evaluation")
    final_passed = evaluation.overall_pass if evaluation else False

    # Attribute this run's outcome to the rules that were active during it.
    # Eviction and probation decisions are only defensible if each rule has a
    # track record; without this the choice of what to drop would be arbitrary.
    memory.record_rule_outcomes(
        active_rule_ids=result.get("active_rule_ids", []),
        run={"final_passed": final_passed,
             "total_attempts": result.get("attempt_number", 1)},
    )

    memory.log_run(
        run_id=result.get("run_id", "unknown"),
        topic=topic,
        total_attempts=result.get("attempt_number", 1),
        final_passed=final_passed,
        failed_checkpoints_per_attempt=failed_per_attempt,
        duration_seconds=duration,
        attempt_snapshots=result.get("attempt_snapshots", []),
        memory_before=result.get("memory_before", []),
        lessons_learned_active=result.get("lessons_learned", []),
        selected_attempt=result.get("selected_attempt"),
        new_lessons=result.get("new_lessons_learned", []),
        memory_after=memory.load_lessons_learned(),
    )


def run_pipeline(
    topic: str = "RAG (Retrieval-Augmented Generation)",
    max_attempts: int = MAX_ATTEMPTS,
    learner_profile: str = TARGET_LEARNER_PROFILE,
    max_retries: int | None = None,
    use_memory: bool = True,
) -> dict:
    """Run the full lesson generation pipeline.

    Args:
        max_attempts: total attempts including the first (1 initial + N retries).
        max_retries: deprecated alias kept for older callers; retries + 1.
        use_memory: when False, no learned rules are injected. Used as the
            control arm when measuring whether memory actually helps.
    """
    if max_retries is not None:
        max_attempts = max_retries + 1

    graph = build_graph()
    start_time = time.time()

    result = graph.invoke({
        "topic": topic,
        "learner_profile": learner_profile,
        "max_attempts": max_attempts,
        "use_memory": use_memory,
    })

    _persist_run(result, topic, time.time() - start_time)

    return result
