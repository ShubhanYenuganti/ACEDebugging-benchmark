import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


def run_pass4(scenario_dir: str, manifest_path: str, api_endpoint: str) -> dict:
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

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

