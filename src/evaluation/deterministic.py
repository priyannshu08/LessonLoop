"""Tier 1 — deterministic gates. No LLM, no cost, identical every run.

WHAT BELONGS HERE
-----------------
Anything countable. An LLM is the wrong instrument for counting: it is slow,
costs tokens, drifts between runs, and exhibits verbosity bias — so asking it to
judge length actively teaches the generator to pad.

Two checkpoints are settled here:

    beginner_friendly_language  -> sentence length, formal-word register
    no_unexplained_jargon       -> is each technical term explained near first use

A deterministic verdict is authoritative: the LLM cannot overturn it. That is
the point — these are the bars the generator cannot talk its way past.

WHAT DELIBERATELY IS NOT HERE
-----------------------------
Flesch-Kincaid is measured and reported, but does NOT gate. Readability formulas
correlate weakly with human judgement of text difficulty (roughly -0.15 to 0.43
at sentence level), they are calibrated on native speakers rather than our
learner, and they are trivially gamed — choppy monosyllabic prose scores well.
Using one as a hard gate while also telling the generator to lower it is
optimising a weak proxy. So FK travels to the judge as *evidence* instead.

Also gone, because the brief never asked for them: word-count bands, required
quiz/summary sections, mandated heading order, example quotas.
"""

import re

import textstat

from src.config import RUBRIC
from src.schemas.evaluation import CheckpointResult

# Formal words that always have a plain everyday equivalent. Counting these is
# more reliable than asking a judge, which reads the analogies, forms an
# impression of "simple", and misses the register entirely.
ACADEMIC_WORDS = [
    "utilise", "utilize", "utilisation", "utilization", "facilitate",
    "facilitates", "demonstrate", "demonstrates", "ascertain", "ascertains",
    "methodology", "paradigm", "leverage", "leverages", "inherent",
    "substantial", "engender", "engenders", "efficacious", "subsequently",
    "furthermore", "commence", "commences", "endeavour", "endeavor",
    "aforementioned", "constitutes", "comprises", "encompasses", "pertaining",
    "requisite", "optimal", "myriad", "salient", "elucidate", "predicated",
    "conceptualisation", "conceptualization",
]

# Terms a reader with no technical background will not know.
#
# Deliberately short, and words with an everyday sense are excluded. Two were
# removed after live runs flagged them wrongly:
#   "database" — flagged "movie databases", which reads like "website" to a
#                reader the brief describes as familiar with everyday technology
#   "prompt"   — flagged "questions or prompts", the ordinary English sense
# An ambiguous word produces false failures that no revision can fix, because
# the generator is right and the gate is wrong.
TECHNICAL_TERMS = [
    "AI", "artificial intelligence", "machine learning", "LLM",
    "large language model", "model", "algorithm", "neural network",
    "embedding", "vector", "vector database", "API", "token", "retrieval",
    "generation", "augmentation", "training data", "dataset",
    "hallucination", "semantic search", "corpus", "inference", "parameter",
]

# A term counts as explained if one of these appears in the same sentence or the
# next one. Excludes bare punctuation and "which is": those matched almost any
# clause within range, so a dense jargon paragraph scored as fully defined.
# Share of the lesson treated as the closing tail, where terms are typically
# named as further-reading pointers rather than taught.
TAIL_FRACTION = 0.15

DEFINITION_MARKERS = [
    r"\bmeans\b", r"\bis simply\b", r"\bthink of\b", r"\bimagine\b",
    r"\bis like\b", r"\bare like\b", r"\bin simple words\b", r"\bthat is,\b",
    r"\bi\.e\.", r"\bin other words\b", r"\brefers to\b", r"\bis a way\b",
    r"\bare a way\b", r"\bis just\b", r"\bare just\b", r"\bis called\b",
    r"\bwe call\b", r"\bworks like\b", r"\bis basically\b", r"\bstands for\b",
    r"\bis when\b", r"\bare when\b", r"\bkey term\b",
]


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, respecting markdown block structure.

    Markdown blocks matter. A heading or a list item carries no terminal
    punctuation, so a naive split glues consecutive items into one enormous
    pseudo-sentence — an eight-step numbered process reads as a single 50-word
    sentence and inflates every length metric. The gate would then penalise the
    lesson for a parsing artefact rather than for hard prose.
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)   # drop code fences

    units = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or re.match(r"^#+\s", line):              # skip headings
            continue
        line = re.sub(r"^\s*(?:[-*>]|\d+\.)\s+", "", line)    # strip list markers
        line = re.sub(r"[*_`]", "", line)                     # strip emphasis
        if not line.strip():
            continue
        # A colon introducing a list or a step also ends a unit.
        for part in re.split(r":\s+|(?<=[.!?])\s+", line):
            part = part.strip()
            if part and re.search(r"[A-Za-z]", part):
                units.append(part)
    return units


def _topic_vocabulary(text: str, topic: str) -> set[str]:
    """Words belonging to the topic itself, which are not "jargon" to flag.

    A lesson *on* retrieval is a definition of retrieval, delivered across the
    whole text. Flagging its own subject produced false alarms on lessons that
    explain themselves perfectly well.
    """
    words = set()
    if topic:
        words.add(topic.lower())
        for group in re.findall(r"\(([^)]+)\)", topic):
            words.add(group.lower())
        for token in re.findall(r"[A-Za-z-]{3,}", topic):
            words.add(token.lower())
            # Split hyphenated terms into their parts. "Retrieval-Augmented"
            # is one token, so without this a lesson on RAG was flagged for
            # using the word "retrieval" — its own subject.
            words.update(part.lower() for part in token.split("-") if len(part) >= 3)
    return words


def measure(text: str, topic: str = "") -> dict:
    """Compute every deterministic metric once, so gates and logs agree.

    `fk_grade` is reported for the judge and the audit trail. It does not gate.
    """
    words = text.split()
    sentences = split_sentences(text)
    n = max(len(sentences), 1)

    long_sentences = [s for s in sentences if len(s.split()) > RUBRIC.long_sentence_words]
    academic = sorted({
        w for w in ACADEMIC_WORDS if re.search(rf"\b{w}\b", text, re.IGNORECASE)
    })

    return {
        "word_count": len(words),
        "sentence_count": len(sentences),
        "avg_sentence_words": round(len(words) / n, 1),
        "long_sentence_count": len(long_sentences),
        "long_sentence_ratio": round(len(long_sentences) / n, 3),
        "longest_sentences": sorted(long_sentences, key=lambda s: -len(s.split()))[:3],
        "academic_words": academic,
        # Reported as evidence for the judge — never gated on. See module docstring.
        "fk_grade": round(textstat.flesch_kincaid_grade(text), 1),
        "reading_ease": round(textstat.flesch_reading_ease(text), 1),
    }


def check_language(text: str, m: dict) -> CheckpointResult | None:
    """Gate: sentence length and register. Returns None when it passes."""
    problems, evidence = [], []

    if m["long_sentence_ratio"] > RUBRIC.max_long_sentence_ratio:
        problems.append(
            f"{m['long_sentence_ratio']:.0%} of sentences run over "
            f"{RUBRIC.long_sentence_words} words (limit "
            f"{RUBRIC.max_long_sentence_ratio:.0%}). Split the longest ones."
        )
        if m["longest_sentences"]:
            evidence.append(f'Longest: "{m["longest_sentences"][0][:150]}"')

    if len(m["academic_words"]) > RUBRIC.max_academic_words:
        problems.append(
            f"Uses {len(m['academic_words'])} formal words where everyday ones "
            f"exist (limit {RUBRIC.max_academic_words}). Replace each with the "
            f"simplest word that means the same thing."
        )
        evidence.append(f"Formal words: {', '.join(m['academic_words'][:8])}")

    if not problems:
        return None

    return CheckpointResult(
        name="beginner_friendly_language",
        evidence=" ".join(evidence) or "(nothing found)",
        reason=" ".join(problems),
        passed=False,
    )


def check_jargon(text: str, topic: str = "") -> CheckpointResult | None:
    """Gate: is each technical term explained near its first use?

    Scoped to the sentence containing the first use plus the next one. A wider
    character window let an unrelated "Think of..." elsewhere in the paragraph
    satisfy the marker, so a dense jargon paragraph scored as fully defined.
    """
    sentences = split_sentences(text)
    topic_words = _topic_vocabulary(text, topic)

    # Sentences in the closing tail are usually "what to explore next" pointers.
    # A term named only there is a suggestion for further reading, not a concept
    # the lesson relies on, so demanding a definition is over-strict. Caught by
    # a real run: a lesson failed for not defining "machine learning", which
    # appeared once, in the final sentence, as a suggested next topic.
    body_end = max(1, int(len(sentences) * (1 - TAIL_FRACTION)))

    undefined = []
    for term in TECHNICAL_TERMS:
        if term.lower() in topic_words:
            continue
        pattern = rf"\b{re.escape(term)}s?\b"
        hits = [
            i for i, s in enumerate(sentences) if re.search(pattern, s, re.IGNORECASE)
        ]
        if not hits or hits[0] >= body_end:
            continue

        first = hits[0]
        window = " ".join(sentences[first:first + RUBRIC.jargon_definition_window])
        if not any(re.search(p, window, re.IGNORECASE) for p in DEFINITION_MARKERS):
            undefined.append(term)

    if not undefined:
        return None

    shown = undefined[:6]
    return CheckpointResult(
        name="no_unexplained_jargon",
        evidence=f"Used without explanation: {', '.join(shown)}",
        reason=(
            f"{len(undefined)} technical term(s) appear with no explanation in "
            f"the same or next sentence: {', '.join(shown)}. Explain each in "
            f"plain words the first time it appears."
        ),
        passed=False,
    )


def check_teaching_order(text: str) -> CheckpointResult | None:
    """Gate: does the lesson explain WHAT before HOW?

    The brief names "coherent teaching flow" as a dimension and asks the lesson
    to cover "what it is, why it matters, and how it works". Explaining the
    mechanism before the reader knows what the thing IS is the clearest possible
    violation of that.

    This is a narrow, countable reading of the brief's own words — NOT a mandated
    structure. It says nothing about which sections must exist, only that if both
    a "what" and a "how" heading are present, "what" comes first.

    It exists because calibration showed the judge cannot see ordering: shown a
    fully REVERSED lesson, it described the flow as "logical". Sequence
    comparison is arithmetic, so it belongs here rather than with a judge.
    """
    # SECTION headings only — never the document title.
    #
    # The title restates the topic, so for a topic like "How the Internet Works"
    # it necessarily contains "how ... works" and sits above every section. The
    # gate then reported a perfectly ordered lesson (What -> Why -> How) as
    # explaining the mechanism first. Caught by running a topic whose name is a
    # question; it never surfaced while only RAG was tested.
    headings = re.findall(r"^#{2,4} .*$", text, re.MULTILINE)

    # Numbered steps must appear in numeric order. A lesson presenting Step 4
    # before Step 1 is incoherent by any reading, and it is pure arithmetic to
    # detect — which is exactly why the judge missed it: shown such a lesson it
    # described the flow as "logical", because each section reads fine alone.
    steps = [
        int(m.group(1))
        for h in headings
        if (m := re.search(r"\bstep\s+(\d+)\b", h, re.I))
    ]
    if len(steps) >= 2 and steps != sorted(steps):
        return CheckpointResult(
            name="coherent_teaching_flow",
            evidence=f"Steps appear in this order: {steps}",
            reason=(
                f"The numbered steps are out of order ({steps}). A reader cannot "
                f"follow a process presented out of sequence. Put them in order."
            ),
            passed=False,
        )

    what = next(
        (i for i, h in enumerate(headings) if re.search(r"\bwhat (is|are)\b", h, re.I)),
        None,
    )
    how = next(
        (i for i, h in enumerate(headings) if re.search(r"\bhow\b.*\bwork", h, re.I)),
        None,
    )
    if what is None or how is None or what < how:
        return None

    return CheckpointResult(
        name="coherent_teaching_flow",
        evidence=f'"{headings[how].strip()}" appears before "{headings[what].strip()}"',
        reason=(
            "The lesson explains how it works before explaining what it is, so "
            "the reader meets the mechanism before the concept. Introduce what "
            "it is first."
        ),
        passed=False,
    )


def check_viable(text: str, m: dict) -> CheckpointResult | None:
    """Sanity floor — catches a degenerate output before a judge call is spent.

    Explicitly NOT a quality criterion. Whether the lesson actually covers what
    it should is decided by `covers_key_points`, which reads the content.
    """
    if m["word_count"] >= RUBRIC.min_viable_words:
        return None
    return CheckpointResult(
        name="covers_key_points",
        evidence=f"Word count: {m['word_count']}",
        reason=(
            f"The lesson is only {m['word_count']} words — too short to teach "
            f"what it is, why it matters and how it works. Explain each of the "
            f"three properly."
        ),
        passed=False,
    )


def run_deterministic_gates(
    text: str, topic: str = ""
) -> tuple[list[CheckpointResult], dict]:
    """Run every deterministic gate.

    Returns (failures, metrics). An empty failure list means all gates passed.
    """
    metrics = measure(text, topic)
    candidates = [
        check_language(text, metrics),
        check_jargon(text, topic),
        check_viable(text, metrics),
        check_teaching_order(text),
    ]
    return [c for c in candidates if c is not None], metrics
