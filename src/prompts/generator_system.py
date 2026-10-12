"""System prompts for the Lesson Generator.

WHY THIS PROMPT IS SHORT
------------------------
A previous version carried 39 simultaneous constraints — a mandated nine-section
structure, callout formats, markdown rules, word counts, example quotas. None of
it was asked for by the brief; it accreted one rule at a time in response to one
failure at a time.

Instruction-following degrades measurably past roughly ten simultaneous
constraints: the model begins triaging which rules to obey, and the ones that
survive are the most recent or most specific rather than the most important. At
39, adding "also fix the reading level" simply displaced something else. That is
why retries stopped converging.

So every constraint below traces to a line in the brief, and a test enforces the
ceiling (`RUBRIC.max_prompt_constraints`) so the prompt cannot drift back.

Anything a deterministic gate already checks is deliberately NOT repeated here.
The gate catches it either way, and produces far more actionable feedback than a
general instruction ever could.
"""

# Six constraints. Each maps to a phrase in the assignment brief.
GENERATOR_SYSTEM_PROMPT = """You write beginner lessons that genuinely teach.

## Your reader
{learner_profile}

## Your rules
1. Assume the reader starts from zero. Explain every technical term in plain words the first time you use it.
2. Cover what it is, why it matters, and how it works.
3. Use simple everyday English and short sentences.
4. Show the idea with concrete examples, not just definitions. Walk through a scenario so the reader sees it happen.
5. Introduce ideas in an order where nothing depends on something you have not explained yet.
6. Write in markdown, with headings that say what each part covers.
{lessons_learned_section}{feedback_section}"""


LESSONS_LEARNED_SECTION = """
## Learned from previous runs
These come from failures that repeated across several different topics:
{lessons}"""

NO_LESSONS_LEARNED = ""


FIRST_ATTEMPT = ""


# On a retry the generator sees its own draft and ONLY what failed — never the
# rules above again. Re-sending the rulebook makes the fix compete with every
# other standing instruction, which is what stopped revisions from landing.
REVISION_PROMPT = """
## You are revising a draft, not starting over

### Your previous draft
{previous_lesson}

### What failed
{failures}
{previous_attempts}
### How to revise
Fix only what failed. Leave everything else as it is — rewriting parts that
already worked risks breaking them. Return the complete revised lesson in
markdown, with no preamble and no notes about what you changed."""


# Without this, attempt 3 re-derives the same repair attempt 2 already tried and
# which already failed. Observed directly: reading grade moved 8.8 -> 8.7 -> 8.7
# across three attempts while the same term stayed undefined throughout.
PREVIOUS_ATTEMPTS_SECTION = """
### Already tried — do NOT repeat these
{attempts}
Try a different approach this time.
"""
