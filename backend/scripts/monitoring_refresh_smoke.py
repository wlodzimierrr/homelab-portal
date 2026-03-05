#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import time
from urllib import error as urlerror
from urllib import request as urlrequest


def timed_get(url: str, token: str) -> tuple[int, float, int]:
    req = urlrequest.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    started = time.perf_counter()
    with urlrequest.urlopen(req, timeout=10) as response:
        body = response.read()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return response.status, elapsed_ms, len(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitoring endpoint repeated-refresh smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--service-id", default="homelab-api", help="Service id to query")
    parser.add_argument("--iterations", type=int, default=20, help="How many refresh rounds to execute")
    parser.add_argument("--token", default="dev-static-token", help="Bearer token")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    targets = [
        f"{base}/services/{args.service_id}/metrics/summary?range=24h",
        f"{base}/services/{args.service_id}/health/timeline?range=24h&step=5m",
        f"{base}/services/{args.service_id}/logs/quickview?preset=errors&range=1h&limit=50",
        f"{base}/alerts/active?serviceId={args.service_id}&limit=50",
    ]

    latencies_ms: list[float] = []
    failures: list[dict[str, str]] = []
    for i in range(args.iterations):
        for target in targets:
            try:
                status_code, elapsed_ms, size = timed_get(target, args.token)
                latencies_ms.append(elapsed_ms)
                if status_code != 200:
                    failures.append({"url": target, "error": f"status={status_code}"})
                print(
                    json.dumps(
                        {
                            "iteration": i + 1,
                            "url": target,
                            "status": status_code,
                            "elapsedMs": round(elapsed_ms, 2),
                            "bytes": size,
                        }
                    )
                )
            except urlerror.HTTPError as exc:
                failures.append({"url": target, "error": f"http={exc.code}"})
            except Exception as exc:  # noqa: BLE001
                failures.append({"url": target, "error": str(exc)})

    if latencies_ms:
        print(
            json.dumps(
                {
                    "summary": {
                        "count": len(latencies_ms),
                        "p50Ms": round(statistics.median(latencies_ms), 2),
                        "p95Ms": round(sorted(latencies_ms)[int(len(latencies_ms) * 0.95) - 1], 2),
                        "maxMs": round(max(latencies_ms), 2),
                        "failures": len(failures),
                    }
                }
            )
        )

    if failures:
        print(json.dumps({"failures": failures}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
