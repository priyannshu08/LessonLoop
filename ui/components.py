"""Reusable render blocks for the app.

The organising idea is a TRACE: a run is a sequence of attempts, and each
attempt is a bundle of (draft → metrics → six verdicts → feedback). Good agent
observability shows that structure directly instead of printing prose, so the
reader can see where the loop turned and why.
"""

import streamlit as st

from src.config import CHECKPOINT_NAMES, RUBRIC
from ui.theme import chip, delta, gauge

# Labels and tiers live in config so the UI and the pipeline cannot disagree
# about what a checkpoint is called or which tier decides it.
from src.config import CHECKPOINT_LABELS as LABELS, CHECKPOINT_TIERS as TIERS

NODE_FLOW = [
    ("initialize", "Load memory"),
    ("generate", "Generate"),
    ("evaluate", "Evaluate"),
    ("compile_feedback", "Compile feedback"),
    ("select_best", "Select best"),
    ("prompt_optimizer", "Consolidate"),
]


def flow_diagram(active: str = "", completed: set[str] | None = None) -> None:
    """The pipeline as nodes, highlighting where the run currently is."""
    completed = completed or set()
    parts = []
    for i, (key, label) in enumerate(NODE_FLOW):
        cls = "active" if key == active else ("done" if key in completed else "")
        parts.append(f'<span class="node {cls}">{label}</span>')
        if i < len(NODE_FLOW) - 1:
            parts.append('<span class="arrow">→</span>')
    st.markdown(f'<div class="flow">{"".join(parts)}</div>', unsafe_allow_html=True)


def checkpoint_row(checkpoints: list[dict]) -> None:
    """Six verdicts as a single scannable row."""
    by_name = {c["name"]: c for c in checkpoints}
    chips = []
    for name in CHECKPOINT_NAMES:
        cp = by_name.get(name)
        state = "idle" if cp is None else ("pass" if cp["passed"] else "fail")
        chips.append(chip(LABELS.get(name, name), state))
    st.markdown(f'<div class="cp-grid">{"".join(chips)}</div>', unsafe_allow_html=True)


def metric_strip(metrics: dict, previous: dict | None = None) -> None:
    """Key numbers with movement since the previous attempt."""
    if not metrics:
        return
    previous = previous or {}
    spec = [
        ("Words", "word_count", 0, False),
        ("Words / sentence", "avg_sentence_words", 1, True),
        ("Long sentences", "long_sentence_ratio", 2, True),
        # Shown for context only — reading grade gates nothing. See
        # src/evaluation/deterministic.py for why.
        ("Reading grade", "fk_grade", 1, True),
    ]
    cells = []
    for label, key, places, lower_better in spec:
        if key not in metrics:
            continue
        value = metrics[key]
        shown = f"{value:.{places}f}" if places else f"{int(value)}"
        change = (
            value - previous[key]
            if key in previous and isinstance(previous.get(key), (int, float))
            else None
        )
        cells.append(
            f'<div class="metric"><span class="label">{label}</span>'
            f'<span class="value">{shown}</span>'
            f"{delta(change, lower_better)}</div>"
        )
    st.markdown(f'<div class="metrics">{"".join(cells)}</div>', unsafe_allow_html=True)


def threshold_gauges(metrics: dict) -> None:
    """Measured values against the thresholds they must clear.

    Only things that actually GATE appear here. A bar implies a bar to clear, so
    showing reading grade as a gauge would misrepresent it — it is reported in
    the metric strip as context instead.
    """
    if not metrics:
        return
    html = ""
    if "long_sentence_ratio" in metrics:
        html += gauge(
            "Long sentences",
            metrics["long_sentence_ratio"],
            RUBRIC.max_long_sentence_ratio,
        )
    if "academic_words" in metrics:
        html += gauge(
            "Formal words", len(metrics["academic_words"]), RUBRIC.max_academic_words
        )
    st.markdown(html, unsafe_allow_html=True)


def checkpoint_detail(checkpoints: list[dict]) -> None:
    """Per-checkpoint evidence and reasoning, failures first.

    Failures lead because they are what the reader acts on; passes collapse so
    they stay available without occupying attention.
    """
    ordered = sorted(checkpoints, key=lambda c: c["passed"])
    for cp in ordered:
        label = LABELS.get(cp["name"], cp["name"])
        tier = TIERS.get(cp["name"], "")
        icon = "✓" if cp["passed"] else "✕"
        with st.expander(f"{icon}  {label}  ·  {tier}", expanded=not cp["passed"]):
            st.markdown(f"**Verdict** — {cp['reason']}")
            if cp.get("evidence"):
                st.caption("Evidence")
                st.code(cp["evidence"], language=None, wrap_lines=True)


def attempt_card(
    snapshot: dict,
    previous: dict | None = None,
    shipped: bool = False,
) -> None:
    """One attempt: header, metrics, verdicts, and what changed since last time."""
    number = snapshot.get("attempt_number", "?")
    passed = snapshot.get("overall_pass", False)
    passed_count = snapshot.get("passed_count", 0)

    classes = f"attempt {'pass' if passed else 'fail'}{' shipped' if shipped else ''}"
    badges = chip("PASSED" if passed else "FAILED", "pass" if passed else "fail")
    if shipped:
        badges += " " + chip("SHIPPED", "warn")

    st.markdown(
        f'<div class="{classes}"><div class="attempt-head">'
        f'<span class="attempt-title">Attempt {number}</span>{badges}'
        f'<span class="note">{passed_count}/6 checkpoints</span>'
        f"</div></div>",
        unsafe_allow_html=True,
    )

    metric_strip(snapshot.get("metrics", {}), (previous or {}).get("metrics", {}))
    checkpoint_row(snapshot.get("checkpoints", []))

    fixed = snapshot.get("checkpoints_fixed") or []
    regressed = snapshot.get("checkpoints_regressed") or []
    if fixed or regressed:
        bits = []
        if fixed:
            bits.append(
                "**Fixed:** " + ", ".join(LABELS.get(f, f) for f in fixed)
            )
        if regressed:
            # Regressions are surfaced, never hidden: refinement is not
            # monotonic, and an audit trail that conceals that is misleading.
            bits.append(
                "**Regressed:** " + ", ".join(LABELS.get(r, r) for r in regressed)
            )
        st.markdown(
            f'<div class="note" style="margin-top:.4rem">{" · ".join(bits)}</div>',
            unsafe_allow_html=True,
        )

    if snapshot.get("checkpoints"):
        with st.expander("Checkpoint detail", expanded=False):
            checkpoint_detail(snapshot["checkpoints"])
    if snapshot.get("full_lesson"):
        with st.expander("Draft produced by this attempt", expanded=False):
            st.markdown(snapshot["full_lesson"])
    if snapshot.get("feedback_sent_to_generator"):
        with st.expander("Feedback sent into the next attempt", expanded=False):
            st.code(snapshot["feedback_sent_to_generator"], language=None, wrap_lines=True)


def rule_card(rule: dict) -> None:
    """A memory rule with its provenance and track record."""
    status = rule.get("status", "active")
    state = {"active": "pass", "probation": "warn", "superseded": "idle"}.get(status, "idle")
    runs = rule.get("runs_active", 0)
    rate = (rule.get("runs_passed_first_attempt", 0) / runs) if runs else None

    meta = [f"{rule['id']}", f"active for {runs} run(s) so far"]
    if rate is not None:
        meta.append(f"{rate:.0%} first-attempt pass")
    if rule.get("evidence_runs"):
        meta.append(f"promoted from {len(rule['evidence_runs'])} topics: "
                    f"{', '.join(rule['evidence_runs'])}")
    if rule.get("superseded_by"):
        meta.append(f"replaced by {rule['superseded_by']}")

    st.markdown(
        f'<div class="rule {"superseded" if status == "superseded" else ""}">'
        f'{chip(status.upper(), state)}'
        f'<div style="margin-top:.4rem">{rule["insight"]}</div>'
        f'<div class="rule-meta">{"  ·  ".join(meta)}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )
    if rule.get("status_reason"):
        st.caption(rule["status_reason"])
