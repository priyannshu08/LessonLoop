"""Consolidation — the only path from experience into the generator's prompt.

The brief asks the system to "learn from REPEATED failures". This module is where
that word is enforced, and where four loop properties are guaranteed.

    observations (episodic, unbounded, never injected)
        │
        │ 1. RECURRENCE   same signature on >= N DIFFERENT TOPICS
        ▼
    candidate pattern
        │
        │ 2. DISTIL       one universal rule — knowing what already failed here
        │ 3. RECONCILE    duplicate / contradicts / subsumes an existing rule?
        ▼
    rule on PROBATION (injected, but provisional)
        │
   ┌────┴─────┐
 helps    doesn't help
   │           │
 ACTIVE   record as a failed fix attempt
             │
       tried < N ? ──► try a DIFFERENT rule next time
             │
       tried = N ──► ESCALATE: not fixable by prompting; tell the human

WHY EACH GUARD EXISTS
---------------------
1. RECURRENCE across TOPICS stops both noise-memorising and topic-overfitting.
   Three failures on three RAG runs say nothing about Photosynthesis.
2. DISTIL sees previous failed attempts as negative examples, so it proposes
   something different rather than re-deriving what already did not work.
3. RECONCILE merges instead of appending. Exact-string dedup cannot see that
   "under 15 words" and "under 25 words" conflict; a semantic check can.
4. ESCALATION terminates the loop. Without it, a dropped rule leaves its
   signature uncovered, the same rule is regenerated, and the system oscillates
   forever.
"""

from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from src.config import EVALUATOR_MODEL, EVALUATOR_TEMPERATURE, MEMORY_POLICY, RUBRIC
from src.memory.signatures import describe_signature
from src.memory.store import MemoryStore


class DistilledRule(BaseModel):
    """One universal rule distilled from a recurring failure pattern."""

    insight: str = Field(
        ...,
        description=(
            "A universal, topic-agnostic instruction for the generator. At most "
            "two sentences. Must apply equally to a lesson on 'Photosynthesis', "
            "'Cricket' or 'Machine Learning'. Never name a specific topic or "
            "domain term. Phrase as a direct rule: 'Always...', 'Never...'."
        ),
    )
    worth_keeping: bool = Field(
        ...,
        description=(
            "False if a mechanical gate already enforces this and a prompt rule "
            "would add nothing, or if no useful generalisation exists. Prefer "
            "False over a vague rule."
        ),
    )
    differs_from_previous: str = Field(
        ...,
        description=(
            "If earlier rules were tried for this pattern and failed, explain how "
            "this one takes a DIFFERENT approach. Write 'first attempt' if none."
        ),
    )


class Reconciliation(BaseModel):
    """How a candidate rule relates to the rules already in memory."""

    relation: Literal["duplicate", "contradicts", "subsumes", "independent"] = Field(
        ...,
        description=(
            "duplicate: says the same thing as an existing rule. contradicts: "
            "gives an incompatible instruction (especially a different threshold "
            "for the same property). subsumes: strictly more general, making an "
            "existing rule redundant. independent: unrelated to all of them."
        ),
    )
    related_rule_ids: list[str] = Field(
        default_factory=list,
        description="Ids this relates to. Empty when independent.",
    )
    merged_insight: Optional[str] = Field(
        None,
        description=(
            "For duplicate/contradicts/subsumes: ONE rule replacing the candidate "
            "and the related rules. Must stay universal and at most two sentences."
        ),
    )
    reasoning: str = Field(..., description="Why this relation was chosen.")


DISTIL_PROMPT = """You turn recurring content-generation failures into universal rules.

This failure pattern has now occurred on several DIFFERENT topics, so it is a
structural weakness rather than noise. Write ONE rule that would prevent it.

## The rule must be UNIVERSAL
It will be applied to every future lesson on every topic. A rule naming a
specific subject is useless when the next topic differs.
- BAD:  "Always define 'embedding' with a GPS analogy"      (topic-specific)
- GOOD: "Always explain every domain-specific noun in plain words on first use"

## Keep it to two sentences
Length is a regularizer. A long rule encodes the specific cases you were shown
and stops generalising; a short one has to capture the pattern.

## Prefer HOW over WHAT
These are already enforced mechanically, and the gate reports them precisely:
- sentences over {long_sentence_words} words
- formal words with everyday equivalents
- technical terms explained near first use
A rule that merely restates one of those adds nothing — set worth_keeping=false.
A rule explaining HOW to satisfy it is valuable.

## If earlier rules were tried and failed
You will be shown them. Do NOT restate them in different words — they have
already been measured and did not help. Propose a genuinely different approach,
and say how it differs.
"""

RECONCILE_PROMPT = """You maintain a small, consistent rule set for a content generator.

Every rule is injected into the same prompt, so two rules giving different
instructions for the same property actively harm output — the generator follows
neither. Decide how a new candidate relates to the existing rules, and merge
where needed.

Classify as exactly one of:
- duplicate    — substantially the same as an existing rule
- contradicts  — incompatible with one (especially a DIFFERENT NUMBER for the
                 same property)
- subsumes     — strictly more general, making an existing rule redundant
- independent  — unrelated to all of them

For anything other than `independent`, write `merged_insight`: a SINGLE rule
replacing the candidate and the related rules.

The merged rule MUST be:
1. UNIVERSAL — strip every topic-specific term and subject-specific analogy
2. ONE instruction stated ONCE — merging must shorten the rule set, not
   concatenate it
3. At most two sentences

Be strict about contradictions. Two rules naming different thresholds for the
same property are ALWAYS a contradiction, even if both sound reasonable.
"""


def _llm(schema):
    return ChatOpenAI(
        model=EVALUATOR_MODEL, temperature=EVALUATOR_TEMPERATURE
    ).with_structured_output(schema, method="function_calling")


def find_promotable_patterns(memory: MemoryStore) -> list[tuple[str, list[str]]]:
    """Stage 1 — signatures that recurred across enough DIFFERENT topics.

    Skips signatures already covered by an active rule (the rule exists; its
    utility score will retire it if it fails) and signatures already escalated
    (we concluded prompting cannot fix them — retrying is the oscillation bug).
    """
    covered = {
        r.get("signature") for r in memory.get_active_rules() if r.get("signature")
    }
    promotable = []
    for signature, topics in memory.signature_topic_counts().items():
        if signature in covered or memory.is_escalated(signature):
            continue
        if len(topics) >= MEMORY_POLICY.promotion_topic_threshold:
            promotable.append((signature, sorted(topics)))
    return promotable


def distil_rule(signature: str, examples: list[str], failed_before: list[dict]) -> Optional[str]:
    """Stage 2 — one universal rule, informed by what already failed here."""
    sample = "\n".join(f"- {e}" for e in examples[:5])

    tried = ""
    if failed_before:
        listed = "\n".join(f'- "{a["insight"]}" — {a["why"]}' for a in failed_before)
        tried = (
            f"\n\n## Already tried for this pattern, and did NOT help\n{listed}\n"
            f"Propose a different approach."
        )

    user = (
        f"## Recurring failure\n{describe_signature(signature)}\n\n"
        f"## How it was reported across topics\n{sample}{tried}\n\n"
        f"Write one universal rule that would prevent it, or set "
        f"worth_keeping=false if a mechanical gate already covers it."
    )
    result = _llm(DistilledRule).invoke([
        SystemMessage(content=DISTIL_PROMPT.format(
            long_sentence_words=RUBRIC.long_sentence_words,
        )),
        HumanMessage(content=user),
    ])
    return result.insight if result.worth_keeping else None


def reconcile(candidate: str, existing: list[dict]) -> Reconciliation:
    """Stage 3 — check the candidate against every rule already in memory."""
    if not existing:
        return Reconciliation(
            relation="independent", related_rule_ids=[], reasoning="Memory is empty."
        )
    listing = "\n".join(f'- {r["id"]}: "{r["insight"]}"' for r in existing)
    user = (
        f"## Existing rules\n{listing}\n\n"
        f'## Candidate rule\n"{candidate}"\n\n'
        f"Classify the relation and, unless independent, write a merged rule."
    )
    return _llm(Reconciliation).invoke([
        SystemMessage(content=RECONCILE_PROMPT),
        HumanMessage(content=user),
    ])


def enforce_capacity(memory: MemoryStore) -> list[dict]:
    """Stage 4 — keep the active rule set within its cap, worst utility first."""
    active = memory.get_active_rules()
    if len(active) <= MEMORY_POLICY.max_active_rules:
        return []

    def utility(rule: dict) -> tuple:
        """Ascending sort — first entries are evicted first.

        Rules too new to have a track record sort last, so a freshly promoted
        rule is never dropped before it has been tried. Among equal pass rates,
        drop the one we know least about.
        """
        runs = rule.get("runs_active", 0)
        if runs < MEMORY_POLICY.probation_runs:
            return (1, 0.0, 0)
        return (0, rule.get("runs_passed_first_attempt", 0) / runs, runs)

    evicted = []
    for rule in sorted(active, key=utility)[: len(active) - MEMORY_POLICY.max_active_rules]:
        runs = rule.get("runs_active", 0)
        rate = rule.get("runs_passed_first_attempt", 0) / runs if runs else 0
        memory.set_rule_status(
            rule["id"], "superseded",
            reason=(
                f"Evicted at capacity ({MEMORY_POLICY.max_active_rules} rules). "
                f"First-attempt pass rate {rate:.0%} over {runs} runs was lowest."
            ),
        )
        evicted.append(rule)
    return evicted


def review_probation(memory: MemoryStore) -> tuple[list[dict], list[dict], list[str]]:
    """Stage 5 — confirm or drop rules whose probation has elapsed.

    A dropped rule does NOT end the story for its signature. The failure is still
    real; the rule was a bad attempt at fixing it. So the signature stays open
    for a different attempt — unless enough different rules have now failed, in
    which case it escalates.

    Returns (confirmed, dropped, newly_escalated_signatures).
    """
    confirmed, dropped, escalated = [], [], []

    for rule in memory.get_active_rules():
        if rule.get("status") != "probation":
            continue
        runs = rule.get("runs_active", 0)
        if runs < MEMORY_POLICY.probation_runs:
            continue

        rate = rule.get("runs_passed", 0) / runs
        if rate > 0:
            memory.set_rule_status(
                rule["id"], "active",
                reason=f"Confirmed: {rate:.0%} pass rate over {runs} runs.",
            )
            confirmed.append(rule)
            continue

        memory.set_rule_status(
            rule["id"], "superseded",
            reason=(
                f"Dropped after probation: 0 passing runs out of {runs} while "
                f"active, so it did not improve quality."
            ),
        )
        dropped.append(rule)

        signature = rule.get("signature")
        if signature and memory.is_escalated(signature):
            memory.escalate(
                signature,
                reason=(
                    f"{MEMORY_POLICY.max_fix_attempts_per_signature} different "
                    f"rules were tried for this failure and none improved "
                    f"quality. It is unlikely to be fixable by prompting — the "
                    f"gate may be miscalibrated, or the generator model may be "
                    f"the limiting factor. Needs a human decision."
                ),
            )
            escalated.append(signature)

    return confirmed, dropped, escalated


def consolidate(memory: MemoryStore, run_id: str) -> dict:
    """Run the full consolidation pass. Returns a report for the audit trail."""
    report = {
        "promoted": [], "merged": [], "rejected": [],
        "evicted": [], "confirmed": [], "dropped": [], "escalated": [],
    }

    confirmed, dropped, escalated = review_probation(memory)
    report["confirmed"] = [r["id"] for r in confirmed]
    report["dropped"] = [r["id"] for r in dropped]
    report["escalated"] = escalated

    observations = memory.get_observations()
    for signature, topics in find_promotable_patterns(memory):
        examples = [
            o["reason"] for o in observations if o.get("signature") == signature
        ][-5:]
        failed_before = memory.failed_fix_attempts(signature)

        candidate = distil_rule(signature, examples, failed_before)
        if not candidate:
            report["rejected"].append(
                {"signature": signature, "why": "already covered by a mechanical gate"}
            )
            continue

        verdict = reconcile(candidate, memory.get_active_rules())
        if verdict.relation == "independent":
            rule_id = memory.add_rule(
                insight=candidate, signature=signature,
                source_run_id=run_id, evidence_runs=topics,
            )
            report["promoted"].append({
                "id": rule_id, "insight": candidate, "topics": topics,
            })
        else:
            merged = verdict.merged_insight or candidate
            rule_id = memory.add_rule(
                insight=merged, signature=signature,
                source_run_id=run_id, evidence_runs=topics,
                supersedes=verdict.related_rule_ids,
            )
            report["merged"].append({
                "id": rule_id, "relation": verdict.relation,
                "replaced": verdict.related_rule_ids,
                "insight": merged, "why": verdict.reasoning,
            })

    report["evicted"] = [r["id"] for r in enforce_capacity(memory)]
    return report
