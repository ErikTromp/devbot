from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.agents.base import AgentContext, AgentResult
from app.config import Settings
from app.jobs.models import CheckResult
from app.process import ProcessTimeout, run_process

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".next",
    ".turbo",
}
_UNIT_SCRIPTS = ("test", "test:unit", "test:ci")


def detect_test_command(worktree: Path) -> list[str] | None:
    found = detect_test_commands(worktree)
    return found[0][1] if found else None


def detect_test_commands(worktree: Path) -> list[tuple[Path, list[str]]]:
    found: list[tuple[Path, list[str]]] = []
    seen: set[Path] = set()
    for cwd in _candidate_dirs(worktree):
        key = cwd.resolve()
        if key in seen:
            continue
        seen.add(key)
        command = _detect_in(cwd, allow_tests_dir=cwd.resolve() == worktree.resolve())
        if command:
            found.append((cwd, command))
    return found


def _candidate_dirs(worktree: Path) -> list[Path]:
    dirs = [worktree]
    if worktree.is_dir():
        dirs.extend(
            child
            for child in sorted(worktree.iterdir())
            if child.is_dir() and child.name not in _SKIP_DIRS and not child.name.startswith(".")
        )
    return dirs


def _detect_in(cwd: Path, *, allow_tests_dir: bool = True) -> list[str] | None:
    pkg = cwd / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        scripts = data.get("scripts") or {}
        for name in _UNIT_SCRIPTS:
            if name in scripts:
                return ["npm", "test", "--silent"] if name == "test" else ["npm", "run", name, "--silent"]
    if (cwd / "pyproject.toml").exists() or (cwd / "pytest.ini").exists() or (allow_tests_dir and (cwd / "tests").is_dir()):
        return ["python", "-m", "pytest", "-q"]
    if (cwd / "Makefile").exists():
        text = (cwd / "Makefile").read_text(encoding="utf-8", errors="replace")
        if "\ntest:" in f"\n{text}" or text.startswith("test:"):
            return ["make", "test"]
    return None


class TestAgent:
    __test__ = False
    name = "testing"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, context: AgentContext) -> AgentResult:
        override = context.extra.get("command")
        jobs = [(context.worktree, override)] if override else detect_test_commands(context.worktree)
        if not jobs:
            return AgentResult(
                ok=False,
                summary="No test runner detected; cannot report tests as passed.",
                structured={"status": CheckResult.NOT_APPLICABLE.value, "tests_run": []},
                metadata={"status": CheckResult.NOT_APPLICABLE.value},
            )
        ran: list[str] = []
        skipped: list[str] = []
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        duration = 0.0
        last_code = 0
        for cwd, command in jobs:
            if shutil.which(command[0]) is None:
                return _missing_binary(command)
            install = _js_install_command(cwd) if command[0] == "npm" else None
            if install:
                installed = _run(install, cwd, self.settings.test_timeout_seconds)
                if isinstance(installed, AgentResult):
                    return installed
                duration += installed.duration_seconds
                if installed.returncode != 0:
                    return AgentResult(
                        ok=False,
                        summary=f"`{' '.join(install)}` failed in `{cwd.name or '.'}`; cannot run tests.",
                        stdout=installed.stdout[-8000:],
                        stderr=installed.stderr[-8000:],
                        exit_code=installed.returncode,
                        duration_seconds=duration,
                        structured={"status": CheckResult.BLOCKED.value, "tests_run": install},
                    )
            result = _run(command, cwd, self.settings.test_timeout_seconds)
            if isinstance(result, AgentResult):
                return result
            stdout_parts.append(result.stdout)
            stderr_parts.append(result.stderr)
            duration += result.duration_seconds
            last_code = result.returncode
            name = cwd.name or "."
            if result.returncode != 0 and _looks_like_unmatched_glob(result.stdout, result.stderr):
                skipped.append(name)
                continue
            if result.returncode != 0 and _looks_like_missing_deps(result.stdout, result.stderr):
                return AgentResult(
                    ok=False,
                    summary="Tests could not start (missing dependencies), not a product regression.",
                    stdout=result.stdout[-8000:],
                    stderr=result.stderr[-8000:],
                    exit_code=result.returncode,
                    duration_seconds=duration,
                    structured={"status": CheckResult.BLOCKED.value, "tests_run": command},
                )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip().splitlines()
                tail = f": {detail[-1]}" if detail else ""
                return AgentResult(
                    ok=False,
                    summary=f"Tests failed in `{name}`{tail}",
                    stdout=result.stdout[-8000:],
                    stderr=result.stderr[-8000:],
                    exit_code=result.returncode,
                    duration_seconds=duration,
                    structured={"status": CheckResult.FAIL.value, "tests_run": ran},
                )
            ran.append(f"{' '.join(command)} ({name})")
        if not ran and skipped:
            places = ", ".join(skipped)
            return AgentResult(
                ok=False,
                summary=f"No test files matched in {places}. The script passed a glob the shell did not expand, so this is not a product failure.",
                stdout="\n".join(stdout_parts)[-8000:],
                stderr="\n".join(stderr_parts)[-8000:],
                exit_code=last_code,
                duration_seconds=duration,
                structured={"status": CheckResult.NOT_APPLICABLE.value, "tests_run": []},
            )
        note = f" No matching files in {', '.join(skipped)}." if skipped else ""
        return AgentResult(
            ok=True,
            summary="Tests passed: " + ", ".join(ran) + note,
            stdout="\n".join(stdout_parts)[-8000:],
            stderr="\n".join(stderr_parts)[-8000:],
            exit_code=last_code,
            duration_seconds=duration,
            structured={"status": CheckResult.PASS.value, "tests_run": ran},
        )


def _run(command: list[str], cwd: Path, timeout: int):
    try:
        return run_process(command, cwd=cwd, timeout=timeout)
    except FileNotFoundError:
        return _missing_binary(command)
    except ProcessTimeout as exc:
        return _timeout_result(exc, command)


def _js_install_command(worktree: Path) -> list[str] | None:
    if not (worktree / "package.json").exists() or (worktree / "node_modules").is_dir():
        return None
    if (worktree / "package-lock.json").exists() or (worktree / "npm-shrinkwrap.json").exists():
        return ["npm", "ci"]
    return ["npm", "install"]


def _looks_like_unmatched_glob(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}"
    return "could not find" in text.lower() and "*" in text


def _looks_like_missing_deps(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return any(
        marker in text
        for marker in ("not found", "cannot find module", "enoent", "npm err!", "err_module_not_found")
    )


def _missing_binary(command: list[str]) -> AgentResult:
    return AgentResult(
        ok=False,
        summary=f"Test command `{command[0]}` is not installed in this worker.",
        structured={"status": CheckResult.NOT_APPLICABLE.value, "tests_run": command},
        metadata={"status": CheckResult.NOT_APPLICABLE.value},
    )


def _timeout_result(exc: ProcessTimeout, command: list[str]) -> AgentResult:
    return AgentResult(
        ok=False,
        summary="Tests timed out",
        stdout=exc.result.stdout[-8000:],
        stderr=exc.result.stderr[-8000:],
        exit_code=exc.result.returncode,
        duration_seconds=exc.result.duration_seconds,
        structured={"status": CheckResult.BLOCKED.value, "tests_run": command},
    )
