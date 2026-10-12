"""
CLI Entry Point for the Self-Evaluating Lesson Content Generator.

Usage:
    python main.py                              # Default: RAG topic
    python main.py --topic "Neural Networks"    # Custom topic
    python main.py --max-retries 3              # Override max retries
    python main.py --clear-memory               # Reset all persistent memory
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.graph import run_pipeline
from src.memory.store import MemoryStore
from src.config import MAX_RETRIES, OUTPUTS_DIR, TARGET_LEARNER_PROFILE


console = Console()


def save_output(result: dict, topic: str) -> Path:
    """Save the final lesson and rejection log to the outputs directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_topic = topic.replace(" ", "_").replace("(", "").replace(")", "")[:30]
    output_dir = OUTPUTS_DIR / f"{safe_topic}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save the lesson
    lesson_file = output_dir / "lesson.md"
    with open(lesson_file, "w") as f:
        f.write(result.get("lesson_content", "No lesson generated."))

    # Save the rejection log
    rejection_log = result.get("rejection_log", [])
    log_file = output_dir / "rejection_log.json"
    log_data = []
    for entry in rejection_log:
        if isinstance(entry, dict):
            log_data.append(entry)
        else:
            log_data.append(entry.model_dump())

    with open(log_file, "w") as f:
        json.dump(log_data, f, indent=2, default=str)

    # Save the final evaluation
    evaluation = result.get("evaluation")
    if evaluation:
        eval_file = output_dir / "final_evaluation.json"
        eval_data = evaluation.model_dump() if hasattr(evaluation, "model_dump") else evaluation
        with open(eval_file, "w") as f:
            json.dump(eval_data, f, indent=2, default=str)

    # Save the full per-attempt audit trail (metrics, checkpoints, deltas).
    # This is what makes the run reproducible and reviewable after the fact.
    attempts_file = output_dir / "attempt_trail.json"
    with open(attempts_file, "w") as f:
        json.dump(result.get("attempt_snapshots", []), f, indent=2, default=str)

    # Save a summary report
    summary_file = output_dir / "run_summary.md"
    with open(summary_file, "w") as f:
        f.write(f"# Run Summary\n\n")
        f.write(f"**Topic:** {topic}\n\n")
        f.write(f"**Total Attempts:** {result.get('attempt_number', 1)}\n\n")
        selected = result.get("selected_attempt")
        if selected:
            f.write(f"**Shipped Attempt:** {selected}")
            if selected != result.get("attempt_number"):
                f.write(" _(an earlier attempt scored higher than the last)_")
            f.write("\n\n")
        passed = evaluation.overall_pass if evaluation else False
        f.write(f"**Final Result:** {'✅ PASSED' if passed else '⚠️ BEST EFFORT'}\n\n")

        metrics = result.get("metrics", {})
        if metrics:
            f.write("## Measured Metrics\n\n")
            f.write(f"- Word count: {metrics.get('word_count')}\n")
            f.write(f"- Flesch-Kincaid grade: {metrics.get('fk_grade')}\n")
            f.write(f"- Sentences over 25 words: {metrics.get('long_sentence_ratio', 0):.0%}\n")
            f.write(f"- Passive voice ratio: {metrics.get('passive_ratio', 0):.0%}\n\n")

        if evaluation:
            f.write(f"## Checkpoint Results\n\n")
            for cp in evaluation.checkpoints:
                status = "✅" if cp.passed else "❌"
                f.write(f"- {status} **{cp.name}**: {cp.reason}\n")

        if rejection_log:
            f.write(f"\n## Rejection History — What Failed and What Changed\n\n")
            for entry in rejection_log:
                data = entry if isinstance(entry, dict) else entry.model_dump()
                f.write(f"### Attempt {data.get('attempt_number', '?')}\n")
                f.write(f"**Failed:** {', '.join(data.get('failed_checkpoints', [])) or 'none'}\n\n")
                if data.get("checkpoints_fixed"):
                    f.write(f"**Fixed since previous attempt:** {', '.join(data['checkpoints_fixed'])}\n\n")
                if data.get("checkpoints_regressed"):
                    f.write(f"**⚠️ Regressed since previous attempt:** {', '.join(data['checkpoints_regressed'])}\n\n")
                if data.get("checkpoints_still_failing"):
                    f.write(f"**Still failing:** {', '.join(data['checkpoints_still_failing'])}\n\n")
                for delta in data.get("metric_deltas", {}).values():
                    f.write(f"- {delta}\n")
                f.write("\n")

    return output_dir


def display_results(result: dict) -> None:
    """Display the pipeline results in a rich formatted output."""
    evaluation = result.get("evaluation")
    rejection_log = result.get("rejection_log", [])
    attempt_number = result.get("attempt_number", 1)

    # Header
    passed = evaluation.overall_pass if evaluation else False
    status_emoji = "✅" if passed else "⚠️"
    console.print()
    console.print(
        Panel(
            f"[bold]{status_emoji} Pipeline Complete[/bold]\n"
            f"Total Attempts: {attempt_number} | "
            f"Final Result: {'[green]PASSED[/green]' if passed else '[yellow]BEST EFFORT (max retries reached)[/yellow]'}",
            title="🎯 Self-Evaluating Lesson Generator",
            border_style="green" if passed else "yellow",
        )
    )

    # Evaluation table
    if evaluation:
        table = Table(title="📋 Final Evaluation Results")
        table.add_column("Checkpoint", style="bold")
        table.add_column("Status", justify="center")
        table.add_column("Reason", max_width=60)

        for cp in evaluation.checkpoints:
            status = "[green]✅ PASS[/green]" if cp.passed else "[red]❌ FAIL[/red]"
            table.add_row(cp.name, status, cp.reason)

        console.print(table)
        console.print()

    # Rejection log summary
    if rejection_log:
        console.print("[bold]📝 Rejection History:[/bold]")
        for entry in rejection_log:
            if isinstance(entry, dict):
                attempt = entry.get("attempt_number", "?")
                failed = entry.get("failed_checkpoints", [])
                reasons = entry.get("failure_reasons", {})
            else:
                attempt = entry.attempt_number
                failed = entry.failed_checkpoints
                reasons = entry.failure_reasons

            console.print(f"\n  [bold]Attempt {attempt}[/bold] — Failed: {', '.join(failed)}")
            for cp_name, reason in reasons.items():
                console.print(f"    • [red]{cp_name}[/red]: {reason}")
        console.print()

    # Show a preview of the lesson
    lesson = result.get("lesson_content", "")
    if lesson:
        preview = lesson[:500] + "..." if len(lesson) > 500 else lesson
        console.print(
            Panel(
                Markdown(preview),
                title="📖 Lesson Preview (first 500 chars)",
                border_style="blue",
            )
        )


def main():
    parser = argparse.ArgumentParser(
        description="Self-Evaluating Lesson Content Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --topic "Neural Networks"
  python main.py --max-retries 3
  python main.py --clear-memory
        """,
    )
    parser.add_argument(
        "--topic",
        default="RAG (Retrieval-Augmented Generation)",
        help="The lesson topic (default: RAG)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        help=f"Maximum regeneration attempts (default: {MAX_RETRIES})",
    )
    parser.add_argument(
        "--clear-memory",
        action="store_true",
        help="Clear all persistent memory before running",
    )

    args = parser.parse_args()

    # Handle memory clear
    if args.clear_memory:
        memory = MemoryStore()
        memory.clear_all_memory()
        console.print("[yellow]🗑️  All persistent memory cleared.[/yellow]")
        if not args.topic:
            return

    # Show startup info
    console.print()
    console.print(
        Panel(
            f"[bold]Topic:[/bold] {args.topic}\n"
            f"[bold]Max Retries:[/bold] {args.max_retries}\n"
            f"[bold]Target Audience:[/bold] 12th-grade Indian student",
            title="🚀 Starting Pipeline",
            border_style="cyan",
        )
    )

    # Show lessons learned from memory
    memory = MemoryStore()
    lessons = memory.load_lessons_learned()
    if lessons:
        console.print(
            f"\n[bold]🧠 Loaded {len(lessons)} lessons from memory:[/bold]"
        )
        for i, lesson in enumerate(lessons, 1):
            console.print(f"  {i}. {lesson}")
        console.print()

    # Run the pipeline
    console.print("[bold cyan]Running pipeline...[/bold cyan]\n")
    start_time = time.time()

    try:
        result = run_pipeline(
            topic=args.topic,
            max_retries=args.max_retries,
        )
    except Exception as e:
        console.print(f"[bold red]❌ Pipeline failed: {e}[/bold red]")
        raise

    duration = time.time() - start_time
    console.print(f"\n[dim]Pipeline completed in {duration:.1f} seconds[/dim]")

    # Display results
    display_results(result)

    # Save output
    output_dir = save_output(result, args.topic)
    console.print(
        f"\n[bold green]📁 Output saved to:[/bold green] {output_dir}"
    )

    # Show updated memory
    new_lessons = memory.load_lessons_learned()
    if len(new_lessons) > len(lessons):
        console.print(
            f"\n[bold]🧠 New lessons learned and saved to memory:[/bold]"
        )
        for lesson in new_lessons[len(lessons):]:
            console.print(f"  • {lesson}")


if __name__ == "__main__":
    main()
