from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.github.projects import ProjectSyncError, sync_pipeline_project
from app.security.secrets import redact_secrets

logger = logging.getLogger(__name__)

DEVBOT_LABEL_PREFIX = "devbot:"


class GitHubClient:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._token = settings.github_token
        self._api = settings.github_api_url.rstrip("/")
        self._assignee = settings.github_assignee
        self._login: str | None = None
        self._client = client or httpx.Client(timeout=30.0)

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _require_token(self) -> None:
        if not self._token:
            raise RuntimeError("GITHUB_TOKEN is not configured")

    def _request(self, method: str, path: str, *, allow_statuses: set[int] | None = None, **kwargs: Any) -> httpx.Response:
        self._require_token()
        response = self._client.request(method, f"{self._api}{path}", headers=self._headers(), **kwargs)
        allowed = allow_statuses or set()
        if response.status_code >= 400 and response.status_code not in allowed:
            logger.error(
                "github_request_failed",
                extra={"status": response.status_code, "path": path, "body": redact_secrets(response.text)[:500]},
            )
            response.raise_for_status()
        return response

    def create_pull_request(
        self,
        repository: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = False,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/repos/{repository}/pulls",
            json={"title": title, "body": body, "head": head, "base": base, "draft": draft},
        )
        return response.json()

    def update_pull_request(self, repository: str, number: int, *, body: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if body is not None:
            payload["body"] = body
        if not payload:
            return {}
        return self._request("PATCH", f"/repos/{repository}/pulls/{number}", json=payload).json()

    def create_issue(self, repository: str, *, title: str, body: str, labels: list[str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return self._request("POST", f"/repos/{repository}/issues", json=payload).json()

    def get_issue(self, repository: str, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{repository}/issues/{number}").json()

    def comment_on_issue(self, repository: str, number: int, body: str) -> dict[str, Any]:
        return self._request("POST", f"/repos/{repository}/issues/{number}/comments", json={"body": body}).json()

    def list_issue_comments(self, repository: str, number: int) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        for page in range(1, 21):
            batch = self._request(
                "GET",
                f"/repos/{repository}/issues/{number}/comments",
                params={"per_page": 100, "page": page},
            ).json()
            if not isinstance(batch, list) or not batch:
                break
            comments.extend(batch)
            if len(batch) < 100:
                break
        return comments

    def set_pipeline_label(self, repository: str, number: int, phase: str) -> dict[str, Any]:
        issue = self.get_issue(repository, number)
        current = [item["name"] if isinstance(item, dict) else str(item) for item in issue.get("labels") or []]
        kept = [name for name in current if not name.startswith(DEVBOT_LABEL_PREFIX)]
        kept.append(f"{DEVBOT_LABEL_PREFIX}{phase}")
        return self._request("PATCH", f"/repos/{repository}/issues/{number}", json={"labels": kept}).json()

    def viewer_login(self) -> str:
        if self._login is not None:
            return self._login
        data = self._request("GET", "/user").json()
        self._login = str(data.get("login") or "")
        return self._login

    def assign_issue(self, repository: str, number: int, username: str | None = None) -> dict[str, Any]:
        assignee = username or self._assignee
        if not assignee:
            try:
                assignee = self.viewer_login()
            except Exception:
                return {}
        if not assignee:
            return {}
        return self._request("PATCH", f"/repos/{repository}/issues/{number}", json={"assignees": [assignee]}).json()

    def close_issue(self, repository: str, number: int) -> dict[str, Any]:
        response = self._request(
            "PATCH",
            f"/repos/{repository}/issues/{number}",
            json={"state": "closed"},
            allow_statuses={404},
        )
        return response.json() if response.status_code < 400 else {}

    def close_pull_request(self, repository: str, number: int) -> dict[str, Any]:
        response = self._request(
            "PATCH",
            f"/repos/{repository}/pulls/{number}",
            json={"state": "closed"},
            allow_statuses={404},
        )
        return response.json() if response.status_code < 400 else {}

    def delete_branch(self, repository: str, branch: str) -> None:
        ref = branch.removeprefix("refs/heads/")
        self._request("DELETE", f"/repos/{repository}/git/refs/heads/{ref}", allow_statuses={404, 422})

    def clone_url(self, repository: str) -> str:
        return f"https://github.com/{repository}.git"

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_token()
        response = self._client.post(
            f"{self._api}/graphql",
            headers=self._headers(),
            json={"query": query, "variables": variables or {}},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProjectSyncError("GitHub GraphQL returned non-JSON") from exc
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if response.status_code >= 400 or errors:
            text = redact_secrets(response.text)[:500]
            lowered = text.lower()
            if response.status_code in {401, 403} or "scope" in lowered or "resource not accessible" in lowered:
                raise ProjectSyncError("GitHub token is missing the project scope")
            raise ProjectSyncError(text or f"GitHub GraphQL failed ({response.status_code})")
        data = payload.get("data") if isinstance(payload, dict) else None
        return data if isinstance(data, dict) else {}

    def sync_pipeline_project(
        self,
        *,
        issue_node_id: str,
        status: str,
        pinned_project_id: str = "",
        stored_project_id: str = "",
        stored_item_id: str = "",
        owner: str = "",
    ) -> dict[str, str]:
        return sync_pipeline_project(
            self.graphql,
            issue_node_id=issue_node_id,
            status=status,
            pinned_project_id=pinned_project_id,
            stored_project_id=stored_project_id,
            stored_item_id=stored_item_id,
            owner=owner,
        )
