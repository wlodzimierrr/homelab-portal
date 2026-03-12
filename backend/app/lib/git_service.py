from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
import difflib
import json
import logging
import os
import time
from typing import Any, Protocol, TypedDict
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest


logger = logging.getLogger("homelab.backend.git_service")

DEFAULT_GITHUB_API_BASE_URL = "https://api.github.com"
DEFAULT_GIT_PROVIDER = "github"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRY_ATTEMPTS = 2
DEFAULT_BACKOFF_SECONDS = 0.5


class GitBranchRef(TypedDict):
    branch: str
    ref: str
    sha: str
    url: str | None


class GitFileUpdateResult(TypedDict):
    path: str
    sha: str | None
    commit_sha: str | None
    diff: str


class GitPullRequest(TypedDict):
    id: int
    url: str
    number: int
    state: str


class GitCommitResult(TypedDict):
    branch: str
    commit_sha: str
    tree_sha: str
    files: list[str]


class GitProvider(Protocol):
    """Provider-agnostic contract for writing managed Git changes."""

    def read_file(self, repo: str, branch: str, file_path: str) -> str:
        """Read a tracked file from a branch and return its decoded text content."""

    def create_branch(self, repo: str, from_branch: str, new_branch: str) -> GitBranchRef:
        """Create a new branch from an existing branch tip."""

    def modify_file(self, repo: str, branch: str, file_path: str, new_content: str) -> GitFileUpdateResult:
        """Replace a single file on a branch and return the rendered diff."""

    def open_pr(
        self,
        repo: str,
        from_branch: str,
        to_branch: str,
        title: str,
        description: str,
    ) -> GitPullRequest:
        """Open a pull request and return the minimal PR identity payload."""

    def commit_to_branch(
        self,
        repo: str,
        branch: str,
        files_dict: Mapping[str, str],
        message: str,
    ) -> GitCommitResult:
        """Write multiple file updates into one commit on an existing branch."""

    def close_pr(self, repo: str, pr_id: int) -> GitPullRequest:
        """Close a pull request by number and return the minimal PR identity payload."""


# Backward-compatible alias retained while the rest of the backend still imports
# the older GitService name from T6.1.2.
GitService = GitProvider


class GitServiceError(Exception):
    """Base error for Git service operations."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GitServiceConfigurationError(GitServiceError):
    """Raised when required Git service configuration is missing."""


class GitServiceAuthError(GitServiceError):
    """Raised for authentication and authorization failures."""


class GitServiceConflictError(GitServiceError):
    """Raised when a branch, ref, or file update conflicts with repository state."""


class GitServiceRateLimitError(GitServiceError):
    """Raised when GitHub rejects a request due to rate limiting."""


class GitServiceNotFoundError(GitServiceError):
    """Raised when the requested repository object does not exist."""


class GitHubGitProvider:
    """GitHub-backed implementation of the provider contract."""

    def __init__(
        self,
        *,
        token: str,
        api_base_url: str = DEFAULT_GITHUB_API_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        dry_run: bool = False,
        sleep_func: Callable[[float], None] = time.sleep,
        urlopen_func: Callable[..., Any] = urlrequest.urlopen,
    ) -> None:
        cleaned_token = token.strip()
        if not cleaned_token:
            raise GitServiceConfigurationError("GitHub token is not configured.")
        self._token = cleaned_token
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._retry_attempts = max(0, retry_attempts)
        self._backoff_seconds = max(0.0, backoff_seconds)
        self._dry_run = dry_run
        self._sleep = sleep_func
        self._urlopen = urlopen_func

    def create_branch(self, repo: str, from_branch: str, new_branch: str) -> GitBranchRef:
        """Create a new branch ref from the tip of an existing branch."""

        if self._dry_run:
            self._log_dry_run(
                "create_branch",
                repo=repo,
                from_branch=from_branch,
                new_branch=new_branch,
            )
            return {
                "branch": new_branch,
                "ref": f"refs/heads/{new_branch}",
                "sha": "dry-run",
                "url": None,
            }

        source_ref = self._get_branch_ref(repo, from_branch)
        payload = {
            "ref": f"refs/heads/{new_branch}",
            "sha": source_ref["sha"],
        }
        response = self._request_json("POST", f"/repos/{repo}/git/refs", payload=payload)
        return self._coerce_branch_ref(response, fallback_branch=new_branch)

    def read_file(self, repo: str, branch: str, file_path: str) -> str:
        """Read a tracked file from a branch and return its decoded text content."""

        if self._dry_run:
            self._log_dry_run(
                "read_file",
                repo=repo,
                branch=branch,
                file_path=file_path,
            )
            return ""

        current_file = self._get_file(repo, branch, file_path)
        return current_file["content"]

    def modify_file(self, repo: str, branch: str, file_path: str, new_content: str) -> GitFileUpdateResult:
        """Replace one tracked file on a branch and return the unified diff."""

        if self._dry_run:
            self._log_dry_run(
                "modify_file",
                repo=repo,
                branch=branch,
                file_path=file_path,
            )
            return {
                "path": file_path,
                "sha": None,
                "commit_sha": None,
                "diff": _build_unified_diff(file_path, "", new_content),
            }

        current_file = self._get_file(repo, branch, file_path)
        diff = _build_unified_diff(file_path, current_file["content"], new_content)
        payload = {
            "message": f"chore(gitops): update {file_path}",
            "content": _encode_text_content(new_content),
            "branch": branch,
            "sha": current_file["sha"],
        }
        response = self._request_json(
            "PUT",
            f"/repos/{repo}/contents/{_quote_file_path(file_path)}",
            payload=payload,
        )
        content_payload = response.get("content") if isinstance(response, dict) else None
        commit_payload = response.get("commit") if isinstance(response, dict) else None
        sha = content_payload.get("sha") if isinstance(content_payload, dict) else current_file["sha"]
        commit_sha = commit_payload.get("sha") if isinstance(commit_payload, dict) else None
        return {
            "path": file_path,
            "sha": sha if isinstance(sha, str) else current_file["sha"],
            "commit_sha": commit_sha if isinstance(commit_sha, str) else None,
            "diff": diff,
        }

    def open_pr(
        self,
        repo: str,
        from_branch: str,
        to_branch: str,
        title: str,
        description: str,
    ) -> GitPullRequest:
        """Open a pull request and return its minimal identity payload."""

        if self._dry_run:
            self._log_dry_run(
                "open_pr",
                repo=repo,
                from_branch=from_branch,
                to_branch=to_branch,
                title=title,
            )
            return {
                "id": 0,
                "url": f"https://github.com/{repo}/pull/0",
                "number": 0,
                "state": "dry-run",
            }

        payload = {
            "title": title,
            "head": from_branch,
            "base": to_branch,
            "body": description,
        }
        response = self._request_json("POST", f"/repos/{repo}/pulls", payload=payload)
        return _coerce_pull_request(response)

    def commit_to_branch(
        self,
        repo: str,
        branch: str,
        files_dict: Mapping[str, str],
        message: str,
    ) -> GitCommitResult:
        """Create a single commit with multiple file updates and fast-forward the branch."""

        if not files_dict:
            raise GitServiceError("commit_to_branch requires at least one file update.")

        if self._dry_run:
            self._log_dry_run(
                "commit_to_branch",
                repo=repo,
                branch=branch,
                files=sorted(files_dict),
                message=message,
            )
            return {
                "branch": branch,
                "commit_sha": "dry-run",
                "tree_sha": "dry-run",
                "files": sorted(files_dict),
            }

        branch_ref = self._get_branch_ref(repo, branch)
        head_commit = self._request_json("GET", f"/repos/{repo}/git/commits/{branch_ref['sha']}")
        base_tree_sha = _extract_nested_str(head_commit, "tree", "sha")
        if not base_tree_sha:
            raise GitServiceError(f"GitHub did not return a tree SHA for branch {branch}.")

        tree_items: list[dict[str, str]] = []
        for file_path, content in files_dict.items():
            blob = self._request_json(
                "POST",
                f"/repos/{repo}/git/blobs",
                payload={
                    "content": content,
                    "encoding": "utf-8",
                },
            )
            blob_sha = _extract_str(blob, "sha")
            if not blob_sha:
                raise GitServiceError(f"GitHub did not return a blob SHA for {file_path}.")
            tree_items.append(
                {
                    "path": file_path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob_sha,
                }
            )

        tree = self._request_json(
            "POST",
            f"/repos/{repo}/git/trees",
            payload={
                "base_tree": base_tree_sha,
                "tree": tree_items,
            },
        )
        tree_sha = _extract_str(tree, "sha")
        if not tree_sha:
            raise GitServiceError("GitHub did not return a tree SHA for the new commit.")

        commit = self._request_json(
            "POST",
            f"/repos/{repo}/git/commits",
            payload={
                "message": message,
                "tree": tree_sha,
                "parents": [branch_ref["sha"]],
            },
        )
        commit_sha = _extract_str(commit, "sha")
        if not commit_sha:
            raise GitServiceError("GitHub did not return a commit SHA for the new commit.")

        self._request_json(
            "PATCH",
            f"/repos/{repo}/git/refs/heads/{_quote_branch_name(branch)}",
            payload={
                "sha": commit_sha,
                "force": False,
            },
        )
        return {
            "branch": branch,
            "commit_sha": commit_sha,
            "tree_sha": tree_sha,
            "files": sorted(files_dict),
        }

    def close_pr(self, repo: str, pr_id: int) -> GitPullRequest:
        """Close an existing pull request using its pull-request number."""

        if self._dry_run:
            self._log_dry_run(
                "close_pr",
                repo=repo,
                pr_id=pr_id,
            )
            return {
                "id": pr_id,
                "url": f"https://github.com/{repo}/pull/{pr_id}",
                "number": pr_id,
                "state": "dry-run",
            }

        response = self._request_json(
            "PATCH",
            f"/repos/{repo}/pulls/{pr_id}",
            payload={"state": "closed"},
        )
        return _coerce_pull_request(response)

    def _get_branch_ref(self, repo: str, branch: str) -> GitBranchRef:
        response = self._request_json(
            "GET",
            f"/repos/{repo}/git/ref/heads/{_quote_branch_name(branch)}",
        )
        return self._coerce_branch_ref(response, fallback_branch=branch)

    def _get_file(self, repo: str, branch: str, file_path: str) -> dict[str, str]:
        response = self._request_json(
            "GET",
            f"/repos/{repo}/contents/{_quote_file_path(file_path)}",
            query={"ref": branch},
        )
        if not isinstance(response, dict):
            raise GitServiceError(f"GitHub returned an invalid file payload for {file_path}.")
        sha = _extract_str(response, "sha")
        encoded = _extract_str(response, "content")
        encoding = _extract_str(response, "encoding") or "base64"
        if not sha or encoded is None:
            raise GitServiceError(f"GitHub did not return file contents for {file_path}.")
        if encoding != "base64":
            raise GitServiceError(f"Unsupported GitHub contents encoding {encoding!r} for {file_path}.")
        try:
            content = base64.b64decode(encoded.encode("utf-8"), validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitServiceError(f"GitHub returned undecodable file contents for {file_path}.") from exc
        return {"sha": sha, "content": content}

    def _coerce_branch_ref(self, payload: object, *, fallback_branch: str) -> GitBranchRef:
        ref = _extract_str(payload, "ref")
        sha = _extract_nested_str(payload, "object", "sha")
        url = _extract_nested_str(payload, "object", "url")
        if not ref or not sha:
            raise GitServiceError(f"GitHub returned an invalid branch ref payload for {fallback_branch}.")
        branch = ref.removeprefix("refs/heads/") or fallback_branch
        return {
            "branch": branch,
            "ref": ref,
            "sha": sha,
            "url": url,
        }

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: object | None = None,
        query: Mapping[str, str] | None = None,
    ) -> object:
        url = f"{self._api_base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urlparse.urlencode(query)}"
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "homelab-portal-backend",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        for attempt in range(self._retry_attempts + 1):
            request = urlrequest.Request(url, data=data, method=method, headers=headers)
            try:
                with self._urlopen(request, timeout=self._timeout_seconds) as response:
                    raw = response.read()
                if not raw:
                    return {}
                return json.loads(raw)
            except urlerror.HTTPError as exc:
                error_payload = _read_error_payload(exc)
                message = _extract_error_message(error_payload, fallback=exc.reason)
                mapped_error = _map_http_error(exc.code, message)
                if _is_retryable_http_error(exc.code, message) and attempt < self._retry_attempts:
                    delay = _retry_delay_seconds(exc.headers, self._backoff_seconds, attempt)
                    logger.warning(
                        "git_service_retry method=%s path=%s status_code=%s attempt=%s delay_seconds=%.2f",
                        method,
                        path,
                        exc.code,
                        attempt + 1,
                        delay,
                    )
                    self._sleep(delay)
                    continue
                raise mapped_error from exc
            except urlerror.URLError as exc:
                raise GitServiceError(f"GitHub request failed: {exc.reason}") from exc

        raise GitServiceError(f"GitHub request failed after retries for {method} {path}.")

    def _log_dry_run(self, action: str, **fields: object) -> None:
        serialized_fields = " ".join(f"{key}={value!r}" for key, value in fields.items())
        logger.info("git_service_dry_run action=%s %s", action, serialized_fields)


def build_default_git_provider(*, dry_run: bool | None = None) -> GitProvider:
    """Build the configured Git provider from environment configuration."""

    configured_dry_run = _parse_bool_env("GIT_SERVICE_DRY_RUN") if dry_run is None else dry_run
    provider = _git_provider_name()
    if provider == "github":
        return GitHubGitProvider(
            token=_github_api_token(),
            api_base_url=os.getenv("GITHUB_API_BASE_URL", DEFAULT_GITHUB_API_BASE_URL).rstrip("/"),
            timeout_seconds=_parse_float_env("GIT_SERVICE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
            retry_attempts=_parse_int_env("GIT_SERVICE_RETRY_ATTEMPTS", DEFAULT_RETRY_ATTEMPTS),
            backoff_seconds=_parse_float_env("GIT_SERVICE_BACKOFF_SECONDS", DEFAULT_BACKOFF_SECONDS),
            dry_run=configured_dry_run,
        )
    raise GitServiceConfigurationError(
        f"Unsupported GIT_PROVIDER {provider!r}. Supported providers: github."
    )


def build_default_git_service(*, dry_run: bool | None = None) -> GitProvider:
    """Backward-compatible alias for the default provider factory."""

    return build_default_git_provider(dry_run=dry_run)


def create_branch(repo: str, from_branch: str, new_branch: str) -> GitBranchRef:
    """Create a branch using the default configured Git service."""

    return build_default_git_provider().create_branch(repo, from_branch, new_branch)


def read_file(repo: str, branch: str, file_path: str) -> str:
    """Read a tracked file using the default configured Git service."""

    return build_default_git_provider().read_file(repo, branch, file_path)


def modify_file(repo: str, branch: str, file_path: str, new_content: str) -> GitFileUpdateResult:
    """Replace a tracked file using the default configured Git service."""

    return build_default_git_provider().modify_file(repo, branch, file_path, new_content)


def open_pr(repo: str, from_branch: str, to_branch: str, title: str, description: str) -> GitPullRequest:
    """Open a pull request using the default configured Git service."""

    return build_default_git_provider().open_pr(repo, from_branch, to_branch, title, description)


def commit_to_branch(repo: str, branch: str, files_dict: Mapping[str, str], message: str) -> GitCommitResult:
    """Commit multiple files using the default configured Git service."""

    return build_default_git_provider().commit_to_branch(repo, branch, files_dict, message)


def close_pr(repo: str, pr_id: int) -> GitPullRequest:
    """Close a pull request using the default configured Git service."""

    return build_default_git_provider().close_pr(repo, pr_id)


def _git_provider_name() -> str:
    raw = os.getenv("GIT_PROVIDER", DEFAULT_GIT_PROVIDER).strip().lower()
    return raw or DEFAULT_GIT_PROVIDER


def _github_api_token() -> str:
    for name in (
        "GIT_GITHUB_TOKEN",
        "GITHUB_API_TOKEN",
        "HOMELAB_WORKLOADS_REPO_TOKEN",
        "GITHUB_TOKEN",
    ):
        token = os.getenv(name, "").strip()
        if token:
            return token
    raise GitServiceConfigurationError(
        "GitHub write token is not configured. Set GIT_GITHUB_TOKEN or GITHUB_API_TOKEN."
    )


# Backward-compatible alias retained so T6.1.2 call sites and tests do not need
# to move immediately when a second provider is added later.
GitHubGitService = GitHubGitProvider


def _parse_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        logger.warning("git_service_invalid_int_env name=%s value=%r default=%s", name, value, default)
        return default


def _parse_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value.strip())
    except ValueError:
        logger.warning("git_service_invalid_float_env name=%s value=%r default=%s", name, value, default)
        return default


def _encode_text_content(content: str) -> str:
    return base64.b64encode(content.encode("utf-8")).decode("ascii")


def _build_unified_diff(file_path: str, previous_content: str, new_content: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            previous_content.splitlines(),
            new_content.splitlines(),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="",
        )
    )


def _quote_branch_name(branch: str) -> str:
    return urlparse.quote(branch.strip(), safe="/")


def _quote_file_path(file_path: str) -> str:
    normalized = file_path.strip().lstrip("/")
    return urlparse.quote(normalized, safe="/")


def _extract_str(payload: object, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _extract_nested_str(payload: object, *keys: str) -> str | None:
    current = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) else None


def _coerce_pull_request(payload: object) -> GitPullRequest:
    if not isinstance(payload, dict):
        raise GitServiceError("GitHub returned an invalid pull request payload.")
    pr_id = payload.get("id")
    number = payload.get("number")
    url = payload.get("html_url")
    state = payload.get("state")
    if not isinstance(pr_id, int) or not isinstance(number, int) or not isinstance(url, str) or not isinstance(state, str):
        raise GitServiceError("GitHub returned an invalid pull request payload.")
    return {
        "id": pr_id,
        "url": url,
        "number": number,
        "state": state,
    }


def _read_error_payload(exc: urlerror.HTTPError) -> object:
    body = exc.read().decode("utf-8", errors="replace").strip()
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def _extract_error_message(payload: object, *, fallback: object) -> str:
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return "GitHub request failed."


def _is_retryable_http_error(status_code: int, message: str) -> bool:
    lowered = message.lower()
    if status_code == 429:
        return True
    if status_code == 403 and "rate limit" in lowered:
        return True
    return 500 <= status_code <= 599


def _retry_delay_seconds(headers: Mapping[str, str] | None, backoff_seconds: float, attempt: int) -> float:
    if headers is not None:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
    return backoff_seconds * (2**attempt)


def _map_http_error(status_code: int, message: str) -> GitServiceError:
    if _is_retryable_http_error(status_code, message):
        return GitServiceRateLimitError(message, status_code=status_code)
    if status_code in {401, 403}:
        return GitServiceAuthError(message, status_code=status_code)
    if status_code in {409, 422}:
        return GitServiceConflictError(message, status_code=status_code)
    if status_code == 404:
        return GitServiceNotFoundError(message, status_code=status_code)
    return GitServiceError(message, status_code=status_code)
