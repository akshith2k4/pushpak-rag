"""Answer generation with citations — Phase 5 (not yet implemented).

Strict prompt (SPEC §1): answer ONLY from provided context, verbatim refusal
"Not specified in the provided documents." when absent, always cite
document name + section + page, calculations via rules tools.
Output: {"answer": ..., "sources": [{"doc","section","page"}]}.
"""
