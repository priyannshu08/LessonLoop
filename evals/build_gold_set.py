"""Build the evaluator's gold set: lessons with KNOWN, labelled defects.

WHY CONSTRUCTED NEGATIVES
-------------------------
A gold set needs ground truth. Hand-labelling real lessons gives honest labels
but weak ones — reasonable people disagree about whether a lesson "teaches by
example" well enough, so disagreement between judge and label may be the label's
fault rather than the judge's.

So most negatives here are CONSTRUCTED: start from a lesson that passes, then
inject one specific, unambiguous defect. Ground truth is then true by
construction — if we deleted every example, `teaches_by_example` must fail, and
a judge that passes it is definitively wrong.

The set deliberately includes SUBTLE negatives, not only blatant ones. A judge
that only catches obvious damage is useless, because obvious damage is rare in
real generations.

Positives are real lessons that passed a full pipeline run and were manually
read through.

Usage:
    python -m evals.build_gold_set
"""

import json
import re
from pathlib import Path

from rich.console import Console

from src.evaluation.deterministic import run_deterministic_gates

console = Console()

EVALS_DIR = Path(__file__).parent
SAMPLES_DIR = EVALS_DIR / "samples"
GOLD_SET = EVALS_DIR / "gold_set.jsonl"

from src.config import CHECKPOINT_NAMES as ALL_CHECKPOINTS


# =====================================================================
# Defect injectors — each breaks exactly ONE checkpoint
# =====================================================================

def _second_section(text: str) -> str:
    """Heading of the second section, used as a topic-agnostic anchor.

    Uses the lesson's own dominant heading level rather than assuming '##'.
    """
    level = _dominant_heading_level(text)
    headings = re.findall(rf"^{level} .*$", text, flags=re.MULTILINE)
    return headings[1] if len(headings) > 1 else ""


def inject_factual_error(text: str) -> str:
    """A wrong MECHANISM — the kind of error that misleads a beginner.

    The claim must be unambiguously false NO MATTER WHAT the base lesson's topic
    is. An earlier version asserted that the system "retrains its weights on
    every question", which is wrong for a retrieval system but reads as nearly
    true for a lesson about neural-network training — so the judge passed it and
    the harness blamed the judge for an ambiguous fixture.

    An erase-and-relearn-from-scratch-per-request cycle is false for every topic
    this pipeline teaches, while still being a mechanism claim rather than a
    trivia error.
    """
    lie = (
        "\nEvery single time you ask a question, the system first deletes "
        "everything it learned during training. It then relearns all of that "
        "knowledge from scratch in under one second, before it answers. This "
        "delete-and-relearn step runs on every request.\n\n"
    )

    # Place it inside the "how it works" section. An earlier version put it in
    # the hook, where the surrounding text is all analogy — the claim extractor
    # read it as teaching scaffolding and skipped it, so it was never checked.
    # Real hallucinations appear where mechanism is explained, so that is where
    # a realistic fixture belongs.
    level = _dominant_heading_level(text)
    how = re.search(
        rf"^{level} .*\bhow\b.*$", text, re.IGNORECASE | re.MULTILINE
    )
    if how:
        end = how.end()
        return text[:end] + "\n" + lie + text[end:]

    anchor = _second_section(text)
    return text.replace(anchor, lie + anchor, 1) if anchor else text + lie


def inject_hard_language(text: str) -> str:
    """Swap plain words for academic register, keeping meaning identical."""
    # Deliberately broad: the fixture must land clearly OVER the budget, not at
    # it. An earlier version produced exactly the limit, so the gate correctly
    # passed it and the harness reported a correct gate as a miss.
    swaps = {
        r"\buses\b": "utilises", r"\bhelps\b": "facilitates",
        r"\bmakes\b": "engenders", r"\bfinds\b": "ascertains",
        r"\bgood\b": "efficacious", r"\bidea\b": "conceptualisation",
        r"\bway\b": "methodology", r"\bshows\b": "demonstrates",
        r"\bbig\b": "substantial", r"\bthen\b": "subsequently",
        r"\balso\b": "furthermore", r"\bstarts\b": "commences",
        r"\bbest\b": "optimal", r"\bmany\b": "myriad",
        r"\bneeded\b": "requisite", r"\bincludes\b": "encompasses",
        r"\babout\b": "pertaining to", r"\bexplain\b": "elucidate",
        r"\bbuilt.in\b": "inherent", r"\bimportant\b": "salient",
    }
    for pattern, replacement in swaps.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def inject_undefined_jargon(text: str) -> str:
    """Strip the Key Term callouts, leaving the terms in place but unexplained."""
    # Remove explanatory phrasing so terms remain but their meaning does not.
    for marker in [r"\*\*Key Term:[^\n]*\n", r"[^.]*\bthink of\b[^.]*\.",
                   r"[^.]*\bis a way of\b[^.]*\.", r"[^.]*\bmeans\b[^.]*\."]:
        text = re.sub(marker, "", text, flags=re.IGNORECASE)
    dense = (
        "\nThe system applies embeddings and vector similarity search over a "
        "corpus, then conditions an autoregressive decoder on the retrieved "
        "context to perform inference via its underlying neural architecture.\n\n"
    )
    anchor = _second_section(text)
    return text.replace(anchor, anchor + dense, 1) if anchor else text + dense


def strip_examples(text: str) -> str:
    """Remove the passages that walk a reader through a scenario.

    Targets the language of worked examples rather than specific heading names,
    so it works whatever structure the base lesson happens to use.
    """
    level = _dominant_heading_level(text)
    # The heading line must be matched with [^\n]* — a bare `.*` under DOTALL is
    # greedy across newlines and swallowed the entire rest of the document,
    # turning a "no examples" fixture into a 78-word stub with a completely
    # different defect.
    text = re.sub(
        rf"^{level} [^\n]*(?:example|use case|in action|in practice)[^\n]*\n"
        rf".*?(?=^{level} |\Z)",
        "", text, flags=re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    for cue in ["for example", "imagine", "suppose", "let's say", "say you",
                "picture this", "think of"]:
        text = re.sub(rf"[^.]*\b{cue}\b[^.]*\.", "", text, flags=re.IGNORECASE)
    return text


def truncate(text: str) -> str:
    """Cut the lesson far below the viability floor.

    Length is not a quality criterion in this rubric — but a 120-word "lesson"
    genuinely cannot teach what it is, why it matters and how it works, which is
    what `covers_key_points` asserts.
    """
    from src.config import RUBRIC
    return " ".join(text.split()[: RUBRIC.min_viable_words // 2])


def _dominant_heading_level(text: str) -> str:
    """The heading level a lesson actually uses for its sections.

    Lessons vary between '##' and '###' for section headings. Assuming '##'
    silently produced a "scrambled" sample identical to the original, so the
    gold set asserted a failure that had never been injected — the harness then
    reported a correct judge as wrong. Detect the level instead of assuming it.
    """
    counts = {
        level: len(re.findall(rf"^{level} ", text, re.MULTILINE))
        for level in ("###", "##")
    }
    return max(counts, key=counts.get) if any(counts.values()) else "##"


def scramble_flow(text: str) -> str:
    """Reverse section order so concepts arrive before their prerequisites."""
    level = _dominant_heading_level(text)
    parts = re.split(rf"(?=^{level} )", text, flags=re.MULTILINE)
    head, sections = parts[0], parts[1:]
    if len(sections) < 3:
        raise ValueError(
            f"Cannot scramble: found only {len(sections)} '{level}' section(s). "
            f"A sample that is not actually broken would poison the gold set."
        )
    return head + "".join(reversed(sections))


DEFECTS = [
    ("factual_error", inject_factual_error, "accurate_and_grounded",
     "Injected a wrong mechanism: claims the system deletes and relearns everything per request."),
    ("hard_language", inject_hard_language, "beginner_friendly_language",
     "Plain words swapped for academic register; meaning unchanged."),
    ("undefined_jargon", inject_undefined_jargon, "no_unexplained_jargon",
     "Key Term callouts stripped; dense jargon paragraph added."),
    # KNOWN-WEAK FIXTURE. Mechanically removing "examples" is unreliable: a
    # numbered step-by-step process is itself a walk-through, so stripping cue
    # phrases leaves something that still teaches by example — and the judge is
    # arguably right to pass it. Isolating this defect needs a hand-written
    # negative (abstract definitions only, no scenario), which is future work.
    # Reported rather than tuned away, so the number stays honest.
    ("no_examples", strip_examples, "teaches_by_example",
     "Example sections and cue phrases removed. WEAK FIXTURE — a step-by-step "
     "process survives and still reads as a walk-through."),
    ("too_short", truncate, "covers_key_points",
     "Truncated far below the length any real lesson needs."),
    ("scrambled_flow", scramble_flow, "coherent_teaching_flow",
     "Section order reversed; concepts precede their prerequisites."),
]


def find_base_lessons() -> list[tuple[Path, str]]:
    """Real lessons that passed, and still clear TODAY's deterministic gates.

    Re-verifying matters: some runs passed under an older, looser rubric. A
    521-word lesson that passed when the floor was lower would be labelled
    "all checkpoints pass" here and then be correctly failed by the current
    gates — making a right answer look like a judge error. The gold set must
    never contain a label the current system is right to disagree with.
    """
    outputs = Path(__file__).parent.parent / "outputs"
    verified = []

    for eval_file in outputs.glob("*/final_evaluation.json"):
        try:
            if not json.load(open(eval_file)).get("overall_pass"):
                continue
            lesson = eval_file.parent / "lesson.md"
            if not lesson.exists():
                continue

            summary = eval_file.parent / "run_summary.md"
            topic = "the lesson topic"
            if summary.exists():
                match = re.search(r"\*\*Topic:\*\*\s*(.+)", summary.read_text())
                if match:
                    topic = match.group(1).strip()

            failures, _ = run_deterministic_gates(lesson.read_text(), topic)
            if failures:
                console.print(
                    f"  [yellow]skipping {eval_file.parent.name}[/yellow] — passed "
                    f"under an older rubric, now fails: "
                    f"{', '.join(f.name for f in failures)}"
                )
                continue
            verified.append((lesson, topic))
        except Exception:
            continue

    return sorted(verified, key=lambda p: p[0].stat().st_mtime, reverse=True)


def main():
    SAMPLES_DIR.mkdir(exist_ok=True)
    bases = find_base_lessons()
    if not bases:
        console.print("[red]No passing lesson found in outputs/ to build from.[/red]")
        console.print("Run the pipeline until a lesson passes, then re-run this.")
        return

    base_path, base_topic = bases[0]
    base_text = base_path.read_text()
    console.print(f"\n[bold]Base lesson:[/bold] {base_path.parent.name} "
                  f"({len(base_text.split())} words) — topic: {base_topic}\n")

    entries = []

    # --- Positives: real lessons that passed, re-verified --------------
    for i, (path, topic) in enumerate(bases[:3], 1):
        name = f"pass_{i:02d}.md"
        (SAMPLES_DIR / name).write_text(path.read_text())
        entries.append({
            "id": f"pos_{i:02d}",
            "sample": name,
            "topic": topic,
            "labels": {cp: True for cp in ALL_CHECKPOINTS},
            "note": f"Real lesson that passed a full run ({path.parent.name}) and "
                    f"still clears the current deterministic gates.",
        })

    # --- Negatives: one injected defect each ---------------------------
    for i, (slug, injector, broken, note) in enumerate(DEFECTS, 1):
        name = f"fail_{slug}.md"
        damaged = injector(base_text)

        # Self-check: a "negative" identical to the base asserts a defect that
        # was never injected, and the harness then reports a CORRECT judge as
        # wrong. This exact bug produced a false "missed" verdict once already.
        if damaged.strip() == base_text.strip():
            raise ValueError(
                f"Injector '{slug}' made no change to the base lesson. "
                f"Fix the injector — a sample that is not broken cannot be "
                f"labelled as failing."
            )

        # An injector that removes most of the lesson stops isolating ONE defect
        # and starts creating others, so the gold set then measures the wrong
        # thing. `truncate` is exempt: shortening IS its defect.
        kept = len(damaged.split()) / max(len(base_text.split()), 1)
        if slug != "too_short" and kept < 0.6:
            raise ValueError(
                f"Injector '{slug}' removed {1 - kept:.0%} of the lesson. A "
                f"fixture must isolate one defect, not gut the document — "
                f"otherwise it fails for a reason the label does not assert."
            )
        (SAMPLES_DIR / name).write_text(damaged)
        labels = {cp: True for cp in ALL_CHECKPOINTS}
        labels[broken] = False
        entries.append({
            "id": f"neg_{i:02d}",
            "sample": name,
            "topic": base_topic,
            "labels": labels,
            "note": note,
            # Only the injected checkpoint is asserted. Collateral damage is
            # real — stripping examples also shortens the lesson — so scoring
            # other checkpoints against this sample would punish a correct judge.
            "assert_only": [broken],
        })

    with open(GOLD_SET, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    console.print(f"[green]Wrote {len(entries)} samples[/green] → {GOLD_SET}")
    for e in entries:
        asserted = e.get("assert_only", ["all"])
        console.print(f"  {e['id']:8} {e['sample']:28} asserts: {', '.join(asserted)}")
    console.print("\n[dim]Review evals/samples/ and correct any label you disagree "
                  "with — the gold set is only as good as its labels.[/dim]")


if __name__ == "__main__":
    main()
