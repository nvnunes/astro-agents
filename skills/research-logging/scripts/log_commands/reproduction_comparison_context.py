"""Bounded first-difference context retained during the existing comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import islice


def _preview(value: object, depth: int = 0) -> object:
    """Summarize already-loaded values without traversing large containers."""

    if isinstance(value, str):
        if len(value) <= 1024:
            return value
        return {"prefix": value[:1024], "characters": len(value), "truncated": True}
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        # Exact comparators intentionally distinguish non-finite/signed-zero
        # values. Their diagnostic spelling must remain finite JSON.
        return repr(value)
    if isinstance(value, (list, tuple, dict)):
        result: dict[str, object] = {"type": type(value).__name__, "count": len(value)}
        if depth < 2:
            if isinstance(value, dict):
                result["items"] = {
                    str(key)[:256]: _preview(item, depth + 1)
                    for key, item in islice(value.items(), 4)
                }
            else:
                result["items"] = [_preview(item, depth + 1) for item in value[:4]]
        result["truncated"] = bool(len(value) > 4 or depth >= 2)
        return result
    return {"type": type(value).__name__}


@dataclass
class ComparisonContext:
    """One comparison's first observed difference, never its match authority."""

    observed: dict[str, object] = field(default_factory=dict)

    def difference(self, location: str, expected: object, regenerated: object) -> None:
        """Keep the first mismatch while comparison continues its original checks."""

        if "difference" not in self.observed:
            self.observed["difference"] = {
                "location": location[:1024],
                "expected": _preview(expected),
                "regenerated": _preview(regenerated),
            }

    def error(self, error: BaseException) -> None:
        """Retain the actual caught exception rather than only its category."""

        message = str(error)
        self.observed["error"] = {
            "type": type(error).__name__,
            "message": message[:2048],
            "truncated": len(message) > 2048,
        }
