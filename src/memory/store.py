"""
JSON-based Memory Store for cross-run persistence.

TWO-TIER MEMORY
---------------
Memory is split the way agent cognitive architectures split it, because
collapsing the two tiers is exactly what made this system degrade:

1. observations.json     — EPISODIC. One record per failed checkpoint per
                           attempt: what failed, on which run, and why.
                           Unbounded, cheap, and NEVER shown to a model.
2. lessons_learned.json  — SEMANTIC/PROCEDURAL. The small set of rules injected
                           into every generation. Capped, utility-tracked, and
                           written only by consolidation.
3. run_history.json      — Full per-run audit trail (rich, per-attempt data)
4. prompt_evolution.json — How the rule set changed, and why

The previous design had only tier 2, written directly after every run. One noisy
run therefore became permanent law, and the rule set grew unbounded until 20
mutually contradictory instructions cancelled each other out. Observations now
absorb the noise; only patterns that actually repeat are promoted to rules.

Rules are never deleted. Retired ones are marked `superseded` with a pointer to
whatever replaced them, so the evolution stays auditable and reversible.

Design Choice: JSON files over a vector DB because the rule set is deliberately
tiny (<= 7) and read in full every run — there is nothing to search.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import (
    LESSONS_LEARNED_FILE,
    MEMORY_POLICY,
    OBSERVATIONS_FILE,
    PROMPT_EVOLUTION_FILE,
    RUN_HISTORY_FILE,
)
from src.memory.signatures import failure_signature


class MemoryStore:
    """Persistent memory store using JSON files."""

    def __init__(
        self,
        lessons_file: Optional[Path] = None,
        history_file: Optional[Path] = None,
        evolution_file: Optional[Path] = None,
        observations_file: Optional[Path] = None,
    ):
        self.lessons_file = lessons_file or LESSONS_LEARNED_FILE
        self.history_file = history_file or RUN_HISTORY_FILE
        self.evolution_file = evolution_file or PROMPT_EVOLUTION_FILE
        # Default the observations file alongside the rules file, so tests that
        # point the store at a temp directory keep both tiers together.
        self.observations_file = observations_file or (
            self.lessons_file.parent / "observations.json"
            if lessons_file
            else OBSERVATIONS_FILE
        )

        self._ensure_file(self.lessons_file, {"lessons": []})
        self._ensure_file(self.history_file, {"runs": []})
        self._ensure_file(self.evolution_file, {"evolutions": []})
        self._ensure_file(self.observations_file, {"observations": []})

    def _ensure_file(self, path: Path, default_content: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with open(path, "w") as f:
                json.dump(default_content, f, indent=2)

    def _read_json(self, path: Path) -> dict:
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write_json(self, path: Path, data: dict) -> None:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    # =========================================================
    # TIER 1 — OBSERVATIONS (episodic; never shown to a model)
    # =========================================================

    def record_observations(
        self,
        run_id: str,
        topic: str,
        rejection_log: list,
        passed_signatures: list[str] | None = None,
    ) -> list[str]:
        """Write one observation per failed checkpoint per attempt.

        This is the cheap, high-volume tier. Nothing here reaches a prompt, so a
        noisy run costs nothing — which is precisely why the old design was
        fragile: it had nowhere to put evidence except the prompt itself.

        Successes are recorded too. A signature that STOPS recurring after a rule
        went active is the strongest evidence that rule works, and recording only
        failures threw that signal away.

        Returns the failure signatures seen on this run.
        """
        data = self._read_json(self.observations_file)
        observations = data.get("observations", [])
        seen = []

        def add(**fields):
            observations.append({
                "id": f"obs_{len(observations) + 1:04d}",
                "timestamp": datetime.now().isoformat(),
                "run_id": run_id,
                "topic": topic,
                **fields,
            })

        for entry in rejection_log:
            record = entry if isinstance(entry, dict) else entry.model_dump()
            for checkpoint, reason in (record.get("failure_reasons") or {}).items():
                signature = failure_signature(checkpoint, reason)
                seen.append(signature)
                add(
                    attempt_number=record.get("attempt_number"),
                    checkpoint=checkpoint,
                    signature=signature,
                    reason=reason,
                    outcome="fail",
                )

        for checkpoint in passed_signatures or []:
            add(checkpoint=checkpoint, signature=None, reason="", outcome="pass")

        data["observations"] = observations
        self._write_json(self.observations_file, data)
        return seen

    def get_observations(self) -> list[dict]:
        return self._read_json(self.observations_file).get("observations", [])

    def signature_topic_counts(self) -> dict[str, set]:
        """Count DISTINCT TOPICS per failure signature, within the recent window.

        TOPICS, not runs — this is the generalisation guard. Three failures
        across three RAG runs prove nothing about Photosynthesis, so a rule
        distilled from them would be fitted to one subject. Requiring the same
        failure on three DIFFERENT topics makes the resulting rule
        topic-independent by construction, rather than by hoping the distiller
        writes something universal.

        (Counting runs would also let one unlucky run failing three attempts
        promote a rule on its own, which is how noise became permanent law.)
        """
        observations = self.get_observations()
        recent_runs = []
        for obs in reversed(observations):
            if obs["run_id"] not in recent_runs:
                recent_runs.append(obs["run_id"])
            if len(recent_runs) >= MEMORY_POLICY.recurrence_window_runs:
                break
        window = set(recent_runs)

        counts: dict[str, set] = {}
        for obs in observations:
            if obs.get("outcome") == "pass" or not obs.get("signature"):
                continue
            if obs["run_id"] in window:
                counts.setdefault(obs["signature"], set()).add(obs["topic"])
        return counts

    # =========================================================
    # TIER 2 — RULES (semantic; injected into every generation)
    # =========================================================

    def load_lessons_learned(self) -> list[str]:
        """Rule texts that are currently injected into the generator prompt.

        Superseded rules are excluded — they stay on disk for provenance but
        must never reach a prompt, since a retired rule is usually retired for
        contradicting a newer one.
        """
        return [r["insight"] for r in self.get_active_rules()]

    def get_active_rules(self) -> list[dict]:
        """Rules currently in force, newest last. Excludes superseded ones."""
        data = self._read_json(self.lessons_file)
        return [
            r for r in data.get("lessons", [])
            if r.get("status", "active") != "superseded"
        ]

    def get_all_rules(self) -> list[dict]:
        """Every rule ever promoted, including superseded ones (for audit)."""
        return self._read_json(self.lessons_file).get("lessons", [])

    def add_rule(
        self,
        insight: str,
        signature: str,
        source_run_id: str,
        evidence_runs: list[str],
        supersedes: Optional[list[str]] = None,
    ) -> str:
        """Promote a consolidated pattern into an active rule.

        New rules start on PROBATION: they are injected like any other rule, but
        are dropped rather than kept if quality does not improve while they are
        active. This is the "keep only if it improves" discipline that separates
        prompt evolution from prompt accumulation.
        """
        data = self._read_json(self.lessons_file)
        rules = data.get("lessons", [])

        rule_id = f"rule_{len(rules) + 1:03d}"
        rules.append({
            "id": rule_id,
            "created_at": datetime.now().isoformat(),
            "insight": insight,
            "signature": signature,
            "source_run": source_run_id,
            "evidence_runs": evidence_runs,
            "status": "probation",
            "supersedes": supersedes or [],
            "superseded_by": None,
            # Utility tracking — the evidence used for eviction.
            "runs_active": 0,
            "runs_passed": 0,
            "runs_passed_first_attempt": 0,
        })

        # Mark whatever this rule replaces, rather than deleting it.
        for old_id in supersedes or []:
            for rule in rules:
                if rule["id"] == old_id:
                    rule["status"] = "superseded"
                    rule["superseded_by"] = rule_id

        data["lessons"] = rules
        self._write_json(self.lessons_file, data)
        return rule_id

    def record_rule_outcomes(self, active_rule_ids: list[str], run: dict) -> None:
        """Attribute a run's outcome to the rules that were active during it.

        This is what makes eviction evidence-based rather than arbitrary. The
        old schema had an `applied_count` field that nothing ever incremented,
        so there was never any basis for deciding which rule to drop.
        """
        data = self._read_json(self.lessons_file)
        passed = bool(run.get("final_passed"))
        first_try = passed and run.get("total_attempts", 99) == 1

        for rule in data.get("lessons", []):
            if rule["id"] in active_rule_ids:
                rule["runs_active"] = rule.get("runs_active", 0) + 1
                rule["runs_passed"] = rule.get("runs_passed", 0) + int(passed)
                rule["runs_passed_first_attempt"] = (
                    rule.get("runs_passed_first_attempt", 0) + int(first_try)
                )
        self._write_json(self.lessons_file, data)

    # =========================================================
    # FIX-ATTEMPT LEDGER (per failure signature)
    # =========================================================
    # Without this, a dropped rule leaves its signature uncovered, the same
    # observations are still on disk, the distiller regenerates a near-identical
    # rule, it fails again, and the system loops forever. The ledger records what
    # has already been tried so the next attempt is DIFFERENT — and so the system
    # can eventually conclude the pattern is not fixable by prompting at all.

    def get_fix_attempts(self, signature: str) -> list[dict]:
        """Rules already tried for this signature, successful or not."""
        return [
            {"insight": r["insight"], "status": r.get("status"),
             "why": r.get("status_reason", "")}
            for r in self.get_all_rules()
            if r.get("signature") == signature
        ]

    def failed_fix_attempts(self, signature: str) -> list[dict]:
        """Rules tried for this signature that did NOT help."""
        return [a for a in self.get_fix_attempts(signature) if a["status"] == "superseded"]

    def is_escalated(self, signature: str) -> bool:
        """True once enough different rules have failed on this signature.

        At that point the honest conclusion is that prompting cannot fix it —
        which is a useful diagnosis, not a failure. It usually means the gate is
        miscalibrated or the generator model is the bottleneck, and it should
        reach a human rather than spin.
        """
        data = self._read_json(self.lessons_file)
        if signature in data.get("escalated", []):
            return True
        return (
            len(self.failed_fix_attempts(signature))
            >= MEMORY_POLICY.max_fix_attempts_per_signature
        )

    def escalate(self, signature: str, reason: str) -> None:
        """Mark a signature as not fixable by prompting, and stop retrying it."""
        data = self._read_json(self.lessons_file)
        escalated = data.setdefault("escalated", [])
        if signature not in escalated:
            escalated.append(signature)
        data.setdefault("escalation_notes", {})[signature] = {
            "reason": reason,
            "at": datetime.now().isoformat(),
        }
        self._write_json(self.lessons_file, data)

    def get_escalations(self) -> dict:
        """Signatures the system has given up trying to fix by prompting."""
        return self._read_json(self.lessons_file).get("escalation_notes", {})

    def set_rule_status(self, rule_id: str, status: str, reason: str = "") -> None:
        """Move a rule between probation / active / superseded."""
        data = self._read_json(self.lessons_file)
        for rule in data.get("lessons", []):
            if rule["id"] == rule_id:
                rule["status"] = status
                if reason:
                    rule["status_reason"] = reason
                break
        self._write_json(self.lessons_file, data)

    def save_lesson_learned(
        self,
        insight: str,
        failed_checkpoint: str,
        source_run_id: str,
    ) -> None:
        """Directly add a rule, bypassing consolidation.

        DEPRECATED. This is the old write path — one run, straight to permanent
        law, deduplicated only by exact string match. That is how the system
        accumulated 20 rules containing four incompatible sentence-length limits
        and two different word counts.

        Retained only so existing callers and tests keep working. Production
        writes go through `src.memory.consolidation`, which requires a failure
        to repeat and checks for contradictions before admitting anything.
        """
        data = self._read_json(self.lessons_file)
        lessons = data.get("lessons", [])

        existing = [l["insight"].lower() for l in lessons]
        if insight.lower() not in existing:
            lessons.append({
                "id": f"lesson_{len(lessons) + 1:03d}",
                "created_at": datetime.now().isoformat(),
                "source_run": source_run_id,
                "failed_checkpoint": failed_checkpoint,
                "signature": f"{failed_checkpoint}::legacy",
                "insight": insight,
                "status": "active",
                "supersedes": [],
                "superseded_by": None,
                "runs_active": 0,
                "runs_passed": 0,
                "runs_passed_first_attempt": 0,
            })
            data["lessons"] = lessons
            self._write_json(self.lessons_file, data)

    # =========================================================
    # RUN AUDIT LOG (Rich Per-Run + Per-Attempt Data)
    # =========================================================

    def log_run(
        self,
        run_id: str,
        topic: str,
        total_attempts: int,
        final_passed: bool,
        failed_checkpoints_per_attempt: list[list[str]],
        duration_seconds: float,
        attempt_snapshots: Optional[list[dict]] = None,
        memory_before: Optional[list[str]] = None,
        lessons_learned_active: Optional[list[str]] = None,
        selected_attempt: Optional[int] = None,
        new_lessons: Optional[list[str]] = None,
        memory_after: Optional[list[str]] = None,
    ) -> None:
        """Persist a rich, per-attempt audit entry for this run.

        The audit entry captures:
        - User input (topic)
        - Memory rules that were active (injected into generator prompt)
        - Per-attempt: lesson preview, word count, all checkpoint results
          with reasons + evidence, feedback sent to generator
        - Memory rules added after the run
        """
        data = self._read_json(self.history_file)
        runs = data.get("runs", [])

        # Compute summary stats from snapshots
        attempt_summaries = []
        if attempt_snapshots:
            for snap in attempt_snapshots:
                attempt_summaries.append({
                    "attempt_number": snap.get("attempt_number"),
                    "word_count": snap.get("word_count"),
                    "word_count_delta": snap.get("word_count_delta"),
                    "metrics": snap.get("metrics", {}),
                    "passed_count": snap.get("passed_count", 0),
                    "failed_count": snap.get("failed_count", 0),
                    "overall_pass": snap.get("overall_pass", False),
                    "evaluation_summary": snap.get("evaluation_summary", ""),
                    "is_final_attempt": snap.get("is_final_attempt", False),
                    "lesson_preview": snap.get("lesson_preview", ""),
                    "checkpoints": snap.get("checkpoints", []),
                    "feedback_sent_to_generator": snap.get("feedback_sent_to_generator"),
                    # What actually changed vs the previous attempt
                    "checkpoints_fixed": snap.get("checkpoints_fixed", []),
                    "checkpoints_regressed": snap.get("checkpoints_regressed", []),
                })

        run_entry = {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),

            # What the user asked for
            "topic": topic,

            # High-level outcome
            "total_attempts": total_attempts,
            "final_passed": final_passed,
            "final_result": "PASSED" if final_passed else "BEST_EFFORT",
            "duration_seconds": round(duration_seconds, 2),

            # Memory context for this run
            "memory_rules_active_count": len(memory_before or []),
            "memory_rules_active": memory_before or [],

            # Per-attempt full audit trail
            "attempts": attempt_summaries,
            "selected_attempt": selected_attempt,

            # What this run taught the system (written in the same pass, so the
            # entry is complete the moment it is persisted)
            "new_rules_learned": new_lessons or [],
            "memory_rules_after": memory_after or [],
            "memory_rules_after_count": len(memory_after or []),

            # Legacy field for backward compat
            "failed_checkpoints_per_attempt": failed_checkpoints_per_attempt,
        }

        runs.append(run_entry)
        data["runs"] = runs
        self._write_json(self.history_file, data)

    def update_run_with_new_lessons(
        self,
        run_id: str,
        new_lessons: list[str],
        memory_after: list[str],
    ) -> None:
        """After prompt optimization, update the run entry with new rules added."""
        data = self._read_json(self.history_file)
        for run in data.get("runs", []):
            if run.get("run_id") == run_id:
                run["new_rules_learned"] = new_lessons
                run["memory_rules_after"] = memory_after
                run["memory_rules_after_count"] = len(memory_after)
                break
        self._write_json(self.history_file, data)

    def get_run_history(self) -> list[dict]:
        """Get all past runs (rich audit format)."""
        data = self._read_json(self.history_file)
        return data.get("runs", [])

    def get_latest_run_id(self) -> Optional[str]:
        """Get the run_id of the most recent run (handles both legacy 'id' and new 'run_id' keys)."""
        runs = self.get_run_history()
        if not runs:
            return None
        latest = runs[-1]
        return latest.get("run_id") or latest.get("id")

    # =========================================================
    # PROMPT EVOLUTION HISTORY
    # =========================================================

    def log_prompt_evolution(
        self,
        run_id: str,
        change_description: str,
        new_lessons_added: list[str],
    ) -> None:
        """Track how the system's knowledge base evolved after a run."""
        data = self._read_json(self.evolution_file)
        evolutions = data.get("evolutions", [])

        evolutions.append({
            "timestamp": datetime.now().isoformat(),
            "source_run": run_id,
            "change_description": change_description,
            "new_lessons_added": new_lessons_added,
            "total_lessons_in_memory": len(self.load_lessons_learned()),
        })
        data["evolutions"] = evolutions
        self._write_json(self.evolution_file, data)

    def get_prompt_evolution_history(self) -> list[dict]:
        data = self._read_json(self.evolution_file)
        return data.get("evolutions", [])

    # =========================================================
    # UTILITY
    # =========================================================

    def clear_all_memory(self) -> None:
        """Reset all memory stores, both tiers."""
        self._write_json(self.lessons_file, {"lessons": []})
        self._write_json(self.history_file, {"runs": []})
        self._write_json(self.evolution_file, {"evolutions": []})
        self._write_json(self.observations_file, {"observations": []})
