# LessonLoop


A program that writes a beginner lesson on any topic, checks its own work against six pass/fail rules, and rewrites the lesson until it passes. A human only sees the result after the program decides it is good enough.

> **Who the lessons are for:** a 12th-grade graduate from India. Limited English vocabulary, non-English-medium school, no background in AI or programming.

**Results so far:** 21 runs on 16 topics, 81% passed. The system learned one rule by itself and confirmed it helped. 86 tests, none need an API key.

---

## Quick start

```bash
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # then add your OPENAI_API_KEY

python main.py --topic "RAG (Retrieval-Augmented Generation)"   # run from the command line
streamlit run app.py                                            # run the web UI
pytest tests/ -q                                                # run the 86 tests (offline)
```

| Flag | What it does |
|:---|:---|
| `--topic "..."` | The lesson topic. Default is RAG |
| `--max-retries N` | Retries after the first draft. Default 2, so 3 attempts in total |
| `--clear-memory` | Deletes learned rules and observations before running |

> **Tip:** Restart Streamlit after changing any code. It caches imported modules, so an old server can run stale code and make correct code look broken.

---

## Contents

- [How it works (the loop)](#how-it-works-the-loop)
- [The six rules (rubric)](#the-six-rules-rubric)
- [How a lesson is scored](#how-a-lesson-is-scored)
- [Memory: learning from repeated failures](#memory-learning-from-repeated-failures)
- [Why the prompt stays small](#why-the-prompt-stays-small)
- [Checking the judge](#checking-the-judge)
- [Output files](#output-files)
- [The UI](#the-ui)
- [Design choices and trade-offs](#design-choices-and-trade-offs)
- [Ideas not built](#ideas-not-built)
- [Project structure](#project-structure)
- [Known limitations](#known-limitations)

---

## How it works (the loop)

```text
Topic
  │
  ▼
1. INITIALIZE   load memory, add up to 4 learned rules to the prompt
  │
  ▼
2. GENERATE     (API call)
  │             Attempt 1:  6 base rules + topic
  │             Attempt 2+: previous draft + only what failed
  ▼
3. EVALUATE
  │   ├── Measured checks (plain code, fast)
  │   └── Judged checks   (one gpt-4o call)
  ▼
4. MERGE        measured result wins over judged result
  │             anything not evaluated counts as FAILED
  ▼
   Did all 6 checks pass?
  │
  ├── NO  → write feedback → go back to GENERATE
  │
  └── YES (or attempts used up)
          │
          ▼
5. PICK BEST    ship the highest-scoring attempt
          │
          ▼
6. UPDATE MEMORY  record failures, maybe learn a new rule
          │
          ▼
       Lesson output
```

Three things to know:

- **It always stops.** The attempt counter only goes up, so there are at most 3 attempts (1 draft + 2 retries). Even if the judge crashes, the counter still moves forward.
- **It ships the best attempt, not the last one.** A later rewrite can be worse than an earlier one. Every attempt gets a score, and the best one is shipped.
- **A retry only sees what failed.** It gets the old draft, the failures, and what was already tried. It does not get the full rulebook again, because the fix would then compete with every other rule.

---

## The six rules (rubric)

Each rule is pass or fail. There is no partial credit. **All six must pass.**

| # | Rule | Who decides |
|:--|:---|:---|
| 1 | `accurate_and_grounded` | Judge (LLM) |
| 2 | `beginner_friendly_language` | Measured (code) |
| 3 | `teaches_by_example` | Judge (LLM) |
| 4 | `no_unexplained_jargon` | Measured (code) |
| 5 | `covers_key_points` | Judge (LLM) |
| 6 | `coherent_teaching_flow` | Both (hybrid) |

### Things we deliberately do NOT check

Quiz sections, summary sections, a fixed heading order, a word-count range, example quotas, and a Flesch-Kincaid reading-grade limit.

An older version checked all of these. The assignment did not ask for any of them, and they caused most failures. One lesson was rejected for being six words short of a number we had invented.

**Flesch-Kincaid** is still calculated and shown to the judge as extra information, but it never decides pass or fail. Readability formulas are a weak match for human opinion, they are built for native English speakers, and they are easy to game with short, choppy sentences.

---

## How a lesson is scored

```text
                a lesson
                   │
      ┌────────────┴────────────┐
      ▼                         ▼
 MEASURED (code)           JUDGED (1 LLM call)
 • long sentences ≤ 15%    • accurate and grounded
 • formal words ≤ 4        • teaches by example
 • jargon explained        • covers what / why / how
   within 2 sentences      • coherent flow
 • teaching order
      │                         │
      └────────────┬────────────┘
                   ▼
                 MERGE
                   │
        6 pass/fail verdicts
```

Two merge rules:

1. **Measured beats judged.** If the code says "fail", the judge cannot overrule it.
2. **Unchecked means failed.** If the judge skips or misnames a rule, that rule is marked failed. Nothing passes by default.

The judge's answer is forced into this order: **evidence → reasoning → verdict**. The judge has to quote and explain before it is allowed to say pass or fail, so it cannot decide first and invent a reason later.

---

## Memory: learning from repeated failures

The system only learns from failures that **repeat**.

```text
A check fails
   │
   ▼
OBSERVATION   saved to a log, never shown to a model
   │
   ▼
PATTERN       failures grouped by cause, e.g. no_unexplained_jargon::undefined_terms
   │
   │  seen on 3 DIFFERENT topics?
   ├── No  → wait for more data
   └── Yes ▼
DISTIL        (API call) write one universal rule, max 2 sentences
   │
   ▼
PROBATION     rule is used, but only provisionally, for 3 runs
   │
   ├── Helps         → ACTIVE (confirmed)
   └── Doesn't help  → DROPPED. The pattern stays open, a different rule is tried,
                       and the failed rule is never proposed again.
                       After 3 failed rules → ESCALATED to a human.
```

### Why "3 different topics" and not "3 runs"?

If one topic fails three times, a rule built from it would only fit that topic. Requiring three different topics makes the rule general by design, instead of hoping the model writes a general one.

Each failure is also given a **signature** (a stable label for the cause). The same problem described in two different ways still counts as one pattern.

### The policy at a glance

| Situation | What happens |
|:---|:---|
| A failure happens once | Saved as an observation. No rule. |
| Same failure on 3 different topics | Turned into one universal rule, on probation |
| Rule improves quality over 3 runs | Confirmed active |
| Rule doesn't help | Dropped, a different rule is tried |
| 3 different rules all fail | Escalated. Prompting can't fix it; the gate or model is the limit |
| A 5th rule arrives | The rule with the worst track record is removed. New rules are protected until they have evidence |
| Anything retired | Marked as replaced with a reason. Nothing is ever deleted |

### What actually happened

In 21 runs on 16 topics, exactly **one** rule was learned:

> *"Always introduce a technical term with a brief, simple explanation immediately upon its first use, using everyday language."*
> Learned from: **Neural Networks, RAG, Vector Databases**

Two topics that failed all three attempts (**Vector Databases** and **Machine Learning**) passed after the rule was added. Pass rate went from 71% to 92%.

> This is an **observation, not a controlled test**. The groups had different topics, so some of the gap is just topic difficulty. To compare fairly, run `python -m evals.ab_memory`, which runs the same topics with memory on and off.

---

## Why the prompt stays small

Models start ignoring instructions when there are more than about **10** at once. They tend to follow the most recent ones, not the most important ones.

An earlier prompt had **39** rules, each added in response to a real failure. Retries stopped improving the lesson because of it.

```text
Prompt budget: 10 rules total
├── Base prompt: 6   (from the assignment)
└── Memory:     ≤ 4  (a new rule must replace a worse one)
```

`tests/test_prompt_budget.py` fails the build if either limit is exceeded. Memory can learn freely, but the prompt can never grow past the budget.

---

## Checking the judge

The judge is also an LLM, so it can make the same mistakes as the model it grades. We test it:

```bash
python -m evals.build_gold_set   # build test samples with known defects
python -m evals.calibrate        # check how well the judge catches them
```

Most bad samples are **constructed**: take a lesson that passes and break one thing on purpose. The answer is then known for sure, so any disagreement is a real judge bug. The builder refuses to output a sample that is identical to the original, or one that removes more than 40% of the lesson.

Results are shown per rule as two numbers:

- **TPR:** of the truly bad samples, how many did the judge catch?
- **TNR:** of the truly good samples, how many did the judge let through?

They are kept separate because the sample is unbalanced, and one combined accuracy number would hide that.

> **Honest limitation:** there is about one bad sample per rule, so a "100%" result is only a smoke test. A proper test would need about 40 samples per rule.

---

## Output files

Each run saves files to `outputs/<topic>_<timestamp>/`:

| File | What's inside |
|:---|:---|
| `lesson.md` | The final lesson |
| `rejection_log.json` | What failed, why, and what changed on retry |
| `attempt_trail.json` | Metrics, checks and feedback for each attempt |
| `final_evaluation.json` | All six verdicts with evidence |
| `run_summary.md` | A readable summary |

The rejection log lists `checkpoints_fixed`, `checkpoints_regressed`, `checkpoints_still_failing` and `metric_deltas`. **Regressions are shown, not hidden.** A rewrite can make things worse, and a log that hides that would be misleading.

---

## The UI

```bash
streamlit run app.py
```

| Tab | What it shows |
|:---|:---|
| **Run** | The loop running live: each attempt's result, metric changes, and what was fixed or broken |
| **Evaluator lab** | Deliberately broken drafts that prove the rules can catch problems |
| **Memory** | What the system has learned, which failures created each rule, and the promotion/removal policy |
| **How it works** | Diagrams, how the rules map to the assignment, design choices, and what was not built |

---

## Design choices and trade-offs

Every choice has a cost. Listing only the benefits would be marketing.

| Decision | Why | Cost |
|:---|:---|:---|
| **Separate judge, not self-critique** | A model reviewing its own work with no outside signal doesn't improve | Generator and judge are from the same vendor, so some bias remains |
| **Pass/fail only, no scores** | Binary judges agree with humans more than 5-point scales, and are harder to game with long answers | Six gates stack up, so the first attempt often fails. The retry loop exists for this |
| **Count what can be counted** | LLM judges favour longer text, which teaches the generator to pad | Code checks are blunt and sometimes over-flag (e.g. "movie databases" once flagged as jargon) |
| **Reading grade informs, never decides** | Readability formulas are weak and easy to game | One fewer hard signal |
| **Revise, don't regenerate** | Resending all rules makes the fix compete with them, and lose | The revision keeps the draft's structure, so a bad first draft is harder to escape |
| **Ship the best attempt** | Rewrites can get worse | The shipped lesson may not be the latest one, so this must be shown clearly |
| **Learn only from repeated failures** | One run is a sample, not a pattern. Writing a rule per failure gave 20 rules, including four conflicting sentence-length limits | Learning is slow. Nothing is learned in the first few runs |
| **Hard limit on rules** | Instruction-following gets worse past ~10 rules | Memory holds at most 4 rules |

---

## Ideas not built

Knowing what *not* to build is part of good design.

- **Comprehension gate:** a separate reader model sees only the lesson and must answer what/why/how from it. Questions it could answer without the lesson are thrown out. This is the strongest idea, because it tests whether a beginner would actually *get* the lesson. It was left out because there wasn't enough time to build a fourth evaluation tier.
- **Cross-vendor judge:** use a judge from a different company than the generator to remove bias. It needs a second API key, so it is configurable but off by default.
- **Self-consistency voting:** ask the judge three times on borderline cases and take the majority. Judges give different answers even at temperature 0. Skipped because stricter severity thresholds fixed the problem more cheaply.
- **Word-frequency scoring:** rate difficulty by how common each word is, instead of syllable count. This suits non-native readers better than Flesch-Kincaid. Skipped because it needs a word corpus, and making FK informational-only captured most of the benefit.
- **A larger gold set:** about 40 labelled samples per rule instead of the current handful.

---

## Project structure

```text
LessonLoop/
├── main.py                          CLI entry point
├── app.py                           Streamlit UI
├── src/
│   ├── config.py                    Every threshold in one place
│   ├── graph.py                     LangGraph loop, routing, best-attempt pick
│   ├── nodes/
│   │   ├── generator.py             Writes the draft; revises using only the failures
│   │   ├── evaluator.py             Runs both check types and merges them
│   │   ├── feedback_compiler.py     Rejection log and change tracking
│   │   └── prompt_optimizer.py      Records observations, runs memory update
│   ├── evaluation/
│   │   └── deterministic.py         Measured checks (no LLM)
│   ├── memory/
│   │   ├── store.py                 Saves rules, tracks usefulness, tracks replacements
│   │   ├── consolidation.py         Promotion, probation, eviction, escalation
│   │   └── signatures.py            Turns a failure into a stable cause label
│   ├── prompts/                     Generator and judge prompts
│   └── schemas/                     Pydantic models and LangGraph state
├── ui/
│   ├── diagram.py                   Diagrams, generated from config
│   ├── memory_view.py               Memory tab
│   ├── architecture_view.py         How-it-works tab
│   ├── components.py                Attempt cards, check chips, gauges
│   └── theme.py                     Colours and fonts
├── evals/
│   ├── build_gold_set.py            Builds samples with known defects
│   ├── calibrate.py                 Judge vs known answers (TPR/TNR)
│   └── ab_memory.py                 Memory ON vs OFF
├── tests/                           86 tests, no network needed
├── memory/                          What the system has learned
└── outputs/                         Generated lessons and audit trails
```

The diagrams in the UI are generated by `ui/diagram.py` from `src/config.py`, so the numbers shown always match the numbers the system actually uses.

---

## Known limitations

- **Same vendor for generator and judge** (`gpt-4o-mini` / `gpt-4o`), so some self-preference bias remains. The fix is a configurable cross-vendor judge.
- **Small gold set:** about one bad sample per rule. Calibration is a smoke test, not a real measurement.
- **`teaches_by_example` has a weak test sample.** Removing "examples" automatically is unreliable, because a numbered step-by-step process is itself a walk-through. This is documented rather than hidden.
- **Not every run passes.** Across 21 runs on 16 topics, 81% passed. If a run uses all its attempts, it ships the best draft and says so. That is the gate working, not a bug.

---
