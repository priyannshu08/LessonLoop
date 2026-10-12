"""Generator Node — writes the lesson, and revises it on retry.

TWO MODES
---------
Attempt 1  : topic + six rules + any learned rules. Write from scratch.
Attempt 2+ : the previous draft + ONLY what failed + what was already tried.
             The six rules are deliberately NOT re-sent.

That second mode is the important one. Re-sending the whole rulebook on a retry
makes the specific fix compete for attention with every standing instruction,
and the fix loses. Sending the draft plus a short grounded error trace is what
turns a re-roll into an actual revision.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.config import (
    GENERATOR_MODEL,
    GENERATOR_TEMPERATURE,
    TARGET_LEARNER_PROFILE,
)
from src.prompts.generator_system import (
    FIRST_ATTEMPT,
    GENERATOR_SYSTEM_PROMPT,
    LESSONS_LEARNED_SECTION,
    NO_LESSONS_LEARNED,
    PREVIOUS_ATTEMPTS_SECTION,
    REVISION_PROMPT,
)


def _format_failures(evaluation) -> str:
    """Render failed checkpoints as a grounded error trace.

    Natural-language feedback carries far more information than a score, and the
    critique must be specific enough to act on — so each failure names what is
    wrong, quotes the evidence, and states the fix. Passing checkpoints are
    omitted entirely: they are noise at this point.
    """
    if not evaluation:
        return "(no evaluation available)"

    lines = []
    for i, cp in enumerate(
        [c for c in evaluation.checkpoints if not c.passed], start=1
    ):
        lines.append(f"{i}. **{cp.name}**")
        lines.append(f"   Problem: {cp.reason}")
        if cp.evidence and cp.evidence != "(nothing found)":
            lines.append(f"   Evidence: {cp.evidence}")
    return "\n".join(lines) if lines else "(nothing failed)"


def _format_previous_attempts(snapshots: list[dict]) -> str:
    """Summarise what earlier revisions tried, and what it achieved.

    Prevents the loop re-deriving a repair that has already been shown not to
    work. Each entry pairs the attempt with its measured outcome so the model can
    see that the approach did not move the needle, not merely that it was tried.
    """
    tried = []
    for snap in snapshots:
        failed = [c["name"] for c in snap.get("checkpoints", []) if not c["passed"]]
        if not failed:
            continue
        metrics = snap.get("metrics", {})
        outcome = (
            f"still failed {', '.join(failed)}"
            f" (long sentences {metrics.get('long_sentence_ratio', 0):.0%},"
            f" formal words {len(metrics.get('academic_words', []))})"
        )
        tried.append(f"- Attempt {snap['attempt_number']}: {outcome}")

    if not tried:
        return ""
    return PREVIOUS_ATTEMPTS_SECTION.format(attempts="\n".join(tried))


def _build_system_prompt(
    learner_profile: str,
    lessons_learned: list[str],
    attempt_number: int,
    previous_lesson: str,
    evaluation,
    snapshots: list[dict],
) -> str:
    """Assemble the system prompt for this attempt."""
    if lessons_learned:
        numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(lessons_learned, 1))
        lessons_section = LESSONS_LEARNED_SECTION.format(lessons=numbered)
    else:
        lessons_section = NO_LESSONS_LEARNED

    if attempt_number == 1:
        feedback_section = FIRST_ATTEMPT
    else:
        feedback_section = REVISION_PROMPT.format(
            previous_lesson=previous_lesson,
            failures=_format_failures(evaluation),
            previous_attempts=_format_previous_attempts(snapshots),
        )

    return GENERATOR_SYSTEM_PROMPT.format(
        learner_profile=learner_profile,
        lessons_learned_section=lessons_section,
        feedback_section=feedback_section,
    )


def generate_lesson(state: dict) -> dict:
    """LangGraph node: write or revise the lesson.

    Reads:  topic, learner_profile, lessons_learned, lesson_content,
            evaluation, attempt_number, attempt_snapshots
    Writes: lesson_content, attempt_number, attempt_snapshots
    """
    topic = state["topic"]
    learner_profile = state.get("learner_profile", TARGET_LEARNER_PROFILE)
    lessons_learned = state.get("lessons_learned", [])
    attempt_number = state.get("attempt_number", 0) + 1
    snapshots = state.get("attempt_snapshots", [])
    previous_lesson = state.get("lesson_content", "")

    system_prompt = _build_system_prompt(
        learner_profile=learner_profile,
        lessons_learned=lessons_learned,
        attempt_number=attempt_number,
        previous_lesson=previous_lesson,
        evaluation=state.get("evaluation"),
        snapshots=snapshots,
    )

    if attempt_number == 1:
        user_message = f"Write a beginner lesson on: **{topic}**"
    else:
        user_message = (
            f"Revise your lesson on **{topic}** using the notes above. "
            f"Return the complete revised lesson."
        )

    llm = ChatOpenAI(model=GENERATOR_MODEL, temperature=GENERATOR_TEMPERATURE)
    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_message),
    ])
    lesson = response.content

    snapshot = {
        "attempt_number": attempt_number,
        "word_count": len(lesson.split()),
        "lesson_preview": lesson[:400] + ("..." if len(lesson) > 400 else ""),
        "full_lesson": lesson,
        # Filled in by the evaluator / feedback compiler:
        "checkpoints": [],
        "metrics": {},
        "passed_count": 0,
        "failed_count": 0,
        "overall_pass": False,
        "evaluation_summary": "",
        "feedback_sent_to_generator": None,
        "is_final_attempt": False,
    }

    return {
        "lesson_content": lesson,
        "attempt_number": attempt_number,
        "attempt_snapshots": snapshots + [snapshot],
    }
