"""Configuration — every threshold and setting in one place.

DESIGN NOTE
-----------
Everything here traces to a line in the assignment brief. An earlier version of
this project enforced quiz sections, summary sections, a mandated heading order,
an 800-word floor and a Flesch-Kincaid cap — none of which the brief asks for.
Those invented requirements were the main source of failures and complexity.

If a setting below cannot be justified by a line in the brief, or by a measured
result, it should not exist.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# === Project Paths ===
PROJECT_ROOT = Path(__file__).parent.parent
MEMORY_DIR = PROJECT_ROOT / "memory"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

MEMORY_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)

# Two memory tiers: observations are episodic (what happened — unbounded, never
# shown to a model); rules are procedural (what is generally true — capped, and
# injected into every generation).
OBSERVATIONS_FILE = MEMORY_DIR / "observations.json"
LESSONS_LEARNED_FILE = MEMORY_DIR / "lessons_learned.json"
RUN_HISTORY_FILE = MEMORY_DIR / "run_history.json"
PROMPT_EVOLUTION_FILE = MEMORY_DIR / "prompt_evolution.json"

# === LLM Configuration ===
GENERATOR_MODEL = os.getenv("GENERATOR_MODEL", "gpt-4o-mini")
EVALUATOR_MODEL = os.getenv("EVALUATOR_MODEL", "gpt-4o")

GENERATOR_TEMPERATURE = 0.7   # some variation keeps the writing from going flat
EVALUATOR_TEMPERATURE = 0.0   # judging should be as reproducible as possible

# NOTE: generator and judge are the same vendor, which leaves a known
# self-enhancement bias in place. Documented in DESIGN.md rather than hidden.


@dataclass(frozen=True)
class Rubric:
    """Measurable thresholds for the deterministic half of the rubric.

    Only three settings survive here. Everything else the old version gated on
    (word count band, example counts, Indian-context requirement, reading-grade
    cap, required sections) was invented rather than asked for, and is gone.
    """

    # Sentences longer than this are hard for a reader with limited English.
    # Expressed as a RATIO so a longer lesson is not punished for being longer.
    long_sentence_words: int = 25
    max_long_sentence_ratio: float = 0.15

    # Formal words that always have a plain equivalent ("utilise" for "use").
    # A small budget rather than zero — one or two may be unavoidable, a dozen
    # means the lesson is written in the wrong register for this reader.
    max_academic_words: int = 4

    # How near a term's first use its explanation must appear, in sentences.
    jargon_definition_window: int = 2

    # Sanity floor only — catches a degenerate output before we spend a judge
    # call on it. NOT a quality criterion: "covers the key points" decides that.
    min_viable_words: int = 250

    # 1 initial attempt + 2 retries. The brief says "max 1-2 retries, so the
    # loop always terminates".
    max_attempts: int = 3

    # Hard ceiling on generator prompt constraints. Instruction-following
    # degrades measurably past ~10 simultaneous constraints; a previous version
    # of this prompt carried 39 and the model simply triaged which to obey.
    # Enforced by a test so the prompt cannot drift back into bloat.
    max_prompt_constraints: int = 10


RUBRIC = Rubric()

MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", str(RUBRIC.max_attempts)))
MAX_RETRIES = MAX_ATTEMPTS - 1        # backwards-compatible alias


@dataclass(frozen=True)
class MemoryPolicy:
    """Governs what enters long-term memory, what leaves, and when to give up.

    Every value here bounds a feedback loop. Unconstrained self-evolution does
    not accumulate capability — it drifts.
    """

    # A failure must recur on this many DISTINCT TOPICS before it can become a
    # rule. Topics, not runs: three failures on three RAG runs prove nothing
    # about Photosynthesis, so requiring topic diversity makes the resulting
    # rule topic-independent by construction rather than by hoping the LLM
    # writes something universal.
    promotion_topic_threshold: int = 3

    # Hard ceiling on injected rules.
    #
    # This is NOT an independent choice — it is the prompt budget minus the base
    # prompt's own constraints (10 - 6 = 4). Setting the two caps separately is
    # how they drift out of sync: the rule cap looked fine, the base prompt
    # looked fine, and together they blew the budget. A test enforces the
    # relationship so raising one forces you to lower the other.
    max_active_rules: int = 4

    # A newly promoted rule must prove itself over this many runs.
    probation_runs: int = 3

    # How many DIFFERENT rules to try for one failure pattern before concluding
    # the pattern is not fixable by prompting. Without this the system drops a
    # failed rule, re-detects the same pattern, regenerates the same rule, and
    # loops forever.
    max_fix_attempts_per_signature: int = 3

    # Observations older than this many runs are ignored when counting
    # recurrence, so the system can move on from patterns it has solved.
    recurrence_window_runs: int = 20


MEMORY_POLICY = MemoryPolicy()

# === Learner Profile — straight from the brief ===
TARGET_LEARNER_PROFILE = """12th-grade graduate from India. Limited English
vocabulary, from a non-English-medium school. No background in AI, programming
or technology beyond everyday smartphone use. Wants to start a career in AI."""

# === Rubric Checkpoints — one per dimension named in the brief ===
CHECKPOINT_NAMES = [
    "accurate_and_grounded",
    "beginner_friendly_language",
    "teaches_by_example",
    "no_unexplained_jargon",
    "covers_key_points",
    "coherent_teaching_flow",
]

# Human labels, and which tier decides each. Surfacing the tier is the point:
# it shows a reader that half the rubric is measured rather than judged.
CHECKPOINT_LABELS = {
    "accurate_and_grounded": "Accurate & grounded",
    "beginner_friendly_language": "Beginner-friendly language",
    "teaches_by_example": "Teaches by example",
    "no_unexplained_jargon": "No unexplained jargon",
    "covers_key_points": "Covers the key points",
    "coherent_teaching_flow": "Coherent teaching flow",
}

CHECKPOINT_TIERS = {
    "accurate_and_grounded": "JUDGED",
    "beginner_friendly_language": "MEASURED",
    "teaches_by_example": "JUDGED",
    "no_unexplained_jargon": "MEASURED",
    "covers_key_points": "JUDGED",
    "coherent_teaching_flow": "HYBRID",
}
