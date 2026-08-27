"""Reviewer: the only agent with access to the verified solution.

Scores the fix by functional equivalence: applies both the candidate
patch and the verified fix against LocalStack (via healer.localstack) and
compares resulting infra state, not the code diff. This is the only
function in the codebase that triggers eval/ access (through
localstack.diff_state) — and it never returns that raw diff. Everything
it writes to the thread goes through the fixed ReviewFeedback schema,
which carries no verified-fix values, just which resource still differs
and how many attributes are off.
"""
from healer import localstack
from healer.models import Patch, ReviewFeedback


def review(bug_id: str, patch: Patch) -> ReviewFeedback:
    mismatches = localstack.diff_state(bug_id, patch)

    if not mismatches:
        return ReviewFeedback(passed=True, resource=None, symptom=None, attempt_delta=None)

    first_key = next(iter(mismatches))
    resource = first_key.rsplit(".", 1)[0]
    return ReviewFeedback(
        passed=False,
        resource=resource,
        symptom="resulting configuration does not match the verified fix's resulting state",
        attempt_delta=f"{len(mismatches)} attribute(s) still differ from the verified fix",
    )
