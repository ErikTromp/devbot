from __future__ import annotations

import re
from dataclasses import dataclass

_SAFE_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")


@dataclass(frozen=True)
class RepoEntry:
    repository: str
    alias: str


@dataclass(frozen=True)
class RepoCatalog:
    entries: tuple[RepoEntry, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.entries

    def resolve(self, token: str | None, default_org: str = "") -> str | None:
        raw = _strip_repo_prefix(token)
        if not raw:
            return None
        key = raw.lower()
        for entry in self.entries:
            if key in {entry.alias.lower(), entry.repository.lower(), entry.repository.split("/")[-1].lower()}:
                return entry.repository
        if not self.empty and "/" not in raw:
            return None
        return normalize_repo(raw, default_org)

    def allows(self, repository: str) -> bool:
        if self.empty:
            return True
        key = repository.lower()
        return any(
            key in {entry.repository.lower(), entry.alias.lower(), entry.repository.split("/")[-1].lower()}
            for entry in self.entries
        )


def _strip_repo_prefix(value: str | None) -> str:
    text = (value or "").strip().strip("/")
    if text.lower().startswith("repo="):
        text = text[5:].strip().strip("/")
    return text


def normalize_repo(value: str | None, default_org: str) -> str | None:
    repo = _strip_repo_prefix(value)
    if not repo or ".." in repo or not _SAFE_REPO_RE.match(repo):
        return None
    if any(part in {".", "..", ""} for part in repo.split("/")):
        return None
    if "/" not in repo:
        if not default_org:
            return None
        repo = f"{default_org}/{repo}"
    return repo


def parse_allowed_repos(raw: str, default_org: str = "") -> RepoCatalog:
    entries: list[RepoEntry] = []
    seen_alias: set[str] = set()
    for item in (raw or "").split(","):
        item = item.strip().strip('"').strip("'")
        if not item:
            continue
        if ";" in item:
            left, alias = (part.strip() for part in item.split(";", 1))
        else:
            left, alias = item, ""
        if "/" in left:
            repository = normalize_repo(left, default_org)
        elif alias:
            repository = normalize_repo(f"{left}/{alias}", default_org)
        else:
            repository = normalize_repo(left, default_org)
        if not repository:
            continue
        alias = alias or repository.split("/")[-1]
        key = alias.lower()
        if key in seen_alias:
            continue
        seen_alias.add(key)
        entries.append(RepoEntry(repository=repository, alias=alias))
    return RepoCatalog(tuple(entries))
