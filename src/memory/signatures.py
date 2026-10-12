"""Failure signatures — the unit of "has this happened before?".

The brief asks the system to learn from REPEATED failures. To act on that word,
the system must recognise when two failures on different runs are the same KIND
of failure. Raw text cannot do this: two runs failing the same way produce
differently-worded complaints, so counting text gives everything a count of one.

A signature is a stable label for the CAUSE — `no_unexplained_jargon::undefined`
— derived from the checkpoint plus the shape of its reason.

GRANULARITY
-----------
The governing rule is: one signature = one root cause = one fix. Too coarse
("jargon failed") and the resulting rule is vague. Too fine ("the word embedding
was undefined in run 4") and nothing ever recurs, so nothing is ever learned.

Anything that would need a DIFFERENT fix gets a different signature. This
taxonomy is not final — it should be revisited as new failure shapes appear.
"""

import re

# Ordered per checkpoint; first match wins, so specific patterns come first.
_SIGNATURE_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "beginner_friendly_language": [
        (r"run over \d+ words|long.sentence", "long_sentences"),
        (r"formal words", "formal_register"),
        (r"textbook|encyclopa|academic", "academic_tone"),
    ],
    "no_unexplained_jargon": [
        (r"no explanation|without explanation", "undefined_terms"),
        (r"circular", "circular_definitions"),
        (r"abstract|no wiser", "weak_definitions"),
    ],
    "covers_key_points": [
        (r"only \d+ words|too short", "too_short"),
        (r"what it is|missing what", "missing_what"),
        (r"why it matters|missing why", "missing_why"),
        (r"how it works|sequence|process", "missing_how"),
        (r"heading with nothing|restatement|name only|shallow", "shallow_sections"),
    ],
    "teaches_by_example": [
        (r"walks? through|only defines|names a situation", "examples_not_worked"),
        (r"no example|without.*example", "no_examples"),
    ],
    "coherent_teaching_flow": [
        (r"before it is explained|not met yet|prerequisite", "ordering"),
        (r"disconnected|stapled|jump", "transitions"),
    ],
    "accurate_and_grounded": [
        (r"does not happen|wrong|incorrect|false", "incorrect_claim"),
        (r"distort|misleading", "misleading_simplification"),
    ],
}


def failure_signature(checkpoint: str, reason: str) -> str:
    """Map a failed checkpoint plus its reason onto a stable cause label."""
    reason = (reason or "").lower()
    for pattern, cause in _SIGNATURE_PATTERNS.get(checkpoint, []):
        if re.search(pattern, reason):
            return f"{checkpoint}::{cause}"
    return f"{checkpoint}::general"


def describe_signature(signature: str) -> str:
    """Human-readable gloss, used in consolidation prompts and audit output."""
    checkpoint, _, cause = signature.partition("::")
    return f"{checkpoint.replace('_', ' ')} — {cause.replace('_', ' ')}"
