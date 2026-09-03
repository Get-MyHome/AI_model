from __future__ import annotations

from pathlib import Path

from get_myhome_ai.runtime_source import (
    SOURCE_FINGERPRINT_ALGORITHM,
    compute_python_source_fingerprint,
)
from scripts.check_runtime_sync import (
    _safe_health,
    _safe_readiness,
    evaluate_runtime_sync,
)


def _snapshot(*, service_start: str, changes: list[str] | None = None) -> dict:
    return {
        "local": {
            "app_version": "0.3.0",
            "runtime_revision_committed_at": "2026-09-04T12:00:00+09:00",
            "runtime_tree_changes": changes or [],
            "expected_provider": "ollama",
            "source_fingerprint_algorithm": SOURCE_FINGERPRINT_ALGORITHM,
            "source_fingerprint_sha256": "a" * 64,
        },
        "service": {
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "ExecMainStartTimestamp": service_start,
        },
        "working_directory_matches": True,
        "unit_template_matches": True,
        "health": {
            "reachable": True,
            "status": "ok",
            "version": "0.3.0",
            "source_fingerprint_algorithm": SOURCE_FINGERPRINT_ALGORITHM,
            "source_fingerprint_sha256": "a" * 64,
        },
        "readiness": {
            "reachable": True,
            "ready": True,
            "provider": "ollama",
        },
    }


def test_exact_fingerprint_passes_even_when_commit_timestamp_is_newer() -> None:
    result = evaluate_runtime_sync(
        _snapshot(service_start="Fri 2026-09-04 11:59:00 KST")
    )

    assert result["status"] == "EXACT_RUNTIME_MATCH"
    assert {item["code"] for item in result["findings"]} == {
        "SERVICE_STARTED_BEFORE_RUNTIME_REVISION"
    }
    assert result["exact_runtime_source_fingerprint_match"] is True


def test_fingerprint_mismatch_requires_restart_despite_newer_start_time() -> None:
    snapshot = _snapshot(service_start="Fri 2026-09-04 12:01:00 KST")
    snapshot["health"]["source_fingerprint_sha256"] = "b" * 64
    result = evaluate_runtime_sync(snapshot)

    assert result["status"] == "RESTART_REQUIRED"
    assert result["findings"] == [
        {
            "code": "SOURCE_FINGERPRINT_MISMATCH",
            "severity": "RESTART_REQUIRED",
            "message": (
                "The running service source fingerprint differs from the local source tree."
            ),
        }
    ]
    assert result["exact_runtime_source_fingerprint_match"] is False


def test_provider_must_match_explicit_expectation() -> None:
    snapshot = _snapshot(service_start="Fri 2026-09-04 12:01:00 KST")
    snapshot["readiness"]["provider"] = "openai"
    result = evaluate_runtime_sync(snapshot)

    assert result["status"] == "FAILED"
    assert {item["code"] for item in result["findings"]} == {"PROVIDER_MISMATCH"}


def test_old_health_without_fingerprint_requires_restart() -> None:
    snapshot = _snapshot(service_start="Fri 2026-09-04 12:01:00 KST")
    snapshot["health"]["source_fingerprint_algorithm"] = None
    snapshot["health"]["source_fingerprint_sha256"] = None
    result = evaluate_runtime_sync(snapshot)

    assert result["status"] == "RESTART_REQUIRED"
    assert {item["code"] for item in result["findings"]} == {
        "SOURCE_FINGERPRINT_MISSING"
    }


def test_endpoint_sanitizers_drop_unknown_sensitive_fields() -> None:
    health = _safe_health(
        {
            "reachable": True,
            "payload": {
                "status": "ok",
                "version": "0.3.0",
                "source_fingerprint_algorithm": SOURCE_FINGERPRINT_ALGORITHM,
                "source_fingerprint_sha256": "a" * 64,
                "api_key": "must-not-leak",
            },
        }
    )
    readiness = _safe_readiness(
        {
            "reachable": True,
            "payload": {
                "ready": True,
                "provider": "ollama",
                "checks": {"provider": True},
                "pdf_url": "must-not-leak",
            },
        }
    )

    assert health == {
        "reachable": True,
        "status": "ok",
        "version": "0.3.0",
        "source_fingerprint_algorithm": SOURCE_FINGERPRINT_ALGORITHM,
        "source_fingerprint_sha256": "a" * 64,
    }
    assert readiness == {
        "reachable": True,
        "ready": True,
        "provider": "ollama",
        "checks": {"provider": True},
    }


def test_python_source_fingerprint_is_deterministic_and_content_sensitive(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    (package_root / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    nested = package_root / "nested"
    nested.mkdir()
    (nested / "b.py").write_text("VALUE = 2\n", encoding="utf-8")

    first = compute_python_source_fingerprint(package_root)
    assert compute_python_source_fingerprint(package_root) == first

    (nested / "b.py").write_text("VALUE = 3\n", encoding="utf-8")
    assert compute_python_source_fingerprint(package_root) != first
