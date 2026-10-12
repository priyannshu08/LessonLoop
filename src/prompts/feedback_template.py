"""
Feedback compilation templates.

These templates transform structured evaluation results into
actionable, targeted feedback for the Generator to use during retry.
"""

FEEDBACK_COMPILATION_TEMPLATE = """## Evaluation Results for Attempt {attempt_number}

### ❌ FAILED Checkpoints (MUST FIX):
{failed_details}

### ✅ PASSED Checkpoints (preserve these):
{passed_details}

### Evaluator's Overall Assessment:
{summary}

### Specific Instructions for Retry:
{retry_instructions}
"""

FAILED_CHECKPOINT_DETAIL = """#### {index}. {name} — ❌ FAILED
**Reason:** {reason}
**Evidence:** "{evidence}"
**Fix:** {fix_instruction}
"""

PASSED_CHECKPOINT_DETAIL = """- ✅ **{name}**: {reason}
"""

RETRY_INSTRUCTIONS_TEMPLATE = """Based on the failures above, here are your priority fixes:
{numbered_fixes}

IMPORTANT: Do NOT discard the parts of the lesson that passed evaluation. Only fix the specific issues listed above."""
