from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from get_myhome_ai import __version__
from get_myhome_ai.runtime_source import (
    SOURCE_FINGERPRINT_ALGORITHM,
    compute_python_source_fingerprint,
)
from get_myhome_ai.settings import Settings

SYSTEMD_TIMESTAMP = re.compile(
    r"^\S+\s+(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2})"
)
RUNTIME_PATHS = ("src/get_myhome_ai", "pyproject.toml")


def _command(arguments: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _git_revision(repo: Path, *, runtime_only: bool) -> tuple[str, str]:
    arguments = ["git", "log", "-1", "--format=%H%x1f%cI"]
    if runtime_only:
        arguments.extend(["--", *RUNTIME_PATHS])
    output = _command(arguments, cwd=repo)
    commit, committed_at = output.split("\x1f", maxsplit=1)
    return commit, committed_at


def _runtime_tree_changes(repo: Path) -> list[str]:
    output = _command(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            *RUNTIME_PATHS,
        ],
        cwd=repo,
    )
    return [line for line in output.splitlines() if line]


def _systemd_properties(service: str, *, repo: Path) -> dict[str, str]:
    try:
        output = _command(
            [
                "systemctl",
                "--user",
                "show",
                service,
                "--property=LoadState,ActiveState,SubState,MainPID,ExecMainStartTimestamp,"
                "FragmentPath,WorkingDirectory",
                "--no-pager",
            ],
            cwd=repo,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"collection_error": type(exc).__name__}
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _fetch_json(url: str, *, timeout_seconds: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            content = response.read(65_537)
            if len(content) > 65_536:
                raise ValueError("response exceeds 64 KiB")
            payload = json.loads(content)
    except HTTPError as exc:
        return {"reachable": False, "http_status": exc.code, "error": "HTTPError"}
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        return {"reachable": False, "error": type(exc).__name__}
    if not isinstance(payload, dict):
        return {"reachable": False, "error": "NonObjectResponse"}
    return {"reachable": True, "payload": payload}


def _safe_health(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("reachable"):
        return result
    payload = result["payload"]
    return {
        "reachable": True,
        "status": payload.get("status"),
        "version": payload.get("version"),
        "source_fingerprint_algorithm": payload.get("source_fingerprint_algorithm"),
        "source_fingerprint_sha256": payload.get("source_fingerprint_sha256"),
    }


def _safe_readiness(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("reachable"):
        return result
    payload = result["payload"]
    checks = payload.get("checks")
    safe_checks = (
        {str(key): bool(value) for key, value in checks.items()}
        if isinstance(checks, dict)
        else None
    )
    return {
        "reachable": True,
        "ready": payload.get("ready"),
        "provider": payload.get("provider"),
        "checks": safe_checks,
    }


def _parse_systemd_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    match = SYSTEMD_TIMESTAMP.match(value)
    if not match:
        return None
    parsed = datetime.fromisoformat(f"{match.group('date')}T{match.group('time')}")
    return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)


def evaluate_runtime_sync(snapshot: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    service = snapshot["service"]
    local = snapshot["local"]
    health = snapshot["health"]
    readiness = snapshot["readiness"]

    def add(code: str, severity: str, message: str) -> None:
        findings.append({"code": code, "severity": severity, "message": message})

    if service.get("LoadState") != "loaded":
        add("SERVICE_NOT_LOADED", "ERROR", "The AI service unit is not loaded.")
    if service.get("ActiveState") != "active" or service.get("SubState") != "running":
        add("SERVICE_NOT_RUNNING", "ERROR", "The AI service is not running.")
    if not snapshot.get("working_directory_matches"):
        add(
            "WORKING_DIRECTORY_MISMATCH",
            "ERROR",
            "The running service does not point at the inspected repository.",
        )
    if not snapshot.get("unit_template_matches"):
        add(
            "UNIT_TEMPLATE_MISMATCH",
            "ERROR",
            "The installed systemd unit differs from the server-local template.",
        )
    if not health.get("reachable") or health.get("status") != "ok":
        add("HEALTH_CHECK_FAILED", "ERROR", "The health endpoint did not return status=ok.")
    elif health.get("version") != local["app_version"]:
        add(
            "APP_VERSION_MISMATCH",
            "ERROR",
            "The health version differs from the checked-out application version.",
        )
    fingerprint_matches = (
        health.get("source_fingerprint_algorithm") == local["source_fingerprint_algorithm"]
        and health.get("source_fingerprint_sha256") == local["source_fingerprint_sha256"]
    )
    if health.get("reachable") and health.get("status") == "ok":
        runtime_algorithm = health.get("source_fingerprint_algorithm")
        runtime_fingerprint = health.get("source_fingerprint_sha256")
        if runtime_algorithm is None and runtime_fingerprint is None:
            add(
                "SOURCE_FINGERPRINT_MISSING",
                "RESTART_REQUIRED",
                "The running service predates source-fingerprinted health responses.",
            )
        elif runtime_algorithm != local["source_fingerprint_algorithm"]:
            add(
                "SOURCE_FINGERPRINT_ALGORITHM_MISMATCH",
                "ERROR",
                "Runtime and local source fingerprint algorithms differ or are missing.",
            )
        elif runtime_fingerprint != local["source_fingerprint_sha256"]:
            add(
                "SOURCE_FINGERPRINT_MISMATCH",
                "RESTART_REQUIRED",
                "The running service source fingerprint differs from the local source tree.",
            )
    if not readiness.get("reachable") or readiness.get("ready") is not True:
        add(
            "READINESS_CHECK_FAILED",
            "ERROR",
            "The readiness endpoint is not ready.",
        )
    if readiness.get("reachable") and readiness.get("provider") != local["expected_provider"]:
        add(
            "PROVIDER_MISMATCH",
            "ERROR",
            "The ready provider differs from the explicitly expected provider.",
        )

    service_started = _parse_systemd_timestamp(service.get("ExecMainStartTimestamp"))
    runtime_committed = datetime.fromisoformat(local["runtime_revision_committed_at"])
    if service_started is None:
        add(
            "SERVICE_START_TIME_UNAVAILABLE",
            "INFO",
            "The service start time could not be compared with the runtime revision; "
            "the source fingerprint remains authoritative.",
        )
    elif service_started < runtime_committed.astimezone(service_started.tzinfo):
        add(
            "SERVICE_STARTED_BEFORE_RUNTIME_REVISION",
            "INFO",
            "The service predates the latest runtime commit; the source fingerprint "
            "determines whether a restart is required.",
        )
    if local["runtime_tree_changes"]:
        add(
            "RUNTIME_TREE_DIRTY",
            "INFO",
            "Runtime source files have uncommitted changes; exact sync is decided "
            "from their source fingerprint.",
        )

    severities = {finding["severity"] for finding in findings}
    if "ERROR" in severities:
        status = "FAILED"
    elif "RESTART_REQUIRED" in severities:
        status = "RESTART_REQUIRED"
    elif fingerprint_matches:
        status = "EXACT_RUNTIME_MATCH"
    else:
        status = "FAILED"
    return {
        "status": status,
        "exact_runtime_source_fingerprint_match": fingerprint_matches,
        "findings": findings,
    }


def collect_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    repo = args.repo.resolve()
    head_revision, head_committed_at = _git_revision(repo, runtime_only=False)
    runtime_revision, runtime_committed_at = _git_revision(repo, runtime_only=True)
    service = _systemd_properties(args.service, repo=repo)
    working_directory = service.get("WorkingDirectory")
    working_directory_matches = bool(
        working_directory and Path(working_directory).resolve() == repo
    )
    expected_unit_hash = _sha256(args.expected_unit)
    installed_unit_hash = _sha256(args.installed_unit)
    defaults = Settings.model_fields

    snapshot: dict[str, Any] = {
        "schema_version": "runtime_sync_report_v0.2",
        "generated_at": datetime.now().astimezone().isoformat(),
        "local": {
            "repo_head_revision": head_revision,
            "repo_head_committed_at": head_committed_at,
            "runtime_revision": runtime_revision,
            "runtime_revision_committed_at": runtime_committed_at,
            "runtime_tree_changes": _runtime_tree_changes(repo),
            "app_version": __version__,
            "schema_version": defaults["schema_version"].default,
            "extractor_version": defaults["extractor_version"].default,
            "expected_provider": args.expected_provider,
            "source_fingerprint_algorithm": SOURCE_FINGERPRINT_ALGORITHM,
            "source_fingerprint_sha256": compute_python_source_fingerprint(),
        },
        "service": service,
        "working_directory_matches": working_directory_matches,
        "unit_template_matches": bool(
            expected_unit_hash
            and installed_unit_hash
            and expected_unit_hash == installed_unit_hash
        ),
        "health": _safe_health(
            _fetch_json(args.health_url, timeout_seconds=args.timeout_seconds)
        ),
        "readiness": _safe_readiness(
            _fetch_json(args.ready_url, timeout_seconds=args.timeout_seconds)
        ),
    }
    snapshot["evaluation"] = evaluate_runtime_sync(snapshot)
    return snapshot


def _parse_args() -> argparse.Namespace:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Read-only runtime/deployment sync check without reading service secrets."
    )
    parser.add_argument("--repo", type=Path, default=repo)
    parser.add_argument("--service", default="get-myhome-ai.service")
    parser.add_argument(
        "--expected-unit",
        type=Path,
        default=repo / ".local/runtime/get-myhome-ai.service",
    )
    parser.add_argument(
        "--installed-unit",
        type=Path,
        default=Path.home() / ".config/systemd/user/get-myhome-ai.service",
    )
    parser.add_argument("--health-url", default="http://127.0.0.1:9000/health")
    parser.add_argument("--ready-url", default="http://127.0.0.1:9000/ready")
    parser.add_argument(
        "--expected-provider",
        choices=("ollama", "openai", "fixture"),
        default=Settings.model_fields["ai_provider"].default,
    )
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    snapshot = collect_snapshot(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0 if snapshot["evaluation"]["status"] == "EXACT_RUNTIME_MATCH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
