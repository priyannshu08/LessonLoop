"""
Tests for the Memory Store.

These tests verify that the JSON-based memory system correctly
persists and retrieves data across operations.
"""

import pytest
import json
import tempfile
from pathlib import Path

from src.memory.store import MemoryStore


@pytest.fixture
def temp_memory():
    """Create a MemoryStore with temporary files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(
            lessons_file=Path(tmpdir) / "lessons.json",
            history_file=Path(tmpdir) / "history.json",
            evolution_file=Path(tmpdir) / "evolution.json",
        )
        yield store


class TestMemoryStore:

    def test_initial_state_is_empty(self, temp_memory):
        """New memory store should have no lessons."""
        assert temp_memory.load_lessons_learned() == []
        assert temp_memory.get_run_history() == []
        assert temp_memory.get_prompt_evolution_history() == []

    def test_save_and_load_lesson(self, temp_memory):
        """Saved lessons should be retrievable."""
        temp_memory.save_lesson_learned(
            insight="Always define 'embedding' with GPS analogy",
            failed_checkpoint="no_unexplained_jargon",
            source_run_id="test_001",
        )

        lessons = temp_memory.load_lessons_learned()
        assert len(lessons) == 1
        assert "embedding" in lessons[0].lower()

    def test_no_duplicate_lessons(self, temp_memory):
        """Same insight should not be stored twice."""
        for _ in range(3):
            temp_memory.save_lesson_learned(
                insight="Always define 'embedding' with GPS analogy",
                failed_checkpoint="no_unexplained_jargon",
                source_run_id="test_001",
            )

        lessons = temp_memory.load_lessons_learned()
        assert len(lessons) == 1

    def test_multiple_lessons(self, temp_memory):
        """Multiple different lessons should be stored."""
        temp_memory.save_lesson_learned(
            insight="Define embedding with GPS analogy",
            failed_checkpoint="no_unexplained_jargon",
            source_run_id="run_1",
        )
        temp_memory.save_lesson_learned(
            insight="Use cricket analogy for explaining retrieval",
            failed_checkpoint="teaches_by_example",
            source_run_id="run_2",
        )

        lessons = temp_memory.load_lessons_learned()
        assert len(lessons) == 2

    def test_log_run(self, temp_memory):
        """Run history should be logged correctly."""
        temp_memory.log_run(
            run_id="run_001",
            topic="RAG",
            total_attempts=3,
            final_passed=True,
            failed_checkpoints_per_attempt=[["jargon"], ["language"]],
            duration_seconds=45.5,
        )

        runs = temp_memory.get_run_history()
        assert len(runs) == 1
        assert runs[0]["topic"] == "RAG"
        assert runs[0]["total_attempts"] == 3
        assert runs[0]["final_passed"] is True

    def test_clear_memory(self, temp_memory):
        """Clear should reset all stores."""
        temp_memory.save_lesson_learned(
            insight="Test insight",
            failed_checkpoint="test",
            source_run_id="test",
        )
        temp_memory.log_run(
            run_id="run_001",
            topic="Test",
            total_attempts=1,
            final_passed=True,
            failed_checkpoints_per_attempt=[],
            duration_seconds=1.0,
        )

        temp_memory.clear_all_memory()

        assert temp_memory.load_lessons_learned() == []
        assert temp_memory.get_run_history() == []

    def test_prompt_evolution_logging(self, temp_memory):
        """Prompt evolution should be tracked."""
        temp_memory.log_prompt_evolution(
            run_id="run_001",
            change_description="Added rule about defining embeddings",
            new_lessons_added=["Define embedding with GPS analogy"],
        )

        evolutions = temp_memory.get_prompt_evolution_history()
        assert len(evolutions) == 1
        assert "embedding" in evolutions[0]["change_description"].lower()

    def test_persistence_across_instances(self, temp_memory):
        """Data should persist when creating a new MemoryStore instance."""
        temp_memory.save_lesson_learned(
            insight="Persistent insight",
            failed_checkpoint="test",
            source_run_id="test",
        )

        # Create a new instance pointing to the same files
        new_store = MemoryStore(
            lessons_file=temp_memory.lessons_file,
            history_file=temp_memory.history_file,
            evolution_file=temp_memory.evolution_file,
        )

        lessons = new_store.load_lessons_learned()
        assert len(lessons) == 1
        assert lessons[0] == "Persistent insight"
