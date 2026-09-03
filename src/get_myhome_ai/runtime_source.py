from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE_FINGERPRINT_ALGORITHM = "sha256-python-source-tree-v1"


def compute_python_source_fingerprint(package_root: Path | None = None) -> str:
    """Hash package-relative paths and bytes for every deployed Python source file."""

    root = (package_root or Path(__file__).resolve().parent).resolve()
    digest = hashlib.sha256()
    digest.update(SOURCE_FINGERPRINT_ALGORITHM.encode("ascii"))
    for path in sorted(root.rglob("*.py"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


# Freeze the fingerprint when this module is imported. A later on-disk edit cannot make an old
# service process claim that it has loaded the new source tree.
RUNNING_SOURCE_FINGERPRINT_SHA256 = compute_python_source_fingerprint()
