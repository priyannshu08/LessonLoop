"""Guard against stale references after a refactor.

WHY THIS EXISTS
---------------
When the rubric was rebuilt, several config fields and one store method were
renamed or removed. The test suite stayed green because the UI is only executed
when a human clicks a tab — so the breakage surfaced as a crash in the browser
instead of a failing build:

    AttributeError: 'Rubric' object has no attribute 'max_fk_grade'

Streamlit code is hard to unit-test directly, but the specific failure mode here
is trivially detectable statically: an attribute or method that no longer exists.
This test walks the source and checks every reference resolves.

It cannot catch every runtime error, only this class. That is enough — this is
the class that actually bit.
"""

import re
from pathlib import Path

from src.config import MEMORY_POLICY, RUBRIC
from src.memory.store import MemoryStore

PROJECT = Path(__file__).parent.parent
SKIP = {"venv", ".git", "__pycache__", "outputs", "memory"}


def source_files() -> list[Path]:
    """All project source, excluding this file.

    This module necessarily names the very symbols it forbids, so scanning
    itself would always fail.
    """
    here = Path(__file__).resolve()
    return [
        f for f in PROJECT.rglob("*.py")
        if not any(part in SKIP for part in f.parts) and f.resolve() != here
    ]


def test_every_rubric_reference_exists():
    """A removed threshold must not survive anywhere in the codebase."""
    valid = set(RUBRIC.__dataclass_fields__)
    stale = [
        f"{f.relative_to(PROJECT)}:{i} → RUBRIC.{attr}"
        for f in source_files()
        for i, line in enumerate(f.read_text().splitlines(), 1)
        for attr in re.findall(r"\bRUBRIC\.(\w+)", line)
        if attr not in valid
    ]
    assert not stale, "References to removed Rubric fields:\n  " + "\n  ".join(stale)


def test_every_memory_policy_reference_exists():
    valid = set(MEMORY_POLICY.__dataclass_fields__)
    stale = [
        f"{f.relative_to(PROJECT)}:{i} → MEMORY_POLICY.{attr}"
        for f in source_files()
        for i, line in enumerate(f.read_text().splitlines(), 1)
        for attr in re.findall(r"\bMEMORY_POLICY\.(\w+)", line)
        if attr not in valid
    ]
    assert not stale, "References to removed MemoryPolicy fields:\n  " + "\n  ".join(stale)


def test_every_memory_store_call_exists():
    """Catches a renamed store method — e.g. signature_run_counts →
    signature_topic_counts, which only crashed when the Memory tab was opened."""
    methods = {m for m in dir(MemoryStore) if not m.startswith("_")}
    stale = [
        f"{f.relative_to(PROJECT)}:{i} → .{call}()"
        for f in source_files()
        if f.name != "store.py"
        for i, line in enumerate(f.read_text().splitlines(), 1)
        for call in re.findall(r"\b(?:memory|mem|store)\.(\w+)\(", line)
        if call not in methods
    ]
    assert not stale, "Calls to non-existent MemoryStore methods:\n  " + "\n  ".join(stale)


def test_every_checkpoint_name_is_canonical():
    """Checkpoint names are strings, so a rename fails silently everywhere it is
    missed — including in the rubric prompt, where a wrong name means the judge
    returns a checkpoint nobody reads and the real one fails closed."""
    from src.config import CHECKPOINT_NAMES

    retired = {
        "covers_key_concepts", "factual_accuracy",
        "no_unexplained_jargon_v1", "beginner_friendly_language_v1",
    }
    hits = [
        f"{f.relative_to(PROJECT)}:{i} → {name}"
        for f in source_files()
        for i, line in enumerate(f.read_text().splitlines(), 1)
        for name in retired
        if name in line
    ]
    assert not hits, "Retired checkpoint names still referenced:\n  " + "\n  ".join(hits)
    assert len(CHECKPOINT_NAMES) == 6, "The brief names exactly six dimensions."


def test_config_exposes_labels_and_tiers_for_every_checkpoint():
    """The UI reads these; a missing entry renders a raw snake_case name."""
    from src.config import CHECKPOINT_LABELS, CHECKPOINT_NAMES, CHECKPOINT_TIERS

    assert set(CHECKPOINT_LABELS) == set(CHECKPOINT_NAMES)
    assert set(CHECKPOINT_TIERS) == set(CHECKPOINT_NAMES)
