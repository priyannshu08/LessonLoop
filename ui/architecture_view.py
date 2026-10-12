"""The Architecture tab — how the system works, and why it is built this way.

WHY THIS TAB EXISTS
-------------------
The assessment asks for "a face-visible walkthrough of how your workflow works"
and says what it is looking for is "how you build the system — your planning, a
robust architecture, and the reasoning behind your design choices (why this
structure, these components, this evaluator, these trade-offs)."

So this tab is not decoration. It is the deliverable, laid out so it can be
narrated top to bottom:

    1. The loop            — the pipeline as a diagram, with the retry edge
    2. The rubric          — six checkpoints, each traced to a line in the brief
    3. The two tiers       — what is measured versus what is judged, and why
    4. Design decisions    — each choice with its reason and its trade-off
    5. What is NOT here    — considered and deliberately deferred
    6. The map             — which file does what

Diagrams are inline SVG rather than images so they stay sharp under video
compression. They carry their own palette rather than inheriting the theme:
Streamlit's markdown sanitiser strips <style> blocks and does not cascade CSS
custom properties into sanitised HTML, so a theme-aware diagram renders blank.
"""

import re
import streamlit as st

from ui.diagram import (
    evaluation_diagram,
    memory_diagram,
    pipeline_diagram,
    state_diagram,
)

from src.config import (
    CHECKPOINT_LABELS,
    CHECKPOINT_TIERS,
    EVALUATOR_MODEL,
    GENERATOR_MODEL,
    MEMORY_POLICY,
    RUBRIC,
)

# ---------------------------------------------------------------------
# ASCII Syntax Highlighting
# ---------------------------------------------------------------------

def render_ascii(text: str) -> None:
    html = text.replace("<", "&lt;").replace(">", "&gt;")
    html = html.replace("[ API CALL ]", "<span style='color: #fb923c; font-weight: bold;'>[ API CALL ]</span>")
    html = re.sub(r'\bYES\b', "<span style='color: #10b981; font-weight: bold;'>YES</span>", html)
    html = re.sub(r'\bNO\b', "<span style='color: #ef4444; font-weight: bold;'>NO</span>", html)
    html = html.replace("ACTIVE", "<span style='color: #10b981; font-weight: bold;'>ACTIVE</span>")
    html = html.replace("DROPPED", "<span style='color: #f59e0b; font-weight: bold;'>DROPPED</span>")
    html = html.replace("ESCALATED", "<span style='color: #ef4444; font-weight: bold;'>ESCALATED</span>")
    html = html.replace("FAILS CLOSED", "<span style='color: #ef4444; font-weight: bold;'>FAILS CLOSED</span>")
    html = html.replace("TIER 1 — MEASURED", "<span style='color: #94a3b8; font-weight: bold;'>TIER 1 — MEASURED</span>")
    html = html.replace("TIER 1 · MEASURED", "<span style='color: #94a3b8; font-weight: bold;'>TIER 1 · MEASURED</span>")
    html = html.replace("TIER 2 — JUDGED", "<span style='color: #a78bfa; font-weight: bold;'>TIER 2 — JUDGED</span>")
    html = html.replace("TIER 2 · JUDGED", "<span style='color: #a78bfa; font-weight: bold;'>TIER 2 · JUDGED</span>")
    
    st.markdown(
        f'<div style="white-space: pre; font-family: ui-monospace, monospace; font-size: 13px; line-height: 1.25; overflow-x: auto; padding: 1rem; border-radius: 0.5rem; background-color: var(--secondary-background-color); border: 1px solid var(--border-color);">{html}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------
# 2. Orchestration & State
# ---------------------------------------------------------------------

def render_orchestration() -> None:
    st.markdown("#### Orchestration & State (LangGraph)")
    st.markdown(
        "The entire pipeline is orchestrated as a directed cyclic graph using **LangGraph**. "
        "The nodes (Initialize, Generate, Evaluate, Compile Feedback, Select Best) communicate purely by "
        "reading from and writing to a strongly-typed `State` dictionary."
    )
    render_ascii(state_diagram())
    st.markdown(
        "Because state is passed explicitly between nodes, the **Generator** only ever sees the targeted critique "
        "compiled by the **Compile Feedback** node, preventing context bloat. The graph's edges dictate "
        "the exact logic of when to loop, when to route to memory, and when to terminate."
    )


# ---------------------------------------------------------------------
# 3. The loop
# ---------------------------------------------------------------------

def render_loop() -> None:
    st.markdown("#### The loop")

    # The same node strip the Run tab lights up live, shown here as a static
    # reference so the diagram below and the running pipeline are recognisably
    # the same six stages.
    from ui.components import NODE_FLOW, flow_diagram
    flow_diagram(completed={n for n, _ in NODE_FLOW})
    st.markdown(
        '<div class="note">These are the six graph nodes. During a run they '
        'light up in sequence on the Run tab. Below is what each does and how '
        'control flows between them.</div>',
        unsafe_allow_html=True,
    )
    st.write("")
    render_ascii(pipeline_diagram())



# ---------------------------------------------------------------------
# 2. The rubric
# ---------------------------------------------------------------------

BRIEF_MAPPING = {
    "accurate_and_grounded": (
        "accurate & grounded",
        "Claims are correct. Simplifying is fine; describing a process that does "
        "not happen is not.",
    ),
    "beginner_friendly_language": (
        "beginner-friendly language",
        f"Sentence length and formal-word register, measured. Long sentences "
        f"≤ {RUBRIC.max_long_sentence_ratio:.0%}, formal words "
        f"≤ {RUBRIC.max_academic_words}.",
    ),
    "teaches_by_example": (
        "teaches by example",
        "At least one example that WALKS THROUGH a scenario, rather than naming "
        "one.",
    ),
    "no_unexplained_jargon": (
        "clear, no unexplained jargon",
        "Every technical term explained within "
        f"{RUBRIC.jargon_definition_window} sentences of first use.",
    ),
    "covers_key_points": (
        "covers the key points",
        "What it is, why it matters, how it works — the brief's own three.",
    ),
    "coherent_teaching_flow": (
        "coherent teaching flow",
        "Nothing used before it is explained; numbered steps in order.",
    ),
}


def render_rubric() -> None:
    st.markdown("#### The rubric — six checkpoints, six lines of the brief")
    st.markdown(
        '<div class="note">Each checkpoint is binary: pass or fail, no partial '
        'credit. All six must pass or the lesson goes back. The mapping is '
        'deliberate — every checkpoint traces to a dimension the brief names, and '
        'nothing else is gated.</div>',
        unsafe_allow_html=True,
    )
    st.dataframe(
        [
            {
                "Checkpoint": CHECKPOINT_LABELS[name],
                "Brief's wording": brief,
                "Decided by": CHECKPOINT_TIERS[name],
                "Test": test,
            }
            for name, (brief, test) in BRIEF_MAPPING.items()
        ],
        width="stretch", hide_index=True,
    )



# ---------------------------------------------------------------------
# 3. The two tiers
# ---------------------------------------------------------------------

def render_tiers() -> None:
    st.markdown("#### Why evaluation is split in two")
    render_ascii(evaluation_diagram())
    left, right = st.columns(2, gap="large")

    with left:
        st.markdown("##### MEASURED — no LLM")
        st.markdown(
            """
<div class="tier-card measured">
<b>Sentence length · formal register · jargon explained · teaching order</b>
<br><br>
Free, instant, and identical on every run. An LLM is the wrong instrument for
counting: it is slow, costs tokens, drifts between runs, and rewards length.
<br><br>
A measured verdict is <b>authoritative</b> — the judge cannot overturn it. These
are the bars the generator cannot talk its way past.
</div>""",
            unsafe_allow_html=True,
        )

    with right:
        st.markdown("##### JUDGED — one LLM call")
        st.markdown(
            f"""
<div class="tier-card judged">
<b>Accuracy · worked examples · covers what/why/how · flow</b>
<br><br>
Only what genuinely needs an opinion. The judge runs on {EVALUATOR_MODEL} while
the generator runs on {GENERATOR_MODEL} — feedback must come from outside the
thing being corrected.
<br><br>
<b>The Evidence-Before-Verdict Pattern:</b> The output schema orders fields 
<b>evidence → reasoning → verdict</b>. By forcing the LLM to quote evidence and 
argue <i>before</i> it can output the binary <code>True/False</code> pass, it 
grounds its decision and severely limits lazy grading or hallucinated passes.
</div>""",
            unsafe_allow_html=True,
        )




# ---------------------------------------------------------------------
# 5. Design decisions
# ---------------------------------------------------------------------

DECISIONS = [
    ("Temperature Strategy: Creative vs. Deterministic",
     "The generator must write engaging content, while the evaluator must grade deterministically without drifting.",
     "The Generator uses a higher temperature (0.7) for fluency. The Evaluator uses temperature 0.0 for strict, reproducible grading.",
     "The evaluator's strictness can sometimes flag borderline acceptable creative choices, forcing extra retries."),
    ("An external judge, not self-critique",
     "A model critiquing its own work with no outside signal leaves quality flat "
     "or degrades it. Iterative refinement works when the signal comes from "
     "outside.",
     "The judge is a different model, sitting behind gates it does not control.",
     "Generator and judge share a vendor, so a self-enhancement bias remains. "
     "Named rather than hidden."),

    ("Binary checkpoints, no partial credit",
     "Binary judges agree with human raters more reliably than 5-point scales, "
     "and a pass/fail verdict is harder to game with verbosity.",
     "Six binary gates; all must pass.",
     "Six independent gates compound — at ~85% each the joint first-attempt rate "
     "is ~38%. That is what the retry loop is for."),

    ("Count what is countable",
     "LLM judges show verbosity bias, so a judge told to weigh length teaches the "
     "generator to pad.",
     "Length, register and jargon are measured before the judge sees anything, "
     "and the word count is never shown to it.",
     "Measured gates are blunt. They over-fire on edge cases — 'movie databases' "
     "was once flagged as jargon."),

    ("Reading grade informs, never decides",
     "Readability formulas correlate weakly with human judgement, are calibrated "
     "on native speakers rather than this reader, and are trivially gamed by "
     "choppy monosyllabic prose.",
     "Flesch-Kincaid is measured and handed to the judge as evidence, but gates "
     "nothing.",
     "One fewer hard signal. Accepted: optimising a weak proxy is worse."),

    ("Revise, do not resample",
     "Re-sending the full rulebook on a retry makes the fix compete with every "
     "standing instruction, and the fix loses.",
     "A retry carries the previous draft plus only the failures — and a note of "
     "what was already tried, so the loop cannot re-derive a failed repair.",
     "The revision inherits the draft's structure, so a fundamentally bad first "
     "draft is harder to escape."),

    ("Ship the best attempt, not the last",
     "Refinement is not monotonic; a later attempt can be worse.",
     "Every attempt is scored and the strongest shipped.",
     "The shipped lesson may not be the most recent, which has to be surfaced "
     "clearly or it looks like a bug."),

    ("Learn only from repeated failure",
     "One run is a sample, not a pattern. Writing a rule per failure produced 20 "
     "rules with four incompatible sentence-length limits, and quality fell as "
     "memory grew.",
     f"A failure must recur on {MEMORY_POLICY.promotion_topic_threshold} "
     f"DIFFERENT topics before it becomes a rule.",
     "Learning is slow by construction. Nothing is learned in the first few runs."),

    ("A hard constraint budget",
     "Instruction-following degrades past roughly ten simultaneous constraints — "
     "the model starts triaging which rules to obey.",
     f"The generator prompt is capped at {RUBRIC.max_prompt_constraints} "
     f"constraints, enforced by a test that fails the build.",
     f"Memory can hold at most {MEMORY_POLICY.max_active_rules} rules. A new one "
     f"must displace a worse one."),
]


def render_decisions() -> None:
    st.markdown("#### Design decisions, and what each one costs")
    st.markdown(
        '<div class="note">Every choice has a trade-off. Listing only the upside '
        'would be marketing.</div>',
        unsafe_allow_html=True,
    )
    for title, why, what, cost in DECISIONS:
        with st.expander(f"**{title}**", expanded=False):
            st.markdown(f"**Why** — {why}")
            st.markdown(f"**What it does** — {what}")
            st.markdown(f"**What it costs** — {cost}")


# ---------------------------------------------------------------------
# 6. Considered and deferred
# ---------------------------------------------------------------------

DEFERRED = [
    ("Comprehension gate",
     "A separate reader model, shown only the lesson, must answer what/why/how "
     "from it alone. Questions answerable WITHOUT the lesson are discarded as "
     "testing prior knowledge.",
     "The strongest idea here — it would test the brief's actual requirement "
     "('someone with no background should finish it and get it') rather than a "
     "proxy for it. Deferred as a fourth tier that could not be justified inside "
     "the time available."),
    ("Cross-family judge",
     "Run the judge on a different vendor from the generator.",
     "Removes the self-enhancement bias. Needs a second API key, so it is "
     "configurable but not the default."),
    ("Self-consistency voting",
     "Sample the judge three times on borderline verdicts and take the majority.",
     "Judges are non-deterministic even at temperature 0. Deferred: the severity "
     "thresholds fixed the instability more cheaply."),
    ("Vocabulary-frequency scoring",
     "Score difficulty by word frequency band rather than syllable count.",
     "Better founded than Flesch-Kincaid for a non-native reader. Deferred: "
     "needs a corpus, and demoting FK to evidence captured most of the benefit."),
    ("A statistically powered gold set",
     "~40 labelled samples per checkpoint rather than the current handful.",
     "The current calibration is a smoke test, not a measurement, and is "
     "reported as such. A powered set needs ~250 samples."),
]


def render_deferred() -> None:
    st.markdown("#### Considered and deliberately not built")
    st.markdown(
        '<div class="note">Knowing what not to build is part of the design. Each '
        'of these is defensible on its own; together they would be a far larger '
        'system than the brief asks for.</div>',
        unsafe_allow_html=True,
    )
    for title, what, why in DEFERRED:
        with st.expander(f"**{title}**", expanded=False):
            st.markdown(f"**What it would do** — {what}")
            st.markdown(f"**Why it is not here** — {why}")


# ---------------------------------------------------------------------
# 7. The map
# ---------------------------------------------------------------------

def render_map() -> None:
    st.markdown("#### Where everything lives")
    st.dataframe(
        [
            {"Path": "src/graph.py", "Role": "The LangGraph loop: nodes, routing, termination, best-attempt selection"},
            {"Path": "src/nodes/generator.py", "Role": "Writes the draft; on retry, revises it from the failures alone"},
            {"Path": "src/nodes/evaluator.py", "Role": "Runs both tiers and merges them; measured beats judged"},
            {"Path": "src/evaluation/deterministic.py", "Role": "The measured gates — no LLM, no cost, reproducible"},
            {"Path": "src/prompts/", "Role": "Generator and judge prompts, each traced to the brief"},
            {"Path": "src/memory/store.py", "Role": "Two tiers: observations (episodic) and rules (procedural)"},
            {"Path": "src/memory/consolidation.py", "Role": "Promotion, probation, eviction, escalation"},
            {"Path": "src/memory/signatures.py", "Role": "Turns a failure into a stable cause label so recurrence is countable"},
            {"Path": "evals/calibrate.py", "Role": "Measures the judge against labelled samples"},
            {"Path": "evals/ab_memory.py", "Role": "Same topics with memory on vs off — the control group"},
            {"Path": "outputs/<run>/", "Role": "lesson.md, rejection_log.json, attempt_trail.json, final_evaluation.json"},
            {"Path": "memory/", "Role": "What the system has learned — the only thing runs change"},
        ],
        width="stretch", hide_index=True,
    )
    st.caption(
        "Twenty runs changed no source file. The system alters its own behaviour "
        "entirely through `memory/` — rules are data, loaded at run start and "
        "injected into the generator prompt."
    )


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------

def render_architecture_tab() -> None:
    st.markdown(
        '<div class="note">A lesson is drafted, judged against six binary '
        'checkpoints, and revised until it clears the bar — or the best attempt '
        'is shipped. This tab is how and why.</div>',
        unsafe_allow_html=True,
    )
    st.divider()
    render_loop()
    st.divider()
    render_orchestration()
    st.divider()
    render_rubric()
    st.divider()
    render_tiers()

    st.markdown("#### How memory decides what to learn")
    st.markdown(
        '<div class="note">The same lifecycle the Memory tab shows with live '
        'data.</div>', unsafe_allow_html=True,
    )
    render_ascii(memory_diagram())

