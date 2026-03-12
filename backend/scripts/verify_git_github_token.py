from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error as urlerror
from urllib import request as urlrequest


DEFAULT_API_BASE_URL = "https://api.github.com"
DEFAULT_REPO = "wlodzimierrr/homelab-workloads"
TOKEN_ENV_CANDIDATES = (
    "GIT_GITHUB_TOKEN",
    "GITHUB_API_TOKEN",
    "HOMELAB_WORKLOADS_REPO_TOKEN",
    "GITHUB_TOKEN",
)


def _resolve_token(explicit_env: str | None) -> tuple[str, str]:
    names = (explicit_env,) if explicit_env else TOKEN_ENV_CANDIDATES
    for name in names:
        if not name:
            continue
        token = os.getenv(name, "").strip()
        if token:
            return name, token
    checked = ", ".join(name for name in names if name)
    raise SystemExit(f"GitHub token is not configured. Checked: {checked}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that the configured GitHub token can read the target workloads repository."
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo slug to validate.")
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("GITHUB_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/"),
        help="GitHub API base URL.",
    )
    parser.add_argument(
        "--token-env",
        default="GIT_GITHUB_TOKEN",
        help="Primary environment variable name to read before fallback names.",
    )
    args = parser.parse_args()

    token_env, token = _resolve_token(args.token_env)
    request = urlrequest.Request(
        f"{args.api_base_url}/repos/{args.repo}",
        method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "homelab-portal-git-token-check",
        },
    )

    try:
        with urlrequest.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        message = body or exc.reason or "GitHub repo probe failed"
        print(
            json.dumps(
                {
                    "ok": False,
                    "repo": args.repo,
                    "tokenEnv": token_env,
                    "statusCode": exc.code,
                    "error": message,
                }
            )
        )
        return 1
    except urlerror.URLError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "repo": args.repo,
                    "tokenEnv": token_env,
                    "statusCode": None,
                    "error": str(exc.reason),
                }
            )
        )
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "repo": payload.get("full_name"),
                "private": payload.get("private"),
                "defaultBranch": payload.get("default_branch"),
                "visibility": payload.get("visibility"),
                "tokenEnv": token_env,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
