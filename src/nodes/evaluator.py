"""Evaluator Node — the quality gate the generator cannot talk its way past.

TWO TIERS
---------
Tier 1 (`src.evaluation.deterministic`) settles everything countable — sentence
    length, formal register, whether terms are explained near first use. Free,
    instant, and identical on every run.
Tier 2 (this module) is a single LLM call ruling on what genuinely needs
    judgement: is it true, does it teach by example, does it cover what/why/how,
    does it flow.

A Tier 1 verdict is AUTHORITATIVE and overrides the judge for its checkpoint.
That is the point of the split: the mechanical bar cannot be argued down.

WHY THE JUDGE IS SEPARATE FROM THE GENERATOR
--------------------------------------------
Intrinsic self-correction — a model critiquing its own output with no external
signal — is documented to leave quality flat or make it worse. Iterative
refinement works when the feedback comes from OUTSIDE. So the judge is a
different model behind gates it does not control.

Known limitation: judge and generator are the same vendor, which leaves a
self-enhancement bias in place. Recorded in DESIGN.md rather than hidden.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.config import (
    CHECKPOINT_NAMES,
    EVALUATOR_MODEL,
    EVALUATOR_TEMPERATURE,
    TARGET_LEARNER_PROFILE,
)
from src.evaluation.deterministic import run_deterministic_gates
from src.prompts.evaluator_system import EVALUATOR_SYSTEM_PROMPT
from src.schemas.evaluation import CheckpointResult, EvaluationResult


def _metrics_summary(m: dict) -> str:
    """Render measurements as evidence for the judge.

    Reading grade appears here rather than as a gate. Readability formulas
    correlate weakly with human judgement and are trivially gamed, so the number
    informs the judge without deciding anything on its own.
    """
    if not m:
        return "(no measurements available)"
    return (
        f"- Words: {m.get('word_count')} "
        f"({m.get('avg_sentence_words')} per sentence)\n"
        f"- Sentences over 25 words: {m.get('long_sentence_ratio', 0):.0%}\n"
        f"- Formal/academic words: {len(m.get('academic_words', []))}\n"
        f"- Flesch-Kincaid grade: {m.get('fk_grade')} "
        f"(context only — this does not decide any checkpoint)"
    )


def _merge(
    judged: list[CheckpointResult],
    authoritative: list[CheckpointResult],
) -> list[CheckpointResult]:
    """Combine tiers into exactly one result per checkpoint.

    Deterministic results override the judge for their checkpoint. Critically,
    they are force-inserted even when the judge never emitted that name: an
    earlier version merged only on exact name match, so a judge returning
    "key_points" instead of "covers_key_points" silently dropped a hard failure.

    Any checkpoint nobody ruled on FAILS CLOSED — an unevaluated criterion must
    never be mistaken for a passing one.
    """
    by_name = {r.name: r for r in authoritative}
    judged_by_name = {c.name: c for c in judged}

    merged = []
    for name in CHECKPOINT_NAMES:
        if name in by_name:
            merged.append(by_name[name])
        elif name in judged_by_name:
            merged.append(judged_by_name[name])
        else:
            merged.append(
                CheckpointResult(
                    name=name,
                    evidence="(nothing found)",
                    reason=(
                        "The evaluator returned no result for this checkpoint. "
                        "Treated as a failure so an unevaluated criterion can "
                        "never pass by default."
                    ),
                    passed=False,
                )
            )
    return merged


def evaluate_lesson(state: dict) -> dict:
    """LangGraph node: judge a lesson against the six checkpoints.

    Reads:  lesson_content, topic, learner_profile
    Writes: evaluation, metrics
    """
    lesson = state["lesson_content"]
    topic = state.get("topic", "the given topic")
    learner_profile = state.get("learner_profile", TARGET_LEARNER_PROFILE)

    # --- Tier 1 -----------------------------------------------------------
    deterministic, metrics = run_deterministic_gates(lesson, topic)

    # --- Tier 2 -----------------------------------------------------------
    system_prompt = EVALUATOR_SYSTEM_PROMPT.format(
        learner_profile=learner_profile,
        topic=topic,
        metrics_summary=_metrics_summary(metrics),
    )
    user_message = (
        f'Evaluate this lesson on "{topic}" against all six checkpoints.\n\n'
        f"Quote your evidence first, reason about it second, and only then "
        f"state each verdict.\n\n## Lesson\n\n{lesson}"
    )

    llm = ChatOpenAI(model=EVALUATOR_MODEL, temperature=EVALUATOR_TEMPERATURE)
    structured = llm.with_structured_output(
        EvaluationResult, method="function_calling"
    )

    try:
        result = structured.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])
        judged, summary = result.checkpoints, result.summary
    except Exception as exc:
        # Fail closed. An API error must not crash the run or spin the loop —
        # the attempt counter still advances, so termination is unaffected.
        judged = []
        summary = f"Evaluation failed ({type(exc).__name__}: {exc})."

    checkpoints = _merge(judged, deterministic)
    overall_pass = all(cp.passed for cp in checkpoints)

    if deterministic:
        names = ", ".join(d.name for d in deterministic)
        summary = f"Failed measured gates ({names}). {summary}"

    return {
        "evaluation": EvaluationResult(
            checkpoints=checkpoints,
            overall_pass=overall_pass,
            summary=summary,
        ),
        "metrics": metrics,
    }
