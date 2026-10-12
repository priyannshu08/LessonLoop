"""Self-Evaluating Lesson Generator — control surface.

The interface is organised around the four things a reviewer actually needs to
judge this system, one tab each:

    Run        — watch the generate → evaluate → revise loop turn, live
    Evaluator  — prove the rubric has teeth by feeding it broken content
    Memory     — see what the system has learned, and what it refused to learn
    Calibration— the evidence that the evaluator itself is correct

Everything here is a view over the pipeline. No evaluation logic lives in this
file; the UI's job is to make the trace legible, not to make decisions.
"""

import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from src.config import (
    CHECKPOINT_NAMES,
    MAX_ATTEMPTS,
    MEMORY_POLICY,
    OUTPUTS_DIR,
    RUBRIC,
    TARGET_LEARNER_PROFILE,
)
from src.evaluation.deterministic import run_deterministic_gates
from src.graph import stream_pipeline
from src.memory.store import MemoryStore
from src.nodes.evaluator import evaluate_lesson
from ui import theme
from ui.architecture_view import render_architecture_tab
from ui.memory_view import render_memory_tab
from ui.components import (
    LABELS,
    NODE_FLOW,
    attempt_card,
    checkpoint_detail,
    checkpoint_row,
    flow_diagram,
    metric_strip,
    rule_card,
    threshold_gauges,
)

st.set_page_config(
    page_title="Self-Evaluating Lesson Generator",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.inject()


# =====================================================================
# Session state
# =====================================================================

def init_state() -> None:
    for key, default in [
        ("result", None), ("topic", "RAG (Retrieval-Augmented Generation)"),
        ("probe_result", None), ("probe_metrics", None),
    ]:
        st.session_state.setdefault(key, default)


# =====================================================================
# Sidebar
# =====================================================================

def sidebar() -> tuple[str, int, bool]:
    with st.sidebar:
        st.markdown("### Run configuration")
        topic = st.text_input(
            "Lesson topic",
            value=st.session_state.topic,
            help="What the lesson should teach. Assume the learner starts from zero.",
        ).strip()

        max_attempts = st.slider(
            "Max attempts", 1, 4, MAX_ATTEMPTS,
            help="One initial draft plus retries. Refinement gains concentrate in "
                 "the first 1-2 iterations, so the default is 3.",
        )
        use_memory = st.toggle(
            "Inject learned rules", value=True,
            help="Off = the A/B control arm: identical pipeline, no memory. "
                 "This is how we measure whether memory actually helps.",
        )

        st.divider()
        st.markdown("### Measured gates")
        st.caption(
            "The only thresholds in the system. Everything else about the "
            "lesson is judged on its substance, not counted."
        )
        for label, value in [
            ("Long sentences", f"≤ {RUBRIC.max_long_sentence_ratio:.0%}"),
            ("Sentence length", f"{RUBRIC.long_sentence_words} words"),
            ("Formal words", f"≤ {RUBRIC.max_academic_words}"),
            ("Viability floor", f"{RUBRIC.min_viable_words} words"),
        ]:
            left, right = st.columns([3, 2])
            left.caption(label)
            right.caption(f"**{value}**")

        st.divider()
        memory = MemoryStore()
        active = memory.get_active_rules()
        st.markdown("### Memory")
        st.caption(
            f"**{len(active)}/{MEMORY_POLICY.max_active_rules}** rules active · "
            f"**{len(memory.get_observations())}** observations recorded"
        )

    return topic, max_attempts, use_memory


# =====================================================================
# Tab 1 — Run
# =====================================================================

def save_outputs(result: dict, topic: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if c.isalnum() else "_" for c in topic)[:40].strip("_")
    out = OUTPUTS_DIR / f"{safe}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    (out / "lesson.md").write_text(result.get("lesson_content", ""))

    log = [e if isinstance(e, dict) else e.model_dump()
           for e in result.get("rejection_log", [])]
    (out / "rejection_log.json").write_text(json.dumps(log, indent=2, default=str))
    (out / "attempt_trail.json").write_text(
        json.dumps(result.get("attempt_snapshots", []), indent=2, default=str)
    )

    evaluation = result.get("evaluation")
    if evaluation:
        (out / "final_evaluation.json").write_text(
            json.dumps(evaluation.model_dump(), indent=2, default=str)
        )
    return out


def render_run_tab(topic: str, max_attempts: int, use_memory: bool) -> None:
    st.markdown(
        '<div class="note">The pipeline drafts a lesson, judges it against six '
        'pass/fail checkpoints, and revises until it clears the bar — or ships '
        'the best attempt it managed. Watch it turn.</div>',
        unsafe_allow_html=True,
    )

    go = st.button("Run pipeline", type="primary", use_container_width=False)
    if not go and st.session_state.result is None:
        flow_diagram()
        st.info("Set a topic in the sidebar and run the pipeline to see a trace.")
        return

    if go:
        if not topic:
            st.error("Enter a topic first.")
            return
        st.session_state.topic = topic
        st.session_state.result = None

        flow_slot = st.empty()
        live_slot = st.container()
        completed: set[str] = set()
        rendered = 0

        with st.status("Running pipeline…", expanded=True) as status:
            try:
                for _, state in stream_pipeline(
                    topic=topic,
                    max_attempts=max_attempts,
                    use_memory=use_memory,
                ):
                    snapshots = state.get("attempt_snapshots", [])

                    # Render each attempt the moment its verdict lands, so the
                    # reader sees WHY a retry happened rather than a spinner.
                    scored = [s for s in snapshots if s.get("checkpoints")]
                    while rendered < len(scored):
                        snap = scored[rendered]
                        prev = scored[rendered - 1] if rendered else None
                        with live_slot:
                            attempt_card(snap, prev)
                        rendered += 1
                        completed |= {"initialize", "generate", "evaluate"}

                    with flow_slot:
                        flow_diagram(active="evaluate", completed=completed)
                    status.update(
                        label=f"Attempt {state.get('attempt_number', 0)} of {max_attempts}…"
                    )
                    st.session_state.result = state

                status.update(label="Pipeline complete", state="complete")
            except Exception as exc:
                status.update(label=f"Pipeline failed: {exc}", state="error")
                st.exception(exc)
                return

        live_slot.empty()

    result = st.session_state.result
    if not result:
        return

    evaluation = result.get("evaluation")
    passed = bool(evaluation and evaluation.overall_pass)
    snapshots = [s for s in result.get("attempt_snapshots", []) if s.get("checkpoints")]
    shipped = result.get("selected_attempt")

    # Keep the pipeline visible after the run, with every node marked done, so
    # the finished trace still shows WHICH path it took.
    flow_diagram(completed={n for n, _ in NODE_FLOW})

    # ---- Verdict header ----------------------------------------------
    cols = st.columns([2, 1, 1, 1])
    cols[0].metric("Result", "PASSED" if passed else "BEST EFFORT")
    cols[1].metric("Attempts", result.get("attempt_number", 0))
    cols[2].metric("Shipped", f"#{shipped}" if shipped else "—")
    cols[3].metric(
        "Checkpoints",
        f"{sum(1 for c in evaluation.checkpoints if c.passed)}/6" if evaluation else "—",
    )

    if shipped and shipped != result.get("attempt_number"):
        st.warning(
            f"Attempt {shipped} was shipped over the later attempt {result['attempt_number']}. "
            f"Refinement is not monotonic, so the system ranks every attempt and "
            f"ships the strongest rather than the last."
        )

    st.divider()
    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown("#### Trace")
        st.markdown(
            '<div class="note">Each attempt, in order, with what changed between them.</div>',
            unsafe_allow_html=True,
        )
        for i, snap in enumerate(snapshots):
            attempt_card(
                snap,
                snapshots[i - 1] if i else None,
                shipped=(snap.get("attempt_number") == shipped),
            )

    with right:
        st.markdown("#### Final verdict")
        if evaluation:
            checkpoint_row([c.model_dump() for c in evaluation.checkpoints])
            st.caption(evaluation.summary)
            st.markdown("##### Measured against thresholds")
            threshold_gauges(result.get("metrics", {}))
            with st.expander("Full checkpoint detail", expanded=False):
                checkpoint_detail([c.model_dump() for c in evaluation.checkpoints])

        report = result.get("memory_report") or {}
        if any(report.get(k) for k in ("promoted", "merged", "evicted", "dropped")):
            st.markdown("##### Memory changed this run")
            for item in report.get("promoted", []):
                st.success(f"Promoted: {item['insight']}")
            for item in report.get("merged", []):
                st.info(f"Merged ({item['relation']}): {item['insight']}")
            for rid in report.get("dropped", []):
                st.warning(f"Dropped after probation: {rid}")

    st.divider()
    st.markdown("#### Shipped lesson")
    lesson = result.get("lesson_content", "")
    a, b = st.columns([1, 1])
    a.caption(f"{len(lesson.split())} words")
    if b.button("Save run to outputs/"):
        st.success(f"Saved to `{save_outputs(result, st.session_state.topic)}`")
    st.markdown(lesson)


# =====================================================================
# Tab 2 — Evaluator lab
# =====================================================================

PROBES = {
    "Factual error (wrong mechanism)": (
        "## What Is RAG?\n\nRAG helps a computer answer your questions.\n\n"
        "## How Does RAG Work?\n\nEvery single time you ask a question, the system "
        "first deletes everything it learned during training. It then relearns all "
        "of that knowledge from scratch in under one second, before it answers. "
        "This delete-and-relearn step runs on every request, and it is why the "
        "answers are accurate.\n\n"
        "## Why Does RAG Matter?\n\nIt saves time.\n\n"
        "## Real-World Use Cases\n\nShops use it.\n\n"
        "## Quick Quiz\n\n1. What is RAG?\n\n## Summary\n\n- RAG answers questions.\n"
    ),
    "Academic register (unreadable)": (
        "## What Is RAG?\n\nRAG constitutes a sophisticated paradigm that utilises "
        "retrieval methodologies to facilitate the augmentation of generative "
        "capabilities, subsequently leveraging the aforementioned architecture to "
        "engender contextually optimal outputs encompassing myriad salient "
        "considerations pertaining to the requisite informational substrate.\n\n"
        "## Why Does RAG Matter?\n\nIt demonstrates efficacious performance.\n\n"
        "## How Does RAG Work?\n\nThe methodology commences with vectorisation.\n\n"
        "## Real-World Use Cases\n\nEnterprises utilise it.\n\n"
        "## Quick Quiz\n\n1. Elucidate RAG.\n\n## Summary\n\n- RAG is optimal.\n"
    ),
    "Undefined jargon": (
        "## What Is RAG?\n\nRAG uses embeddings and a vector database to run "
        "semantic search over a corpus, then passes the retrieved tokens to an "
        "LLM for inference via its neural network.\n\n"
        "## Why Does RAG Matter?\n\nIt reduces hallucination in the model.\n\n"
        "## How Does RAG Work?\n\nThe algorithm indexes the dataset.\n\n"
        "## Real-World Use Cases\n\nAPIs use it.\n\n"
        "## Quick Quiz\n\n1. What is an embedding?\n\n## Summary\n\n- RAG uses AI.\n"
    ),
    "Scrambled teaching order": (
        "## Summary\n\n- You now understand RAG.\n\n"
        "## Quick Quiz\n\n1. What is RAG?\n\n"
        "## Real-World Use Cases\n\nShops and schools use RAG every day.\n\n"
        "## How Does RAG Work?\n\nIt finds information, then writes an answer.\n\n"
        "## Why Does RAG Matter?\n\nIt gives better answers.\n\n"
        "## What Is RAG?\n\nRAG is a way for a computer to look things up.\n"
    ),
}


def render_evaluator_tab() -> None:
    st.markdown(
        '<div class="note">A rubric is only worth having if it rejects bad work. '
        'Feed the evaluator deliberately broken content and watch which '
        'checkpoint fires — and whether it was <b>measured</b> or <b>judged</b>.</div>',
        unsafe_allow_html=True,
    )

    left, right = st.columns([3, 2], gap="large")

    with left:
        preset = st.selectbox("Load a probe", ["(write my own)"] + list(PROBES))
        default = PROBES.get(preset, "")
        draft = st.text_area("Draft to evaluate", value=default, height=300)
        topic = st.text_input("Topic", value="RAG (Retrieval-Augmented Generation)")

        a, b = st.columns(2)
        if a.button("Run full evaluation", type="primary"):
            with st.spinner("Evaluating…"):
                try:
                    res = evaluate_lesson({"lesson_content": draft, "topic": topic})
                    st.session_state.probe_result = res["evaluation"]
                    st.session_state.probe_metrics = res.get("metrics")
                except Exception as exc:
                    st.error(f"Evaluation failed: {exc}")
        if b.button("Deterministic gates only (free)"):
            failures, metrics = run_deterministic_gates(draft, topic)
            st.session_state.probe_result = None
            st.session_state.probe_metrics = metrics
            if failures:
                for f in failures:
                    st.error(f"**{LABELS.get(f.name, f.name)}** — {f.reason}")
            else:
                st.success("All deterministic gates pass.")

    with right:
        st.markdown("#### Result")
        evaluation = st.session_state.probe_result
        if evaluation:
            verdict = "PASSED" if evaluation.overall_pass else "REJECTED"
            (st.success if evaluation.overall_pass else st.error)(
                f"**{verdict}** — "
                f"{sum(1 for c in evaluation.checkpoints if not c.passed)}/6 failed"
            )
            checkpoint_row([c.model_dump() for c in evaluation.checkpoints])
            st.caption(evaluation.summary)
            checkpoint_detail([c.model_dump() for c in evaluation.checkpoints])
        elif st.session_state.probe_metrics is None:
            st.info("Load a probe and evaluate it.")

        if st.session_state.probe_metrics:
            st.markdown("##### Measurements")
            metric_strip(st.session_state.probe_metrics)
            threshold_gauges(st.session_state.probe_metrics)


# =====================================================================
# Tab 3 — Memory
# =====================================================================

# =====================================================================
# Main
# =====================================================================

def main() -> None:
    init_state()
    st.markdown(
        '<div class="hero"><h1>Self-Evaluating Lesson Generator</h1>'
        "<p>Generates a beginner lesson, judges it against six hard pass/fail "
        "checkpoints, and revises until it clears the bar — deciding on its own "
        "whether the content is good enough to ship.</p></div>",
        unsafe_allow_html=True,
    )

    topic, max_attempts, use_memory = sidebar()

    run, evaluator, memory, architecture = st.tabs(
        ["Run", "Evaluator lab", "Memory", "How it works"]
    )
    with run:
        render_run_tab(topic, max_attempts, use_memory)
    with evaluator:
        render_evaluator_tab()
    with memory:
        render_memory_tab()
    with architecture:
        render_architecture_tab()


if __name__ == "__main__":
    main()
