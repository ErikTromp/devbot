from __future__ import annotations

from app.security.secrets import compare_signatures, hmac_sha256_hex


class GitHubSignatureError(ValueError):
    pass


def github_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac_sha256_hex(secret, body)


def verify_github_signature(*, secret: str, body: bytes, provided: str) -> None:
    if not secret:
        raise GitHubSignatureError("GitHub webhook secret is not configured")
    expected = github_signature(secret, body)
    if not provided or not compare_signatures(expected, provided):
        raise GitHubSignatureError("Invalid GitHub signature")
