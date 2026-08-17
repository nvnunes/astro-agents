"""Current rule families for durable semantic judgments."""

from __future__ import annotations

from typing import Any, Mapping

SEMANTIC_REVIEW_RULES = {"semantic_review": 1}
SUBTREE_RULE_DEPENDENCIES = {"semantic_review": 1, "orphan_subtree": 1}
TERMINAL_CLEANUP_VERSION = 1
TERMINAL_CLEANUP_CACHE_KEY = "terminal_judgment_cleanup"


def compatible(judgment: Mapping[str, Any]) -> bool:
    """Return whether one judgment declares a current rule family."""

    dependencies = judgment.get("rule_dependencies")
    if not isinstance(dependencies, Mapping):
        return False
    if judgment.get("kind") == "review-decision":
        return dict(dependencies) in (
            SEMANTIC_REVIEW_RULES,
            SUBTREE_RULE_DEPENDENCIES,
        )
    from .compatibility import components_compatible

    return components_compatible(dependencies)[0]
