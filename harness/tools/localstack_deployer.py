#!/usr/bin/env python3
# localstack-deployer CLI for ACE-Bench harness
# NOT on PyPI; install with:  pip install -e .  from project root.
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path


def _cf_client():
    import boto3

    ep = os.environ.get("LOCALSTACK_ENDPOINT", "http://localhost:4566")
    return boto3.client(
        "cloudformation",
        endpoint_url=ep,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def _stack_status(cf, name):
    try:
        return cf.describe_stacks(StackName=name)["Stacks"][0]["StackStatus"]
    except Exception as e:
        if "does not exist" in str(e):
            return None
        raise


def _wait(cf, name, target, in_prog, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            st = _stack_status(cf, name)
        except Exception as e:
            if target == "DELETE_COMPLETE":
                return "DELETE_COMPLETE"
            _die(str(e))
        if st is None:
            return "DELETE_COMPLETE" if target == "DELETE_COMPLETE" else "GONE"
        print(f"  {name}: {st}", file=sys.stderr, flush=True)
        if st == target:
            return st
        if st not in (in_prog, "REVIEW_IN_PROGRESS"):
            return st
        time.sleep(3)
    return "TIMEOUT"


def _die(msg):
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def cmd_create_stack(args):
    cf = _cf_client()
    name, tpath = args.stack_name, Path(args.template)
    if not tpath.exists():
        _die(f"Template not found: {tpath}")
    body = tpath.read_text(encoding="utf-8")
    st = _stack_status(cf, name)
    if st is not None:
        if st.endswith("_IN_PROGRESS"):
            _die(f"Stack {name!r} busy ({st}) — wait and retry.")
        print(
            f"Deleting existing stack {name!r} ({st})...", file=sys.stderr, flush=True
        )
        cf.delete_stack(StackName=name)
        _wait(cf, name, "DELETE_COMPLETE", "DELETE_IN_PROGRESS")
    print(f"Creating stack {name!r} from {tpath}...", file=sys.stderr, flush=True)
    cf.create_stack(
        StackName=name,
        TemplateBody=body,
        Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
    )
    final = _wait(cf, name, "CREATE_COMPLETE", "CREATE_IN_PROGRESS")
    if final != "CREATE_COMPLETE":
        _die(f"Stack creation ended with status: {final}")
    print(f"Stack {name!r} created (CREATE_COMPLETE).", file=sys.stderr, flush=True)


def cmd_update_stack(args):
    name = args.stack_name
    sig = Path(os.environ.get("ACE_BENCH_SIGNAL_FILE", "/tmp/ace-bench-update.json"))
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    payload = {
        "event": "update_stack",
        "stack_name": name,
        "timestamp": time.time(),
        "iso_timestamp": ts,
    }
    tmp = sig.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.rename(sig)
    # Structured stderr line consumed by ScenarioRunner stderr-tail thread.
    print(
        json.dumps(
            {
                "tool": "localstack_deployer_update_stack",
                "stack_name": name,
                "timestamp": ts,
            }
        ),
        file=sys.stderr,
        flush=True,
    )
    print(
        f"Redeployment signal written for stack {name!r}. The harness will apply your fix.",
        flush=True,
    )


def main():
    p = argparse.ArgumentParser(
        prog="localstack-deployer",
        description="CloudFormation deployer for LocalStack (ACE-Bench harness).",
    )
    p.add_argument("--version", action="version", version="localstack-deployer 0.1.0")
    sub = p.add_subparsers(dest="command", required=True)
    pc = sub.add_parser("create-stack", help="Deploy a new stack from a template.")
    pc.add_argument("--stack-name", required=True, metavar="NAME")
    pc.add_argument("--template", required=True, metavar="FILE")
    pu = sub.add_parser("update-stack", help="Signal harness to submit model fix.")
    pu.add_argument("--stack-name", required=True, metavar="NAME")
    args = p.parse_args()
    {"create-stack": cmd_create_stack, "update-stack": cmd_update_stack}[args.command](
        args
    )


if __name__ == "__main__":
    main()
