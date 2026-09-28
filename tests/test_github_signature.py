from __future__ import annotations

import pytest

from app.github.signatures import GitHubSignatureError, github_signature, verify_github_signature


def test_valid_github_signature() -> None:
    body = b'{"action":"opened"}'
    provided = github_signature("gh-secret", body)
    verify_github_signature(secret="gh-secret", body=body, provided=provided)


def test_rejects_bad_github_signature() -> None:
    with pytest.raises(GitHubSignatureError):
        verify_github_signature(secret="gh-secret", body=b"{}", provided="sha256=nope")
