import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from harness.shared.localstack_client import sqs_client

try:
    from harness.shared.localstack_client import sns_client
except ImportError:  # pragma: no cover - compatibility for older client module
    sns_client = None


PASS4_SKIP_RESULT = {"skipped": True, "reason": "no_concurrency_probe"}


class Pass4Step:
    name = "pass4_concurrency"

    def should_run(self, ctx) -> bool:
        return ctx.fault_class in {"performance", "reliability"}

    def run(self, ctx):
        return run_pass4(ctx.scenario_dir, ctx.manifest_path, ctx.api_endpoint)


def run_pass4(scenario_dir: str, manifest_path: str, api_endpoint: str) -> dict:
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    if not api_endpoint:
        probe = manifest.get("concurrency_probe") or {}
        if not probe:
            return {"skipped": True, "reason": "no_concurrency_probe"}
        return _run_event_probe(probe, manifest.get("concurrency_probe_n", 10))

    n = manifest.get("concurrency_probe_n", 10)
    counts = {"success": 0, "throttled": 0, "timeout": 0, "error": 0}

    def send(_):
        try:
            resp = requests.post(api_endpoint, json={}, timeout=10)
            if resp.status_code == 200:
                return "success"
            elif resp.status_code == 429:
                return "throttled"
            elif resp.status_code == 504:
                return "timeout"
            else:
                return "error"
        except Exception:
            return "error"

    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(send, i) for i in range(n)]
        for future in as_completed(futures):
            counts[future.result()] += 1

    return {
        "requests_sent": n,
        "success_count": counts["success"],
        "throttled_count": counts["throttled"],
        "timeout_count": counts["timeout"],
        "error_count": counts["error"],
        "passed": counts["throttled"] == 0 and counts["timeout"] == 0,
    }


def _run_event_probe(probe: dict, n: int) -> dict:
    probe_type = (probe.get("type") or "").lower()
    counts = {"success": 0, "throttled": 0, "timeout": 0, "error": 0}

    def send_sqs(_):
        try:
            sqs_client.send_message(
                QueueUrl=probe["queue_url"],
                MessageBody=probe.get("message_body", "{}"),
            )
            return "success"
        except Exception:
            return "error"

    def send_sns(_):
        try:
            if sns_client is None:
                return "error"
            sns_client.publish(
                TopicArn=probe["topic_arn"],
                Message=probe.get("message", "{}"),
            )
            return "success"
        except Exception:
            return "error"

    if probe_type == "sqs" and probe.get("queue_url"):
        sender = send_sqs
    elif probe_type == "sns" and probe.get("topic_arn"):
        sender = send_sns
    else:
        return {"skipped": True, "reason": "invalid_concurrency_probe"}

    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(sender, i) for i in range(n)]
        for future in as_completed(futures):
            counts[future.result()] += 1

    backlog = None
    if probe_type == "sqs":
        try:
            attrs = sqs_client.get_queue_attributes(
                QueueUrl=probe["queue_url"],
                AttributeNames=["ApproximateNumberOfMessages"],
            )
            backlog = int(attrs.get("Attributes", {}).get("ApproximateNumberOfMessages", "0"))
        except Exception:
            backlog = None

    passed = counts["error"] == 0
    if backlog is not None:
        passed = passed and backlog < n

    return {
        "probe_type": probe_type,
        "requests_sent": n,
        "success_count": counts["success"],
        "throttled_count": counts["throttled"],
        "timeout_count": counts["timeout"],
        "error_count": counts["error"],
        "backlog_count": backlog,
        "passed": passed,
    }
