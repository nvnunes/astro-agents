"""Transient structured diagnostics for one validation invocation."""

from __future__ import annotations

import copy
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

REUSE_MISS_REASONS = (
    "subject_not_found",
    "rule_dependency_changed",
    "candidate_or_allowed_answer_changed",
    "relevant_input_content_changed",
    "source_locator_changed",
    "incomplete_legacy_input_dependencies",
    "conflicting_compatible_answers",
)


def review_item_counts(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count review items by their current semantic-review kind."""

    counts = Counter(str(item.get("kind", "unknown")) for item in items)
    return dict(sorted(counts.items()))


@dataclass
class ValidationDiagnostics:
    """Collect noncanonical review measurements for one public invocation."""

    started: float = field(default_factory=time.monotonic)
    lifecycle: list[dict[str, Any]] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    accepted_pages: list[dict[str, Any]] = field(default_factory=list)
    reuse_questions_considered: int = 0
    reuse_answers_found: int = 0
    reuse_items_removed: int = 0
    reuse_misses: Counter[str] = field(default_factory=Counter)

    def record_queue(self, stage: str, items: Sequence[Mapping[str, Any]]) -> None:
        """Record one lifecycle queue snapshot without retaining item data."""

        self.lifecycle.append(
            {
                "stage": stage,
                "item_count": len(items),
                "items_by_kind": review_item_counts(items),
            }
        )

    def record_reuse(
        self,
        metrics: Mapping[str, Any],
        *,
        items_before: int,
        items_after: int,
    ) -> None:
        """Merge one reusable-judgment pass into this invocation summary."""

        self.reuse_questions_considered += int(metrics.get("questions_considered", 0))
        self.reuse_answers_found += int(metrics.get("answers_found", 0))
        self.reuse_items_removed += max(0, items_before - items_after)
        misses = metrics.get("misses_by_reason", {})
        if isinstance(misses, Mapping):
            for reason, count in misses.items():
                if reason in REUSE_MISS_REASONS:
                    self.reuse_misses[str(reason)] += int(count)

    def record_page(self, result: Mapping[str, Any]) -> None:
        """Record a newly returned or resumed public packet page."""

        page = result.get("page_diagnostics")
        if isinstance(page, Mapping):
            self.pages.append(copy.deepcopy(dict(page)))

    def record_page_acceptance(self, result: Mapping[str, Any]) -> None:
        """Record accepted-page size and reviewer wait time when available."""

        accepted = result.get("accepted_page_diagnostics")
        if isinstance(accepted, Mapping):
            self.accepted_pages.append(copy.deepcopy(dict(accepted)))

    def as_dict(self) -> dict[str, Any]:
        """Return the compact structured result for this invocation."""

        wait_seconds = sum(
            float(page.get("review_wait_seconds", 0.0)) for page in self.accepted_pages
        )
        return {
            "execution_seconds": round(time.monotonic() - self.started, 3),
            "review_wait_seconds": round(wait_seconds, 3),
            "lifecycle": copy.deepcopy(self.lifecycle),
            "reuse": {
                "questions_considered": self.reuse_questions_considered,
                "answers_found": self.reuse_answers_found,
                "items_removed": self.reuse_items_removed,
                "misses_by_reason": {
                    reason: int(self.reuse_misses.get(reason, 0))
                    for reason in REUSE_MISS_REASONS
                },
            },
            "pages": copy.deepcopy(self.pages),
            "accepted_pages": copy.deepcopy(self.accepted_pages),
        }
