"""Calibration — measure whether the evaluator is actually right.

WHO VALIDATES THE VALIDATOR
---------------------------
An LLM judge is itself an LLM, so it inherits the failure modes of the thing it
grades. The rubric was written a priori and never checked against a human label;
the honest answer to "how do you know your evaluator is correct?" was "I don't".
It failed 100% of outputs at one point, which is a rubric carrying no signal at
all — and nothing in the system could detect that.

This harness runs the evaluator against labelled samples and reports, PER
CHECKPOINT:

    TPR (sensitivity) — of the samples that genuinely fail, how many did the
                        judge catch? Low TPR = bad content ships.
    TNR (specificity) — of the samples that are genuinely fine, how many did the
                        judge let through? Low TNR = good content is rejected
                        forever and the loop never terminates in a pass. This is
                        the pathology that produced the 0% pass rate.

Reported SEPARATELY, never as one accuracy number: the sample is imbalanced, so
a judge that fails everything would still post respectable "accuracy" while
being useless.

Usage:
    python -m evals.calibrate                 # full run
    python -m evals.calibrate --checkpoint accurate_and_grounded
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.config import CHECKPOINT_NAMES, OUTPUTS_DIR
from src.nodes.evaluator import evaluate_lesson

console = Console()

EVALS_DIR = Path(__file__).parent
SAMPLES_DIR = EVALS_DIR / "samples"
GOLD_SET = EVALS_DIR / "gold_set.jsonl"


def load_gold_set() -> list[dict]:
    if not GOLD_SET.exists():
        console.print("[red]No gold set found.[/red] Run: python -m evals.build_gold_set")
        return []
    with open(GOLD_SET) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    parser = argparse.ArgumentParser(description="Measure evaluator agreement.")
    parser.add_argument("--checkpoint", help="Only report this checkpoint")
    args = parser.parse_args()

    entries = load_gold_set()
    if not entries:
        return

    console.print(f"\n[bold]Evaluating {len(entries)} labelled samples[/bold]\n")

    # counts[checkpoint] = {tp, fn, tn, fp}
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fn": 0, "tn": 0, "fp": 0}
    )
    disagreements = []

    for entry in entries:
        lesson = (SAMPLES_DIR / entry["sample"]).read_text()
        console.print(f"  [cyan]{entry['id']}[/cyan] {entry['sample']}")

        try:
            result = evaluate_lesson({
                "lesson_content": lesson,
                "topic": entry["topic"],
            })
            verdicts = {cp.name: cp for cp in result["evaluation"].checkpoints}
        except Exception as exc:
            console.print(f"    [red]evaluation failed: {exc}[/red]")
            continue

        # Score only the checkpoints this sample actually asserts. A negative
        # built by stripping examples also becomes shorter, so grading it on
        # `covers_key_points` would penalise a judge that is right.
        asserted = entry.get("assert_only") or list(entry["labels"])

        for checkpoint in asserted:
            if args.checkpoint and checkpoint != args.checkpoint:
                continue
            should_pass = entry["labels"][checkpoint]
            verdict = verdicts.get(checkpoint)
            if verdict is None:
                continue
            did_pass = verdict.passed

            if not should_pass and not did_pass:
                counts[checkpoint]["tp"] += 1        # correctly caught a failure
            elif not should_pass and did_pass:
                counts[checkpoint]["fn"] += 1        # MISSED a real failure
                disagreements.append((entry, checkpoint, "MISSED", verdict.reason))
            elif should_pass and did_pass:
                counts[checkpoint]["tn"] += 1        # correctly passed good content
            else:
                counts[checkpoint]["fp"] += 1        # rejected good content
                disagreements.append((entry, checkpoint, "FALSE ALARM", verdict.reason))

    # ---- Report -------------------------------------------------------
    table = Table(title="Judge vs Gold Labels (per checkpoint)")
    table.add_column("Checkpoint")
    table.add_column("TPR", justify="center")
    table.add_column("TNR", justify="center")
    table.add_column("Caught", justify="center")
    table.add_column("Missed", justify="center")
    table.add_column("False alarms", justify="center")

    for checkpoint in CHECKPOINT_NAMES:
        if args.checkpoint and checkpoint != args.checkpoint:
            continue
        c = counts[checkpoint]
        fails, passes = c["tp"] + c["fn"], c["tn"] + c["fp"]
        tpr = c["tp"] / fails if fails else None
        tnr = c["tn"] / passes if passes else None

        def fmt(value):
            if value is None:
                return "[dim]n/a[/dim]"
            colour = "green" if value >= 0.8 else "yellow" if value >= 0.5 else "red"
            return f"[{colour}]{value:.0%}[/{colour}]"

        table.add_row(
            checkpoint, fmt(tpr), fmt(tnr),
            str(c["tp"]), str(c["fn"]), str(c["fp"]),
        )

    console.print()
    console.print(table)
    console.print(
        "\n[dim]TPR = of genuinely bad samples, how many were caught.\n"
        "TNR = of genuinely good samples, how many were let through.\n"
        "Low TNR is what makes the retry loop never terminate in a pass.[/dim]"
    )

    if disagreements:
        console.print(f"\n[bold yellow]{len(disagreements)} disagreement(s) — "
                      f"each is either a judge bug or a bad label:[/bold yellow]\n")
        for entry, checkpoint, kind, reason in disagreements:
            colour = "red" if kind == "MISSED" else "yellow"
            console.print(f"  [{colour}]{kind}[/{colour}] {entry['id']} · {checkpoint}")
            console.print(f"    injected: {entry['note']}")
            console.print(f"    judge said: {reason[:200]}\n")
    else:
        console.print("\n[bold green]Full agreement with every gold label.[/bold green]")

    out = OUTPUTS_DIR / f"calibration_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(out, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "counts": {k: dict(v) for k, v in counts.items()},
            "disagreements": [
                {"id": e["id"], "checkpoint": c, "kind": k, "judge_reason": r}
                for e, c, k, r in disagreements
            ],
        }, f, indent=2)
    console.print(f"[dim]Raw results: {out}[/dim]\n")


if __name__ == "__main__":
    main()
