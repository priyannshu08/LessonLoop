"""The architecture diagrams, as monospaced box-drawing text.

WHY ASCII RATHER THAN SVG
-------------------------
An inline SVG version of this rendered as an empty panel twice: Streamlit's
markdown sanitiser strips <style> blocks and does not cascade CSS custom
properties into sanitised HTML, so every shape drew with no fill and no stroke.

Box-drawing text inside a code block has none of those failure modes. It cannot
be sanitised into invisibility, it picks up the theme's own code colours in both
light and dark mode, and monospaced glyphs stay legible under video compression
at sizes where fine SVG strokes disappear.

ALIGNMENT IS COMPUTED, NEVER HAND-COUNTED
-----------------------------------------
These diagrams interpolate live values from `src.config` — model names,
thresholds — so the numbers on screen can never drift from the numbers the
system enforces. But those values have varying widths, and a hand-spaced box
breaks the moment one of them changes, in a way only visible by eye. So every
border is built from its content by `box()` below.
"""

from src.config import (
    EVALUATOR_MODEL,
    GENERATOR_MODEL,
    MEMORY_POLICY,
    RUBRIC,
)

WIDTH = 71          # inner width of a full-width box
INDENT = 3


def box(lines: list[str], width: int = WIDTH, indent: int = INDENT, style: str = "single") -> str:
    """Draw a box around `lines`, padding every row to the same width."""
    pad = " " * indent
    inner = width - 2
    
    borders = {
        "single": ("┌", "─", "┐", "│", "└", "┘"),
        "double": ("╔", "═", "╗", "║", "╚", "╝"),
        "heavy":  ("┏", "━", "┓", "┃", "┗", "┛"),
        "rounded":("╭", "─", "╮", "│", "╰", "╯"),
    }
    tl, hz, tr, vt, bl, br = borders.get(style, borders["single"])
    
    rows = [f"{pad}{vt} {line[:inner].ljust(inner)} {vt}" for line in lines]
    return "\n".join([f"{pad}{tl}{hz * width}{tr}", *rows, f"{pad}{bl}{hz * width}{br}"])


def arrow(label: str = "", indent: int = 38) -> str:
    """A vertical connector, optionally labelled."""
    pad = " " * indent
    return f"{pad}│{'  ' + label if label else ''}\n{pad}▼"


def row(*placements: tuple[int, str]) -> str:
    """Build a line by placing text AT given columns.

    Counting spaces between markers is where every alignment bug in this file
    came from. Placing by absolute column removes the arithmetic entirely.
    """
    width = max(col + len(text) for col, text in placements)
    buf = [" "] * width
    for col, text in placements:
        buf[col:col + len(text)] = list(text)
    return "".join(buf).rstrip()


def span(start: int, end: int, fill: str = "─") -> tuple[int, str]:
    """A horizontal run from `start` up to (not including) `end`."""
    return start, fill * max(end - start, 0)


def side_by_side(left: list[str], right: list[str], width: int = 32,
                 gap: int = 6, indent: int = 3) -> tuple[str, int, int]:
    """Two boxes on one row. Returns the text plus each box's centre column.

    Returning the centres is the point: every connector above and below is
    positioned from them, so the branch lines cannot drift out of alignment when
    a label changes length.
    """
    pad = " " * indent
    inner = width - 2
    bar = f"{pad}┌{'─' * width}┐{' ' * gap}┌{'─' * width}┐"
    rows = [
        f"{pad}│ {(l or '')[:inner].ljust(inner)} │{' ' * gap}"
        f"│ {(r or '')[:inner].ljust(inner)} │"
        for l, r in zip(left + [""] * len(right), right + [""] * len(left))
    ][: max(len(left), len(right))]

    left_centre = indent + 1 + width // 2
    right_centre = indent + width + 2 + gap + width // 2
    foot = (
        f"{pad}└{'─' * (width // 2)}┬{'─' * (width - width // 2 - 1)}┘{' ' * gap}"
        f"└{'─' * (width // 2)}┬{'─' * (width - width // 2 - 1)}┘"
    )
    return "\n".join([bar, *rows, foot]), left_centre, right_centre


def at(col: int, text: str) -> str:
    """Place `text` starting at a column."""
    return " " * col + text


def pipeline_diagram() -> str:
    """The whole loop: input, generation, both tiers, retry, memory, output."""
    EVAL_BOXES, EVAL_L, EVAL_R = side_by_side(
        [
            "TIER 1 — MEASURED",
            "code, no LLM, fast",
            "· sentence length",
            "· formal register",
            "· jargon explained",
            "· teaching order",
        ],
        [
            "TIER 2 — JUDGED",
            f"1 call · {EVALUATOR_MODEL}",
            "· accurate & grounded",
            "· teaches by example",
            "· covers what/why/how",
            "· coherent flow",
        ],
        width=32, gap=4
    )
    EVAL_MID = (EVAL_L + EVAL_R) // 2

    BRANCH_BOXES, LEFT_COL, RIGHT_COL = side_by_side(
        [
            "COMPILE FEEDBACK", "",
            "records what failed, what",
            "was fixed since the last",
            "attempt, and what regressed",
        ],
        [
            "SELECT BEST", "",
            "ships the highest-scoring",
            "attempt, not necessarily",
            "the last one",
        ],
        width=32, gap=4
    )
    DIAMOND_COL = (LEFT_COL + RIGHT_COL) // 2

    return "\n".join([
        "",
        " " * 25 + "INPUT — a topic, any subject",
        arrow(),
        box([
            "🧠 INITIALIZE",
            "",
            f"loads long-term memory and injects up to "
            f"{MEMORY_POLICY.max_active_rules} learned rules",
            "into the generator's prompt",
        ], style="double"),
        arrow(),
        box([
            f"✍️ GENERATE                                 [ API CALL ]",
            "",
            "attempt 1   the 6 base rules + the topic",
            "attempt 2+  the previous draft",
            "            + ONLY what failed",
            "            + what was already tried, so it cannot",
            "              re-derive a repair that already failed",
        ], style="double"),
        row((EVAL_MID, "│")),
        row((EVAL_L, "┌"), span(EVAL_L + 1, EVAL_MID), (EVAL_MID, "┴"), span(EVAL_MID + 1, EVAL_R), (EVAL_R, "┐")),
        row((EVAL_L, "▼"), (EVAL_R, "▼")),
        EVAL_BOXES,
        row((EVAL_L, "│"), (EVAL_R, "│")),
        row((EVAL_L, "└"), span(EVAL_L + 1, EVAL_MID), (EVAL_MID, "┬"), span(EVAL_MID + 1, EVAL_R), (EVAL_R, "┘")),
        row((EVAL_MID, "▼")),
        box([
            "🔀 MERGE",
            "measured overrides judged",
            "anything unevaluated FAILS CLOSED",
        ], width=40, indent=EVAL_MID - 20, style="heavy"),
        arrow("6 binary verdicts", indent=DIAMOND_COL),
        at(DIAMOND_COL - 10, "╔═══════════════════╗"),
        at(DIAMOND_COL - 10, "║  do all 6 pass?   ║"),
        at(DIAMOND_COL - 10, "╚═══════════════════╝"),
        at(DIAMOND_COL, "│"),
        row((LEFT_COL, "┌"), span(LEFT_COL + 1, DIAMOND_COL),
            (DIAMOND_COL, "┴"), span(DIAMOND_COL + 1, RIGHT_COL),
            (RIGHT_COL, "┐")),
        row((LEFT_COL - 4, "NO"), (LEFT_COL, "│"),
            (RIGHT_COL, "│"), (RIGHT_COL + 2, "YES")),
        row((LEFT_COL, "▼"), (RIGHT_COL, "▼")),
        BRANCH_BOXES,
        row((LEFT_COL, "│"), (RIGHT_COL, "│")),
        row((LEFT_COL, "└──► back to GENERATE"), (RIGHT_COL, "│")),
        row((RIGHT_COL, "│")),
        row((LEFT_COL, "attempts exhausted"),
            span(LEFT_COL + 19, RIGHT_COL), (RIGHT_COL, "┤")),
        at(RIGHT_COL, "│"),
        at(RIGHT_COL, "▼"),
        box([
            "💾 CONSOLIDATE MEMORY",
            "",
            "every failure            ─►  an observation",
            f"same failure on {MEMORY_POLICY.promotion_topic_threshold} topics  ─►  becomes a rule",
            "a rule that doesn't help ─►  dropped, a different one tried",
            f"{MEMORY_POLICY.max_fix_attempts_per_signature} rules all failed      ─►  escalated to a human",
        ], style="double"),
        row((RIGHT_COL, "│ ──► observations.json")),
        row((RIGHT_COL, "│ ──► lessons_learned.json")),
        row((RIGHT_COL, "▼")),
        row((3, "┌"), span(4, RIGHT_COL), (RIGHT_COL, "┴"), span(RIGHT_COL + 1, RIGHT_COL + 3), (RIGHT_COL + 3, "► OUTPUT (lesson)")),
        row((3, "│"), (7, "Run N+1 loop")),
        row((3, "└"), span(4, 38), (38, "► INITIALIZE")),
        "",
    ])


def evaluation_diagram() -> str:
    """How one lesson is scored: the two tiers, and the merge rule."""
    left = [
        "TIER 1 · MEASURED",
        "code, no LLM",
        "",
        f"long sentences  ≤ {RUBRIC.max_long_sentence_ratio:.0%}",
        f"formal words    ≤ {RUBRIC.max_academic_words}",
        f"jargon explained within",
        f"  {RUBRIC.jargon_definition_window} sentences",
        "teaching order",
        "",
        "authoritative — the",
        "judge cannot overturn it",
    ]
    right = [
        "TIER 2 · JUDGED",
        "one LLM call",
        "",
        "accurate & grounded",
        "teaches by example",
        "covers what/why/how",
        "coherent flow",
        "",
        "",
        "evidence ─► reasoning",
        "         ─► verdict",
    ]
    w = 30
    rows = [
        f"   │ {l[:w].ljust(w)} │      │ {r[:w].ljust(w)} │"
        for l, r in zip(left, right)
    ]
    bar = f"   ┌{'─' * (w + 2)}┐      ┌{'─' * (w + 2)}┐"
    end = f"   └{'─' * (w + 2)}┘      └{'─' * (w + 2)}┘"

    return "\n".join([
        "",
        " " * 34 + "a lesson",
        " " * 34 + "    │",
        " " * 10 + "┌─────────────────────┴─────────────────────┐",
        " " * 10 + "▼                                           ▼",
        bar, *rows, end,
        " " * 10 + "│                                           │",
        " " * 10 + "└─────────────────────┬─────────────────────┘",
        " " * 34 + "    ▼",
        box([
            "MERGE",
            "",
            "measured beats judged",
            "anything unevaluated FAILS CLOSED — an unjudged",
            "criterion must never pass by default",
        ], width=50, indent=20),
        "",
        " " * 22 + "6 binary verdicts · all six must pass",
        "",
    ])


def memory_diagram() -> str:
    """How an observation becomes a rule — and how a rule leaves."""
    return "\n".join([
        "",
        "   a checkpoint fails",
        "          │",
        "          ▼",
        box([
            "📝 OBSERVATION",
            "episodic · unbounded · NEVER shown to a model,",
            "so a noisy run costs nothing",
        ], width=54, indent=6, style="rounded"),
        "          │   grouped by root cause, not by wording",
        "          ▼",
        box([
            "🔍 PATTERN",
            "e.g. no_unexplained_jargon::undefined_terms",
        ], width=54, indent=6, style="rounded"),
        "          │",
        f"          │   seen on {MEMORY_POLICY.promotion_topic_threshold} DIFFERENT topics?",
        "          │   topics, not runs — three failures on three runs of",
        "          │   one subject would fit the rule to that subject",
        "     ┌────┴─────┐",
        "     NO         YES",
        "     │          │",
        "     ▼          ▼",
        "  ┌────────┐  ┌────────────────────────────────────────────────────┐",
        "  │ WAIT   │  │ DISTIL                                [ API CALL ] │",
        "  │        │  │ one universal rule, at most two sentences,         │",
        "  │ for    │  │ knowing which earlier rules already failed here    │",
        "  │ more   │  └──────────────────┬─────────────────────────────────┘",
        "  │ topics │                     │",
        "  └────────┘                     ▼",
        box([
            "🔬 PROBATION",
            f"injected like any rule, but provisional for "
            f"{MEMORY_POLICY.probation_runs} runs",
        ], width=54, indent=6, style="double"),
        "          │",
        "     ┌────┴─────┐",
        "  helps      doesn't help",
        "     │            │",
        "     ▼            ▼",
        "  ┌────────┐   ┌──────────────────────────────────────────┐",
        "  │ ACTIVE │   │ DROPPED — the pattern stays OPEN.        │",
        "  └───┬────┘   │ A DIFFERENT rule is tried next, and the  │",
        "      │        │ failed one is remembered so it is never  │",
        "      │        │ re-proposed.                             │",
        "      │        └──────────────────┬───────────────────────┘",
        f"      │                           │  after "
        f"{MEMORY_POLICY.max_fix_attempts_per_signature} failed rules",
        "      │                           ▼",
        "      │        ┌──────────────────────────────────────────┐",
        "      │        │ ESCALATED — not fixable by prompting.    │",
        "      │        │ The gate or the model is the limit.      │",
        "      │        │ Ask a human.                             │",
        "      │        └──────────────────────────────────────────┘",
        "      │",
        f"      │  a {MEMORY_POLICY.max_active_rules + 1}th rule arrives?",
        "      ▼",
        box([
            "♻️ EVICT the worst track record.",
            "New rules are protected until they have evidence.",
            "Nothing is ever deleted — a retired rule keeps a",
            "pointer to whatever replaced it.",
        ], width=54, indent=6, style="heavy"),
        "",
    ])


def state_diagram() -> str:
    """How LangGraph manages state between the agents."""
    return "\n".join([
        "",
        "           ┌───────────────────────────────────────────┐",
        "           │ LangGraph Orchestrator                    │",
        "           └──────────────────┬────────────────────────┘",
        "                              │",
        "                    hands the State object",
        "                              ▼",
        box([
            "STATE DICTIONARY",
            "{",
            '  "topic": "Introduction to RAG",',
            '  "draft": "...",',
            '  "evaluations": [...],',
            '  "critique": "..."',
            "}",
        ], width=53, indent=3, style="double"),
        "            ▲                 │                 ▲",
        "            │                 │                 │",
        "      reads topic,       reads draft,     reads evaluations,",
        "     writes draft     writes evaluations   writes critique",
        "            │                 │                 │",
        "            ▼                 ▼                 ▼",
        "     ┌────────────┐    ┌────────────┐    ┌────────────┐",
        "     │ GENERATOR  │    │ EVALUATOR  │    │ FEEDBACK   │",
        "     │ AGENT      │    │ AGENT      │    │ COMPILER   │",
        "     └────────────┘    └────────────┘    └────────────┘",
        "",
    ])
