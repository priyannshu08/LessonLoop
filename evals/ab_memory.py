"""A/B harness — does learned memory actually improve output?

WHY THIS EXISTS
---------------
"Self-evolving" is a claim, and a claim needs a measurement. Under the previous
design the honest answer was that memory made things WORSE: quality fell as the
rule set grew, and nobody noticed because nothing was measured.

This harness runs the same topics twice — once with learned rules injected, once
with none — and compares. It is the control group the system never had.

WHAT IS MEASURED
----------------
- first_attempt_pass : did attempt 1 clear all 6 checkpoints? The primary
                       metric, because memory's whole job is to get it right
                       sooner. Final pass rate is a weaker signal, since the
                       retry loop can rescue a bad first draft either way.
- attempts           : how many generations were needed.
- checkpoints_passed : partial credit on attempt 1, so improvement is visible
                       even when the pass/fail verdict does not flip.

Both arms run against identical topics, prompts, models and thresholds. The only
difference is whether the learned rules are present.

IMPORTANT: this measures the rule set as it stands right now. Re-run it after
memory changes — a rule set that helped at 5 rules can hurt at 15.

Usage:
    python -m evals.ab_memory                       # default topic set
    python -m evals.ab_memory --topics "X" "Y"
    python -m evals.ab_memory --repeats 2           # average over repeats
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.config import OUTPUTS_DIR
from src.graph import run_pipeline
from src.memory.store import MemoryStore

console = Console()

# Deliberately mixed: two technical topics (where polysyllabic vocabulary makes
# readability hard) and two everyday ones (where it is easy). A rule set that
# only helps on one kind of topic is not a general improvement.
DEFAULT_TOPICS = [
    "RAG (Retrieval-Augmented Generation)",
    "Neural Networks",
    "How the Internet Works",
    "Photosynthesis",
]


def summarise(result: dict) -> dict:
    """Pull the comparable numbers out of a completed run."""
    snapshots = result.get("attempt_snapshots", [])
    first = next((s for s in snapshots if s.get("attempt_number") == 1), {})
    evaluation = result.get("evaluation")

    return {
        "first_attempt_pass": bool(first.get("overall_pass")),
        "first_attempt_checkpoints": first.get("passed_count", 0),
        "first_attempt_fk": (first.get("metrics") or {}).get("fk_grade"),
        "attempts": result.get("attempt_number", 0),
        "final_pass": bool(evaluation.overall_pass) if evaluation else False,
    }


def run_arm(topic: str, use_memory: bool) -> dict:
    try:
        return summarise(run_pipeline(topic=topic, use_memory=use_memory))
    except Exception as exc:                       # a failed arm must not abort the sweep
        console.print(f"  [red]error ({'mem' if use_memory else 'ctrl'}): {exc}[/red]")
        return {
            "first_attempt_pass": False, "first_attempt_checkpoints": 0,
            "first_attempt_fk": None, "attempts": 0, "final_pass": False,
            "error": str(exc),
        }


def main():
    parser = argparse.ArgumentParser(description="A/B test learned memory.")
    parser.add_argument("--topics", nargs="+", default=DEFAULT_TOPICS)
    parser.add_argument("--repeats", type=int, default=1,
                        help="Runs per topic per arm. Generation is stochastic; "
                             "repeats average out single-run luck.")
    args = parser.parse_args()

    rules = MemoryStore().get_active_rules()
    total_runs = len(args.topics) * args.repeats * 2
    console.print(
        f"\n[bold]A/B: memory ON vs OFF[/bold]\n"
        f"{len(rules)} active rule(s) · {len(args.topics)} topic(s) · "
        f"{args.repeats} repeat(s) · [yellow]{total_runs} pipeline runs[/yellow]\n"
    )
    if not rules:
        console.print("[yellow]Memory is empty — both arms are identical. "
                      "Run the pipeline a few times first.[/yellow]\n")

    rows = []
    for topic in args.topics:
        for rep in range(args.repeats):
            label = f"{topic}" + (f" #{rep + 1}" if args.repeats > 1 else "")
            console.print(f"[cyan]{label}[/cyan]")

            # Control first, so a rule promoted mid-sweep cannot advantage the
            # memory arm on the very same topic.
            control = run_arm(topic, use_memory=False)
            treatment = run_arm(topic, use_memory=True)
            rows.append({"topic": label, "control": control, "memory": treatment})

            console.print(
                f"  control: {control['first_attempt_checkpoints']}/6 on attempt 1 · "
                f"memory: {treatment['first_attempt_checkpoints']}/6 on attempt 1"
            )

    table = Table(title="Per-Topic Results (attempt 1)")
    table.add_column("Topic", max_width=34)
    table.add_column("Ctrl ✓/6", justify="center")
    table.add_column("Mem ✓/6", justify="center")
    table.add_column("Δ", justify="center")
    table.add_column("Ctrl attempts", justify="center")
    table.add_column("Mem attempts", justify="center")

    for row in rows:
        c, m = row["control"], row["memory"]
        delta = m["first_attempt_checkpoints"] - c["first_attempt_checkpoints"]
        colour = "green" if delta > 0 else "red" if delta < 0 else "dim"
        table.add_row(
            row["topic"],
            str(c["first_attempt_checkpoints"]), str(m["first_attempt_checkpoints"]),
            f"[{colour}]{delta:+d}[/{colour}]",
            str(c["attempts"]), str(m["attempts"]),
        )
    console.print()
    console.print(table)

    def mean(arm: str, key: str) -> float:
        values = [r[arm][key] for r in rows]
        return sum(values) / len(values) if values else 0.0

    n = len(rows)
    verdict = Table(title="Aggregate")
    verdict.add_column("Metric")
    verdict.add_column("Memory OFF", justify="center")
    verdict.add_column("Memory ON", justify="center")
    verdict.add_column("Verdict", justify="center")

    for label, key, as_rate in [
        ("First-attempt pass rate", "first_attempt_pass", True),
        ("Checkpoints passed on attempt 1 (of 6)", "first_attempt_checkpoints", False),
        ("Final pass rate", "final_pass", True),
        ("Attempts needed (lower is better)", "attempts", False),
    ]:
        c, m = mean("control", key), mean("memory", key)
        better = (m < c) if key == "attempts" else (m > c)
        worse = (m > c) if key == "attempts" else (m < c)
        mark = "[green]better[/green]" if better else "[red]WORSE[/red]" if worse else "[dim]same[/dim]"
        fmt = (lambda v: f"{v:.0%}") if as_rate else (lambda v: f"{v:.2f}")
        verdict.add_row(label, fmt(c), fmt(m), mark)

    console.print()
    console.print(verdict)

    improved = mean("memory", "first_attempt_checkpoints") - mean(
        "control", "first_attempt_checkpoints"
    )
    console.print()
    if improved > 0:
        console.print(f"[bold green]Memory helps: +{improved:.2f} checkpoints on attempt 1 "
                      f"across {n} run pair(s).[/bold green]")
    elif improved < 0:
        console.print(f"[bold red]Memory HURTS: {improved:.2f} checkpoints on attempt 1. "
                      f"Tighten the rule set before shipping.[/bold red]")
    else:
        console.print("[yellow]No measurable difference on this sample.[/yellow]")

    out = OUTPUTS_DIR / f"ab_memory_{datetime.now():%Y%m%d_%H%M%S}.json"
    with open(out, "w") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "active_rules": [r["insight"] for r in rules],
            "rows": rows,
        }, f, indent=2, default=str)
    console.print(f"[dim]Raw results: {out}[/dim]\n")


if __name__ == "__main__":
    main()
