"""Visual system for the app — one place for colour, type and component CSS.

DESIGN NOTES
------------
The palette is semantic rather than decorative. In an evaluation tool the
reader's first question is always "did it pass?", so pass/fail carries the only
saturated colour in the interface; everything else is greyscale. That keeps
verdicts scannable at a glance instead of competing with chrome.

Colours are defined as CSS custom properties and adapt to the viewer's Streamlit
theme, so the app is legible in both light and dark mode rather than assuming
one.
"""

import streamlit as st

CSS = """
<style>
html, body, [class*='st-'] { font-size: 17px; }
:root {
    --ok:        #0b7a33;
    --ok-bg:     rgba(16, 137, 62, 0.10);
    --bad:       #b3282c;
    --bad-bg:    rgba(209, 52, 56, 0.10);
    --warn:      #b8860b;
    --warn-bg:   rgba(184, 134, 11, 0.10);
    --muted:     #52525b;
    --line:      rgba(128, 128, 128, 0.22);
    --card:      rgba(128, 128, 128, 0.05);
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
}

/* ---------- Header ---------- */
.hero {
    border-bottom: 1px solid var(--line);
    padding-bottom: 0.9rem;
    margin-bottom: 1.4rem;
}
.hero h1 {
    font-size: 2.1rem; font-weight: 650;
    margin: 0 0 0.2rem 0; letter-spacing: -0.02em;
}
.hero p { color: var(--muted); font-size: 1.15rem; margin: 0; }

/* ---------- Verdict chips ---------- */
.chip {
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.28rem 0.7rem; border-radius: 999px;
    font-size: 1.05rem; font-weight: 600;
    border: 1px solid transparent; white-space: nowrap;
}
.chip.pass { color: var(--ok);   background: var(--ok-bg);   border-color: var(--ok); }
.chip.fail { color: var(--bad);  background: var(--bad-bg);  border-color: var(--bad); }
.chip.warn { color: var(--warn); background: var(--warn-bg); border-color: var(--warn); }
.chip.idle { color: var(--muted); background: var(--card); border-color: var(--line); }

/* ---------- Attempt card ---------- */
.attempt {
    border: 1px solid var(--line); border-left: 3px solid var(--muted);
    border-radius: 8px; padding: 0.85rem 1rem;
    margin-bottom: 0.7rem; background: var(--card);
}
.attempt.pass { border-left-color: var(--ok); }
.attempt.fail { border-left-color: var(--bad); }
.attempt.shipped { border-left-width: 5px; }
.attempt-head {
    display: flex; align-items: center; gap: 0.6rem;
    flex-wrap: wrap; margin-bottom: 0.55rem;
}
.attempt-title { font-weight: 700; font-size: 1.35rem; }

/* ---------- Metric strip ---------- */
.metrics { display: flex; gap: 1.4rem; flex-wrap: wrap; margin: 0.5rem 0 0.2rem 0; }
.metric { display: flex; flex-direction: column; }
.metric .label {
    font-size: 0.95rem; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--muted); font-weight: 600;
}
.metric .value { font-family: var(--mono); font-size: 1.75rem; font-weight: 700; }
.metric .delta { font-family: var(--mono); font-size: 0.95rem; }
.delta.up   { color: var(--bad); }
.delta.down { color: var(--ok); }
.delta.flat { color: var(--muted); }

/* ---------- Checkpoint grid ---------- */
.cp-grid { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-top: 0.45rem; }

/* ---------- Gauge bar ---------- */
.gauge { margin: 0.4rem 0 0.8rem 0; }
.gauge-track {
    height: 10px; border-radius: 4px;
    background: var(--line); overflow: hidden;
}
.gauge-fill { height: 100%; border-radius: 4px; }
.gauge-label {
    display: flex; justify-content: space-between;
    font-size: 1.0rem; color: var(--muted); margin-bottom: 0.2rem;
}
.gauge-label b { font-family: var(--mono); color: inherit; }

/* ---------- Tier badge ---------- */
.tier {
    font-family: var(--mono); font-size: 0.9rem; font-weight: 700;
    padding: 0.12rem 0.45rem; border-radius: 4px;
    background: var(--card); border: 1px solid var(--line); color: var(--muted);
}

/* ---------- Flow diagram ---------- */
.flow { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; margin: 0.6rem 0 1rem 0; }
.node {
    padding: 0.4rem 0.75rem; border: 1px solid var(--line);
    border-radius: 6px; font-size: 1.1rem; font-weight: 600;
    background: var(--card); white-space: nowrap;
}
.node.active { border-color: var(--ok); color: var(--ok); background: var(--ok-bg); }
.node.done   { opacity: 0.55; }
.arrow { color: var(--muted); font-size: 1.15rem; }

/* ---------- Rule card ---------- */
.rule {
    border: 1px solid var(--line); border-radius: 8px;
    padding: 0.7rem 0.85rem; margin-bottom: 0.55rem; background: var(--card);
}
.rule.superseded { opacity: 0.5; }
.rule-meta {
    font-family: var(--mono); font-size: 0.95rem;
    color: var(--muted); margin-top: 0.4rem;
}

/* ---------- Learning funnel ---------- */
/* Progression, not distribution: each stage is sized against the widest, so
   the narrowing from raw observations down to active rules is the message. */
.funnel { margin: 0.6rem 0 0.4rem 0; }
.funnel-row {
    display: grid; grid-template-columns: 15rem 1fr 1.6fr;
    align-items: center; gap: 1rem; padding: 0.42rem 0;
    border-bottom: 1px solid var(--line);
}
.funnel-label { font-weight: 650; font-size: 1.05rem; }
.funnel-bar-wrap { display: flex; align-items: center; gap: 0.7rem; }
.funnel-bar { height: 20px; border-radius: 4px; min-width: 3px; }
.funnel-count {
    font-family: var(--mono); font-weight: 700; font-size: 1.3rem;
}
.funnel-note { color: var(--muted); font-size: 0.98rem; line-height: 1.4; }

/* ---------- Rule hero ---------- */
.rule-hero {
    border: 1px solid var(--line); border-left: 5px solid var(--ok);
    border-radius: 8px; padding: 1rem 1.15rem; background: var(--card);
    margin-bottom: 0.7rem;
}
.rule-text {
    font-size: 1.35rem; font-weight: 600; line-height: 1.45;
    margin: 0.55rem 0 0.4rem 0;
}
.rule-id { font-family: var(--mono); font-size: 0.92rem; color: var(--muted); }

/* ---------- Lifecycle strip ---------- */
.lifecycle {
    display: flex; align-items: center; gap: 0.45rem;
    flex-wrap: wrap; margin: 0.2rem 0 1rem 0;
}
.stage {
    padding: 0.28rem 0.7rem; border-radius: 999px;
    border: 1px solid var(--line); color: var(--muted);
    font-size: 0.95rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.03em;
}
.stage.done {
    border-color: var(--ok); color: var(--ok); background: var(--ok-bg);
}
.stage-arrow { color: var(--muted); }

/* ---------- Pattern row ---------- */
.pattern-row {
    display: flex; align-items: center; gap: 0.7rem;
    margin: 0.7rem 0 0.3rem 0; flex-wrap: wrap;
}
.pattern-row code { font-size: 1rem; }

/* ---------- Tier cards ---------- */
.tier-card {
    border: 1px solid var(--line); border-radius: 8px;
    padding: 0.95rem 1.1rem; background: var(--card);
    font-size: 1.05rem; line-height: 1.55; min-height: 12rem;
}
.tier-card.measured { border-left: 5px solid var(--ok); }
.tier-card.judged   { border-left: 5px solid var(--warn); }

/* ---------- Misc ---------- */
.note { color: var(--muted); font-size: 1.05rem; line-height: 1.55; }
.stTabs [data-baseweb="tab"] { font-weight: 600; }
div[data-testid="stMetricValue"] { font-family: var(--mono); }
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------
# Small HTML builders — keep markup out of the page logic
# ---------------------------------------------------------------------

def chip(label: str, state: str = "idle") -> str:
    """state: pass | fail | warn | idle"""
    icon = {"pass": "✓", "fail": "✕", "warn": "!", "idle": "·"}[state]
    return f'<span class="chip {state}">{icon} {label}</span>'


def gauge(label: str, value: float, limit: float, lower_is_better: bool = True) -> str:
    """A bar showing a metric against its threshold.

    A number alone ("FK grade 8.4") does not say whether it is acceptable. The
    bar shows the threshold, so the reader sees the verdict and the margin at
    once, which is what makes a metric actionable.
    """
    ratio = min(value / limit, 1.4) if limit else 0
    over = value > limit if lower_is_better else value < limit
    colour = "var(--bad)" if over else "var(--ok)"
    width = min(ratio * 100, 100)
    return (
        f'<div class="gauge">'
        f'<div class="gauge-label"><span>{label}</span>'
        f'<span><b>{value}</b> / {limit}</span></div>'
        f'<div class="gauge-track">'
        f'<div class="gauge-fill" style="width:{width}%;background:{colour}"></div>'
        f"</div></div>"
    )


def delta(value: float | None, lower_is_better: bool = True, suffix: str = "") -> str:
    """Signed change, coloured by whether it moved the right way."""
    if value is None:
        return ""
    if abs(value) < 1e-9:
        return '<span class="delta flat">no change</span>'
    good = (value < 0) if lower_is_better else (value > 0)
    css = "down" if good else "up"
    arrow = "▼" if value < 0 else "▲"
    return f'<span class="delta {css}">{arrow} {abs(value):.1f}{suffix}</span>'
