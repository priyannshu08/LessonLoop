"""Tests for the Tier 1 deterministic gates.

These run with no API key and no network. That is the point of the tier: the
mechanical half of the rubric is testable, reproducible, and free.

Several tests here are regressions for bugs found by calibration rather than by
inspection — each docstring says which.
"""

from src.config import RUBRIC
from src.evaluation.deterministic import (
    check_jargon,
    check_teaching_order,
    check_language,
    check_viable,
    measure,
    run_deterministic_gates,
    split_sentences,
)

SIMPLE = " ".join(["The cat sat on the mat. The dog ran fast."] * 60)


class TestSentenceSplitting:

    def test_list_items_are_separate_sentences(self):
        """Regression: list items were glued into one huge pseudo-sentence,
        inflating every length metric and failing lessons for a parsing bug."""
        md = """Here is the process:

1. **Receive a question**: someone asks something
2. **Search for information**: the system looks it up
3. **Write a reply**: the system answers
"""
        sentences = split_sentences(md)
        assert len(sentences) >= 6
        assert all(len(s.split()) < 15 for s in sentences)

    def test_headings_are_not_prose(self):
        assert split_sentences("## What Is RAG?\n") == []

    def test_emphasis_markers_are_stripped(self):
        assert "*" not in " ".join(split_sentences("This is **bold** text.\n"))


class TestLanguageGate:

    def test_plain_prose_passes(self):
        assert check_language(SIMPLE, measure(SIMPLE)) is None

    def test_academic_register_fails(self):
        """Regression: calibration caught the LLM judge waving this through —
        it reads the analogies, forms an impression of 'simple', and misses the
        register entirely. So register is counted, not judged."""
        formal = (
            "The system utilises a methodology that facilitates the requisite "
            "paradigm, subsequently leveraging the aforementioned salient "
            "optimal approach encompassing myriad inherent considerations. " * 6
        )
        result = check_language(formal, measure(formal))
        assert result is not None
        assert "formal words" in result.reason

    def test_long_sentences_fail(self):
        long_one = (
            "This particular sentence has been written deliberately so that it "
            "runs on well past the point where any reader with limited English "
            "could reasonably be expected to follow it without losing track. "
        ) * 8
        result = check_language(long_one, measure(long_one))
        assert result is not None and "run over" in result.reason

    def test_long_sentence_ratio_scales_with_length(self):
        """A ratio, not a count — a longer lesson is not punished for length."""
        unit = "This sentence is short. "
        assert (
            measure(unit * 5)["long_sentence_ratio"]
            == measure(unit * 40)["long_sentence_ratio"]
        )

    def test_failure_names_the_offending_words(self):
        """Generic advice moved the metric 0.1 across a whole revision;
        naming the specific words is what makes feedback actionable."""
        formal = (
            "The system utilises a methodology paradigm that facilitates the "
            "requisite optimal outcome, subsequently leveraging myriad salient "
            "inherent factors. " * 8
        )
        result = check_language(formal, measure(formal))
        assert result is not None
        # The point is that it names concrete words to replace, not which ones.
        named = [w for w in ("utilise", "methodology", "facilitates", "paradigm")
                 if w in result.evidence.lower()]
        assert named, f"feedback should name specific words, got: {result.evidence}"


class TestJargonGate:

    def test_explained_term_passes(self):
        text = (
            "An embedding is a way of turning words into numbers. "
            "Think of it as giving every idea a position on a map."
        )
        assert check_jargon(text) is None

    def test_unexplained_term_fails(self):
        text = "We store the embedding in a vector database and query it later."
        result = check_jargon(text)
        assert result is not None and "embedding" in result.reason

    def test_topic_vocabulary_is_not_flagged(self):
        """Regression: a lesson ON retrieval was failed for using the word
        'retrieval'. Its own subject is not unexplained jargon."""
        text = "Retrieval finds the right documents. Generation writes the answer."
        assert check_jargon(text, "RAG (Retrieval-Augmented Generation)") is None

    def test_term_named_only_as_further_reading_is_not_flagged(self):
        """Regression from a live run: a lesson failed for not defining
        'machine learning', which appeared once — in the closing sentence, as a
        suggested next topic. A pointer to further reading is not a concept the
        lesson relies on."""
        body = "The system finds the right page and writes a short reply. " * 20
        text = body + "Next you could explore machine learning."
        assert check_jargon(text) is None

    def test_term_used_in_the_body_is_still_flagged(self):
        """The tail exemption must not become a loophole."""
        text = (
            "We use machine learning to rank the results. "
            + "The system then writes a reply. " * 20
        )
        assert check_jargon(text) is not None

    def test_definition_must_be_near_the_term(self):
        """Regression: a wide character window let an unrelated 'Think of...'
        elsewhere in the paragraph count as a definition, so a dense jargon
        paragraph scored as fully explained."""
        far = (
            "We use an embedding for every document. "
            + "Filler sentence with no explanation at all. " * 6
            + "Think of a library as a place with books."
        )
        assert check_jargon(far) is not None


class TestViabilityFloor:

    def test_degenerate_output_fails(self):
        tiny = "RAG is good."
        result = check_viable(tiny, measure(tiny))
        assert result is not None
        assert result.name == "covers_key_points"

    def test_normal_length_passes(self):
        """The floor is a sanity guard, NOT a quality gate. Whether the lesson
        genuinely covers what/why/how is decided by the judge reading it."""
        text = "word " * (RUBRIC.min_viable_words + 50)
        assert check_viable(text, measure(text)) is None


class TestNoInventedRequirements:
    """The brief asks for what it is, why it matters, how it works — nothing
    about quizzes, summaries, heading order, word bands or example quotas.

    An earlier version gated on all of those and failed good lessons for
    missing requirements nobody asked for. These tests keep them gone.
    """

    def test_no_quiz_requirement(self):
        text = "word " * 400
        failures, _ = run_deterministic_gates(text, "Topic")
        assert not any("quiz" in f.reason.lower() for f in failures)

    def test_no_summary_or_section_requirement(self):
        text = "word " * 400
        failures, _ = run_deterministic_gates(text, "Topic")
        assert not any(
            w in f.reason.lower() for f in failures for w in ("summary", "section")
        )

    def test_reading_grade_is_measured_but_never_gates(self):
        """FK correlates weakly with human judgement and is trivially gamed, so
        it informs the judge as evidence rather than deciding anything."""
        hard = "The epistemological ramifications necessitate consideration. " * 30
        failures, metrics = run_deterministic_gates(hard, "Topic")
        assert "fk_grade" in metrics
        assert not any("flesch" in f.reason.lower() for f in failures)


def test_gates_return_metrics_alongside_failures():
    failures, metrics = run_deterministic_gates(SIMPLE, "Topic")
    assert "word_count" in metrics and "fk_grade" in metrics
    assert isinstance(failures, list)


class TestTeachingOrder:
    """Ordering is arithmetic, so it belongs in Tier 1 — the judge cannot see it.

    Shown a fully reversed lesson, the LLM judge described the flow as
    "logical", because each section reads fine in isolation.
    """

    ORDERED = (
        "# How the Internet Works\n\n"
        "## What is the Internet?\ntext\n\n"
        "## Why Does It Matter?\ntext\n\n"
        "## How Does It Work?\ntext\n"
    )

    def test_correct_order_passes(self):
        assert check_teaching_order(self.ORDERED) is None

    def test_title_is_not_mistaken_for_a_section(self):
        """Regression from a live run: the topic "How the Internet Works" put
        "how ... works" in the TITLE, above every section, so a correctly
        ordered lesson was failed for explaining the mechanism first."""
        assert check_teaching_order(self.ORDERED) is None, (
            "the document title must not participate in section ordering"
        )

    def test_how_before_what_fails(self):
        reversed_doc = (
            "# A Lesson\n\n## How Does It Work?\ntext\n\n## What is It?\ntext\n"
        )
        result = check_teaching_order(reversed_doc)
        assert result is not None and "before" in result.reason

    def test_out_of_order_steps_fail(self):
        doc = "# T\n\n## Steps\n\n### Step 3: Last\nx\n\n### Step 1: First\ny\n"
        result = check_teaching_order(doc)
        assert result is not None and "out of order" in result.reason

    def test_ordered_steps_pass(self):
        doc = "# T\n\n## Steps\n\n### Step 1: First\nx\n\n### Step 2: Then\ny\n"
        assert check_teaching_order(doc) is None
