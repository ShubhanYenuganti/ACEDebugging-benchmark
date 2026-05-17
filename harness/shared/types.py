"""Shared dataclasses for results that cross module boundaries.

Replacing dicts with these types catches missing/added fields at runtime
rather than via the silent None-then-mystery debugging pattern.
"""
from dataclasses import dataclass, field
from typing import Literal

DeploymentOutcome = Literal[
    "deploy_success",
    "no_changes",
    "lint_fail",
    "deploy_fail",
    "error",
    "unknown",
]


@dataclass
class LambdaUpload:
    """One Lambda file packaged for a submission."""
    rel_path: str            # e.g. "lambda/handler.py"
    stem: str                # e.g. "handler"
    s3_key_original: str     # e.g. "handler.zip"
    s3_key_new: str          # e.g. "lambdas/<run>/<sha>/handler.zip"
    sha256: str
    arcname: str             # e.g. "index.py" (derived from Handler)


@dataclass
class PackagingPlan:
    """Pre-flight plan for a submission: what to upload, what to skip."""
    uploads: list[LambdaUpload] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    template_changed: bool = False

    @property
    def has_packaging_work(self) -> bool:
        return bool(self.uploads) or self.template_changed

    @property
    def has_orphans(self) -> bool:
        return bool(self.orphans)


@dataclass
class CfnEvent:
    logical_id: str | None
    status: str | None
    reason: str | None


@dataclass
class DeploymentResult:
    """Result of one call to handle_submission(). Always returned regardless
    of outcome; fields not relevant to the branch stay at their default."""
    outcome: DeploymentOutcome
    error: str = ""
    skipped_lambda_files: list[str] = field(default_factory=list)
    packaged_files: list[str] = field(default_factory=list)
    lint_errors: list = field(default_factory=list)
    cfn_events: list[CfnEvent] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.outcome == "deploy_success"


@dataclass
class AssertionResult:
    """One ASSERT line from functional_test.py."""
    name: str
    verdict: Literal["pass", "fail"]
    message: str = ""

    @property
    def is_secondary(self) -> bool:
        return "_secondary" in self.name


@dataclass
class AssertionRunResult:
    """Full result of running functional_test.py once."""
    assertions: list[AssertionResult] = field(default_factory=list)
    returncode: int = 0
    crash_reason: str = ""

    @property
    def primary_failed_names(self) -> list[str]:
        return [a.name for a in self.assertions if a.verdict == "fail" and not a.is_secondary]

    @property
    def all_failed_names(self) -> list[str]:
        return [a.name for a in self.assertions if a.verdict == "fail"]

    @property
    def passed(self) -> list[AssertionResult]:
        return [a for a in self.assertions if a.verdict == "pass"]

    @property
    def failed(self) -> list[AssertionResult]:
        return [a for a in self.assertions if a.verdict == "fail"]

    @property
    def primary_assertions_passed(self) -> bool:
        return len(self.primary_failed_names) == 0 and not self.crash_reason

    @property
    def all_assertions_passed(self) -> bool:
        return len(self.all_failed_names) == 0 and not self.crash_reason

    @property
    def assertions_by_name(self) -> dict[str, AssertionResult]:
        return {a.name: a for a in self.assertions}
