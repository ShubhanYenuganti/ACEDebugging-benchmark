"""Composable verification pipeline.

A VerifyStep is a callable that consumes a VerifyContext and produces a
StepResult. run_pipeline() runs each step in order, threading prior results
through the context. Post-processors run after all steps and may rewrite
their results (e.g. the pass4-fail-downgrades-pass3 rule).
"""
from dataclasses import dataclass, field
from typing import Callable, Protocol

from harness.shared.types import AssertionRunResult


@dataclass
class VerifyContext:
    scenario_dir: str
    run_id: str
    manifest_path: str | None
    corpus_dir: str
    api_endpoint: str
    fault_class: str | None = None
    # Filled in as steps run:
    pass1_result: AssertionRunResult | None = None
    results: dict = field(default_factory=dict)


class VerifyStep(Protocol):
    name: str
    def should_run(self, ctx: VerifyContext) -> bool: ...
    def run(self, ctx: VerifyContext): ...


def run_pipeline(
    ctx: VerifyContext,
    steps: list,
    postprocessors: list[Callable[[VerifyContext], None]],
) -> dict:
    """Execute steps in order, then run post-processors. Returns ctx.results."""
    for step in steps:
        if not step.should_run(ctx):
            ctx.results[step.name] = None
            continue
        ctx.results[step.name] = step.run(ctx)
    for pp in postprocessors:
        pp(ctx)
    return ctx.results


def downgrade_pass3_when_pass4_fails(ctx: VerifyContext) -> None:
    """If pass4 ran and failed, pass3's classification drops to 'partial'
    even if pass1's primary assertions passed."""
    pass4 = ctx.results.get("pass4_concurrency")
    pass3 = ctx.results.get("pass3_classification")
    if not pass4 or pass4.get("skipped") or pass3 is None:
        return
    if pass4.get("passed"):
        return
    if ctx.pass1_result and ctx.pass1_result.primary_assertions_passed:
        pass3 = dict(pass3)
        pass3["classification"] = "partial"
        pass3["root_cause_addressed"] = False
        ctx.results["pass3_classification"] = pass3
