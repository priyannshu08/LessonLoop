"""
LangGraph State Schema for the Lesson Content Generator.

Defines the TypedDict that flows through all nodes in the graph.
Every node reads from and writes to this shared state.
"""

from typing import TypedDict, Optional
from src.schemas.evaluation import EvaluationResult, RejectionEntry, AttemptSnapshot


class LessonState(TypedDict):
    """Complete state that flows through the LangGraph pipeline.

    Fields:
        topic: The lesson topic (e.g., "RAG (Retrieval-Augmented Generation)")
        learner_profile: Description of the target audience
        lesson_content: The current lesson draft in markdown format
        evaluation: Structured evaluation result from the evaluator node
        metrics: Deterministic measurements (FK grade, word count, ratios)
        rejection_log: List of all failed attempts with reasons and change-deltas
        attempt_number: Current attempt number (1-indexed)
        max_attempts: Total attempts allowed, counting the first one
        feedback_for_regeneration: Compiled feedback for the next attempt
        lessons_learned: Loaded from long-term memory store at run start
        memory_before: Snapshot of lessons_learned at the START of the run
        attempt_snapshots: Full per-attempt audit trail (lesson + checkpoints + feedback)
        selected_attempt: Attempt number actually shipped — not always the last
        is_final: Whether this is the final output (pass or attempts exhausted)
    """

    topic: str
    run_id: str                       # Minted once at run start; used everywhere
    learner_profile: str
    lesson_content: str
    evaluation: Optional[EvaluationResult]
    metrics: dict                     # Deterministic measurements for this draft
    rejection_log: list[RejectionEntry]
    attempt_number: int
    max_attempts: int                 # Total attempts (1 initial + N retries)
    feedback_for_regeneration: str
    lessons_learned: list[str]
    memory_before: list[str]          # Snapshot at run start — for audit trail
    attempt_snapshots: list[dict]     # Full per-attempt audit (serialized AttemptSnapshot)
    selected_attempt: int             # Which attempt was shipped (see select_best)
    use_memory: bool                  # False = control arm; inject no learned rules
    active_rule_ids: list[str]        # Rules injected this run — for utility tracking
    new_lessons_learned: list[str]    # Rules promoted by consolidation this run
    memory_report: dict               # What consolidation changed, for the audit trail
    is_final: bool
