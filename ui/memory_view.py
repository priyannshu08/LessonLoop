"""The Memory tab — how a failure becomes a rule, and whether it earned its place.

DESIGN NOTE
-----------
The previous version showed four counters and a flat list of rules. That is
DISTRIBUTION — a snapshot of what exists right now. It cannot answer the
questions a viewer actually has:

    Where did this rule come from?
    Why this rule and not some other?
    How do I know it helped?
    What happens when it stops helping?

Lineage work draws the distinction sharply: distribution shows what is at each
stage; PROGRESSION shows how things moved between stages and where they dropped
out. Learning is a progression, so the tab is built as one:

    1. THE FUNNEL     — observations -> patterns -> threshold -> probation ->
                        active, with the drop-off visible at each stage
    2. PROVENANCE     — for each rule: the exact failures, on which topics, that
                        produced it, plus its track record since
    3. IN FLIGHT      — patterns still gathering evidence, and how far off they are
    4. IMPACT         — pass rate before vs after the rule existed
    5. WHAT HAPPENS NEXT — the lifecycle rules stated plainly

Every number is clickable through to the evidence beneath it, so nothing has to
be taken on trust.
"""

import json

import streamlit as st

from src.config import CHECKPOINT_LABELS, MEMORY_POLICY
from src.memory.signatures import describe_signature
from src.memory.store import MemoryStore
from ui.theme import chip


# =====================================================================
# 1. The funnel
# =====================================================================

def _funnel_row(label: str, count: int, total: int, note: str, colour: str) -> str:
    """One stage of the learning funnel, sized relative to the widest stage."""
    width = (count / total * 100) if total else 0
    return (
        f'<div class="funnel-row">'
        f'  <div class="funnel-label">{label}</div>'
        f'  <div class="funnel-bar-wrap">'
        f'    <div class="funnel-bar" style="width:{max(width, 2):.1f}%;'
        f'background:{colour}"></div>'
        f'    <span class="funnel-count">{count}</span>'
        f"  </div>"
        f'  <div class="funnel-note">{note}</div>'
        f"</div>"
    )


def render_funnel(memory: MemoryStore) -> None:
    """Observations narrowing down to active rules, stage by stage."""
    observations = memory.get_observations()
    failures = [o for o in observations if o.get("outcome") == "fail"]
    patterns = memory.signature_topic_counts()
    ready = {s: t for s, t in patterns.items()
             if len(t) >= MEMORY_POLICY.promotion_topic_threshold}
    rules = memory.get_all_rules()
    active = [r for r in rules if r.get("status") != "superseded"]
    probation = [r for r in active if r.get("status") == "probation"]
    confirmed = [r for r in active if r.get("status") == "active"]
    retired = [r for r in rules if r.get("status") == "superseded"]

    st.markdown("#### How a failure becomes a rule")
    st.markdown(
        '<div class="note">Each stage filters the one above it. The narrowing is '
        'the design: most of what goes wrong is noise, and only a pattern that '
        'survives every filter is allowed to change how the system writes.</div>',
        unsafe_allow_html=True,
    )

    total = max(len(observations), 1)
    rows = [
        ("Observations recorded", len(observations),
         "every checkpoint result, pass or fail — never shown to a model", "var(--muted)"),
        ("Failures among them", len(failures),
         "only failures can become evidence", "var(--muted)"),
        ("Distinct patterns", len(patterns),
         "failures grouped by root cause, not by wording", "var(--warn)"),
        (f"Reached {MEMORY_POLICY.promotion_topic_threshold} different topics", len(ready),
         "the generalisation guard — same failure on unrelated subjects", "var(--warn)"),
        ("On probation", len(probation),
         "promoted, but provisional until it proves it helps", "var(--warn)"),
        ("Confirmed active", len(confirmed),
         "earned its place; injected into every draft", "var(--ok)"),
        ("Retired", len(retired),
         "evicted or superseded — kept on disk for provenance", "var(--muted)"),
    ]
    st.markdown(
        '<div class="funnel">'
        + "".join(_funnel_row(l, c, total, n, col) for l, c, n, col in rows)
        + "</div>",
        unsafe_allow_html=True,
    )


# =====================================================================
# 2. Provenance
# =====================================================================

def render_rule_provenance(memory: MemoryStore, rule: dict) -> None:
    """One rule, traced back to the exact failures that produced it."""
    status = rule.get("status", "active")
    state = {"active": "pass", "probation": "warn", "superseded": "idle"}[status]

    st.markdown(
        f'<div class="rule-hero">'
        f'{chip(status.upper(), state)}'
        f'<div class="rule-text">{rule["insight"]}</div>'
        f'<div class="rule-id">{rule["id"]} · created {rule["created_at"][:16]}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    # --- Lifecycle position -------------------------------------------
    stages = ["observed", "recurred", "distilled", "probation", "active"]
    reached = stages.index("active") if status == "active" else stages.index("probation")
    if status == "superseded":
        stages, reached = stages + ["retired"], 5
    st.markdown(
        '<div class="lifecycle">'
        + "".join(
            f'<span class="stage {"done" if i <= reached else ""}">{s}</span>'
            + ('<span class="stage-arrow">→</span>' if i < len(stages) - 1 else "")
            for i, s in enumerate(stages)
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown("**Where it came from**")
        topics = rule.get("evidence_runs", [])
        st.markdown(
            f'<div class="note">The same failure — '
            f'<code>{rule.get("signature", "?")}</code> — appeared on these '
            f'{len(topics)} unrelated topics. That is what made it a pattern '
            f'rather than one bad run.</div>',
            unsafe_allow_html=True,
        )

        # The actual failure text that produced this rule.
        evidence = [
            o for o in memory.get_observations()
            if o.get("signature") == rule.get("signature")
            and o.get("outcome") == "fail"
        ]
        for topic in topics:
            hits = [o for o in evidence if o["topic"] == topic]
            with st.expander(f"📄  {topic}  ·  {len(hits)} failure(s)", expanded=False):
                for o in hits[:3]:
                    st.caption(f"attempt {o.get('attempt_number', '?')}")
                    st.code(o["reason"], language=None, wrap_lines=True)

    with right:
        st.markdown("**Track record since**")
        runs = rule.get("runs_active", 0)
        passed = rule.get("runs_passed", 0)
        rate = passed / runs if runs else None

        a, b = st.columns(2)
        a.metric("Runs active", runs)
        b.metric("Passed", f"{rate:.0%}" if rate is not None else "—")

        if rule.get("status_reason"):
            st.success(rule["status_reason"]) if status == "active" else st.info(
                rule["status_reason"]
            )
        if status == "probation":
            remaining = max(MEMORY_POLICY.probation_runs - runs, 0)
            st.warning(
                f"Provisional. {remaining} more run(s) before it is confirmed or "
                f"dropped. If quality does not improve while it is active, it is "
                f"dropped and a *different* rule is tried."
            )


# =====================================================================
# 3. Patterns still gathering evidence
# =====================================================================

def render_in_flight(memory: MemoryStore) -> None:
    """Patterns that have not yet earned a rule — and how far off they are."""
    patterns = memory.signature_topic_counts()
    covered = {r.get("signature") for r in memory.get_active_rules()}
    threshold = MEMORY_POLICY.promotion_topic_threshold

    st.markdown("#### Patterns still gathering evidence")
    st.markdown(
        f'<div class="note">A failure needs to appear on <b>{threshold} different '
        f'topics</b> before it can become a rule. Topics rather than runs is the '
        f'whole guard: three failures on three runs of the same subject would '
        f'produce a rule fitted to that subject.</div>',
        unsafe_allow_html=True,
    )

    if not patterns:
        st.info("No failures recorded yet.")
        return

    for signature, topics in sorted(patterns.items(), key=lambda kv: -len(kv[1])):
        n = len(topics)
        is_covered = signature in covered
        escalated = memory.is_escalated(signature)

        state = "pass" if is_covered else ("fail" if escalated else "warn")
        label = (
            "RULE EXISTS" if is_covered
            else "ESCALATED" if escalated
            else f"{n}/{threshold} TOPICS"
        )
        st.markdown(
            f'<div class="pattern-row">{chip(label, state)}'
            f'<code>{signature}</code></div>',
            unsafe_allow_html=True,
        )
        st.progress(min(n / threshold, 1.0))
        st.caption(
            f"{describe_signature(signature)} — seen on: {', '.join(sorted(topics))}"
        )


# =====================================================================
# 4. Impact
# =====================================================================

def render_impact(memory: MemoryStore) -> None:
    """Pass rate before the first rule existed, versus after.

    Reported with its own caveat: this is observational, not a controlled
    experiment. The A/B harness runs the SAME topics with memory on and off,
    which is the honest version of this comparison.
    """
    runs = memory.get_run_history()
    first_rule_at = next(
        (i for i, r in enumerate(runs) if r.get("new_rules_learned")), None
    )
    if first_rule_at is None or first_rule_at == len(runs) - 1:
        return

    before, after = runs[: first_rule_at + 1], runs[first_rule_at + 1:]

    def stats(group):
        passed = sum(1 for r in group if r["final_passed"])
        attempts = sum(r["total_attempts"] for r in group) / len(group)
        return passed, len(group), passed / len(group), attempts

    bp, bn, brate, batt = stats(before)
    ap, an, arate, aatt = stats(after)

    st.markdown("#### Did it actually help?")
    cols = st.columns(4)
    # NOTE: st.metric's third positional argument IS `delta` — passing it both
    # positionally and by keyword raises TypeError. Always name it.
    cols[0].metric("Before — pass rate", f"{brate:.0%}", help=f"{bp} of {bn} runs")
    cols[1].metric(
        "After — pass rate", f"{arate:.0%}",
        delta=f"{(arate - brate) * 100:+.0f} pts", help=f"{ap} of {an} runs",
    )
    cols[2].metric("Before — attempts", f"{batt:.1f}", help="average per run")
    cols[3].metric(
        "After — attempts", f"{aatt:.1f}",
        delta=f"{aatt - batt:+.1f}", delta_color="inverse",
        help="average per run — lower is better",
    )

    st.caption(
        "⚠️ Observational, not controlled — the two groups contain different "
        "topics, so some of this gap is topic difficulty rather than the rule. "
        "`python -m evals.ab_memory` runs the same topics with memory on and off, "
        "which is the honest comparison."
    )

    flipped = [
        r["topic"] for r in after
        if r["final_passed"] and any(
            b["topic"] == r["topic"] and not b["final_passed"] for b in before
        )
    ]
    if flipped:
        st.success(
            "**Topics that failed before the rule and passed after it:** "
            + ", ".join(sorted(set(flipped)))
        )


# =====================================================================
# 5. The rules of the system, stated plainly
# =====================================================================

def render_policy() -> None:
    """What governs promotion, eviction and giving up — in one place."""
    with st.expander("What happens next — the rules governing memory", expanded=False):
        st.markdown(
            f"""
| Situation | What the system does |
|:---|:---|
| A failure happens once | Recorded as an observation. **No rule.** One sample is not a pattern. |
| Same failure on {MEMORY_POLICY.promotion_topic_threshold} different topics | Distilled into one universal rule, entered on **probation**. |
| A rule is on probation | Injected like any rule, but provisional for {MEMORY_POLICY.probation_runs} runs. |
| It improved quality | **Confirmed active.** |
| It did not help | **Dropped** — but the failure pattern stays open, and a *different* rule is tried next. The failed one is remembered so it is never re-proposed. |
| {MEMORY_POLICY.max_fix_attempts_per_signature} different rules all failed | **Escalated.** The system concludes this is not fixable by prompting and asks a human. |
| A {MEMORY_POLICY.max_active_rules + 1}th rule arrives | The rule with the **worst track record** is evicted. New rules are protected until they have evidence. |
| Anything is retired | Marked superseded with a reason and kept on disk. **Nothing is ever deleted.** |
"""
        )
        st.caption(
            f"The cap of {MEMORY_POLICY.max_active_rules} is not arbitrary: the "
            f"generator prompt has a budget of 10 constraints, the base prompt "
            f"uses 6, so memory gets the remaining 4. A test fails the build if "
            f"that relationship breaks."
        )


# =====================================================================
# Entry point
# =====================================================================

def render_memory_tab() -> None:
    memory = MemoryStore()
    rules = memory.get_all_rules()
    active = [r for r in rules if r.get("status") != "superseded"]

    st.markdown(
        '<div class="note">Memory has two tiers. <b>Observations</b> record what '
        'happened and are never shown to a model, so a noisy run costs nothing. '
        'Only a pattern that repeats across unrelated topics is promoted to a '
        '<b>rule</b> — the small set of instructions injected into every future '
        'draft.</div>',
        unsafe_allow_html=True,
    )
    st.divider()

    render_funnel(memory)
    st.divider()

    escalations = memory.get_escalations()
    if escalations:
        st.markdown("#### Escalated to a human")
        for signature, note in escalations.items():
            st.error(f"**`{signature}`** — {note['reason']}")
        st.divider()

    if active:
        st.markdown("#### Rules currently shaping every draft")
        for rule in active:
            render_rule_provenance(memory, rule)
            st.divider()
    else:
        st.info(
            "No rules yet. The system has not seen the same failure on "
            f"{MEMORY_POLICY.promotion_topic_threshold} different topics — which "
            f"is the point: it refuses to learn from a single run."
        )
        st.divider()

    render_impact(memory)
    st.divider()
    render_in_flight(memory)
    st.divider()

    retired = [r for r in rules if r.get("status") == "superseded"]
    if retired:
        with st.expander(f"Retired rules ({len(retired)}) — kept for provenance"):
            for rule in retired:
                st.markdown(f"**{rule['id']}** — {rule['insight']}")
                st.caption(rule.get("status_reason", ""))
                if rule.get("superseded_by"):
                    st.caption(f"replaced by {rule['superseded_by']}")

    render_policy()

    history = memory.get_run_history()
    if history:
        st.markdown("#### Every run")
        st.dataframe(
            [
                {
                    "Run": r.get("run_id", "—"),
                    "Topic": r.get("topic", "—"),
                    "Result": "PASSED" if r.get("final_passed") else "BEST EFFORT",
                    "Attempts": r.get("total_attempts"),
                    "Rules active": r.get("memory_rules_active_count", 0),
                    "Learned": len(r.get("new_rules_learned") or []),
                    "Sec": r.get("duration_seconds"),
                }
                for r in reversed(history)
            ],
            width="stretch",
            hide_index=True,
        )
