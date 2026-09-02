"""Shared typed failures for mechanical research-log contracts."""

from __future__ import annotations


class MechanicalContractError(ValueError):
    """One machine-readable mechanical contract failure.

    Concrete validator domains subclass this type so integration boundaries can
    distinguish authored contract failures from programming and operational
    exceptions without relying on duck typing.
    """

    def __init__(
        self,
        code: str,
        subject: str,
        observed: object,
        rule: str,
        *,
        outcome: str = "fail",
    ):
        super().__init__(f"{code}: {subject}: {observed}")
        self.code = code
        self.subject = subject
        self.observed = observed
        self.rule = rule
        self.outcome = outcome
