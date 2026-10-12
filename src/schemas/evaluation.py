"""
Pydantic Models for Lesson Evaluation.

These models enforce structured output from the LLM-as-Judge evaluator.
Using Pydantic guarantees the evaluation result is machine-readable
and can be reliably parsed by the routing logic.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class CheckpointResult(BaseModel):
    """Result of a single rubric checkpoint evaluation.

    Each checkpoint is binary — PASS or FAIL with no partial credit.

    FIELD ORDER IS LOAD-BEARING. Structured output generates tokens in schema
    declaration order, so the judge must produce evidence and reasoning BEFORE
    committing to a verdict. Putting `passed` first would make every
    justification a post-hoc rationalisation of an already-fixed label;
    reasoning-before-verdict measurably improves agreement with human raters.
    """

    name: str = Field(
        ...,
        description="Checkpoint identifier (e.g., 'accurate_and_grounded', 'beginner_friendly_language')",
    )
    evidence: str = Field(
        ...,
        description="STEP 1 — Quote directly from the lesson. Cite the specific passage(s) "
        "relevant to this checkpoint before forming any judgment. "
        "Use '(nothing found)' if the issue is that something is missing.",
    )
    reason: str = Field(
        ...,
        description="STEP 2 — Reason about the quoted evidence against this checkpoint's "
        "criterion. Argue the case explicitly. Must be specific and actionable on failure.",
    )
    passed: bool = Field(
        ...,
        description="STEP 3 — Only now, state the verdict that follows from your reasoning. "
        "True if the checkpoint passes, False if it fails.",
    )


class EvaluationResult(BaseModel):
    """Complete evaluation result across all 6 rubric checkpoints.

    The overall_pass is True ONLY if every single checkpoint passes.
    This is used by the router to decide: output final OR regenerate.
    """

    checkpoints: list[CheckpointResult] = Field(
        ...,
        description="Exactly 6 checkpoint results, one for each rubric dimension",
        min_length=6,
        max_length=6,
    )
    overall_pass: bool = Field(
        ...,
        description="True ONLY if ALL 6 checkpoints pass. False if any single checkpoint fails.",
    )
    summary: str = Field(
        ...,
        description="2-3 sentence overall assessment of the lesson quality",
    )


class AttemptSnapshot(BaseModel):
    """Full snapshot of a single generation attempt.

    Captures everything about one attempt: what was generated,
    how it was evaluated, and what feedback was sent to the next attempt.
    This is persisted to disk for the rich audit trail.
    """

    attempt_number: int = Field(..., description="Which attempt this was (1-indexed)")
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="When this attempt occurred",
    )
    word_count: int = Field(..., description="Total word count of the generated lesson")
    word_count_delta: Optional[str] = Field(
        None,
        description="Word count change vs previous attempt, e.g. '+39 words vs attempt 1'",
    )
    lesson_preview: str = Field(
        ...,
        description="First 400 characters of the generated lesson for quick review",
    )
    full_lesson: str = Field(
        ...,
        description="Complete generated lesson content",
    )
    checkpoints: list[CheckpointResult] = Field(
        ...,
        description="Full checkpoint-level evaluation results for this attempt",
    )
    passed_count: int = Field(
        ..., description="Number of checkpoints that passed"
    )
    failed_count: int = Field(
        ..., description="Number of checkpoints that failed"
    )
    overall_pass: bool = Field(
        ..., description="Whether all 6 checkpoints passed"
    )
    evaluation_summary: str = Field(
        ..., description="2-3 sentence overall evaluation summary for this attempt"
    )
    feedback_sent_to_generator: Optional[str] = Field(
        None,
        description="The compiled feedback payload sent to the generator for the next attempt. "
        "None if this was the final attempt or if the attempt passed.",
    )
    is_final_attempt: bool = Field(
        default=False,
        description="True if this was the last attempt (passed or max retries reached)",
    )


class RejectionEntry(BaseModel):
    """A single entry in the rejection log.

    Captures what failed, why, and what changes were requested
    for each failed attempt.
    """

    attempt_number: int = Field(..., description="Which attempt this was (1-indexed)")
    failed_checkpoints: list[str] = Field(
        ..., description="Names of checkpoints that failed"
    )
    failure_reasons: dict[str, str] = Field(
        ...,
        description="Mapping of checkpoint name to failure reason",
    )
    feedback_given: str = Field(
        ...,
        description="The compiled feedback that was sent to the generator for retry",
    )
    # --- What actually CHANGED versus the previous attempt ---
    # The assessment asks for "what failed, why, and what you changed on retry".
    # The three fields below answer the third part. `checkpoints_regressed` is
    # deliberately recorded: iterative refinement is not monotonic, and hiding
    # regressions would misrepresent how the loop behaves.
    checkpoints_fixed: list[str] = Field(
        default_factory=list,
        description="Checkpoints that failed on the previous attempt and pass on this one",
    )
    checkpoints_regressed: list[str] = Field(
        default_factory=list,
        description="Checkpoints that passed on the previous attempt but fail on this one",
    )
    checkpoints_still_failing: list[str] = Field(
        default_factory=list,
        description="Checkpoints that failed on the previous attempt and still fail",
    )
    metric_deltas: dict[str, str] = Field(
        default_factory=dict,
        description="Measured changes vs the previous attempt (word count, FK grade, etc.)",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="When this rejection occurred",
    )
