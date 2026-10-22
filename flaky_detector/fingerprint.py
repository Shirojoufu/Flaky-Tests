"""Fingerprint the code each test depends on, so outcomes can be compared on identical code.

Two runs of a test with the same fingerprint executed the same test file, conftest files,
dependency manifests and measured source files. If one passed and the other failed, the
difference came from somewhere other than the code: the test is flaky.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath

from .gitutil import blob_hash

DEFAULT_EXTRA = (
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "requirements*.txt",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
    "pytest.ini",
    "tox.ini",
)

PRECISE = "precise"
TREE = "tree"


class Fingerprinter:
    def __init__(self, root: Path, tree: str, extra_patterns=DEFAULT_EXTRA, mode: str = PRECISE):
        self.root = root
        self.tree = tree
        self.mode = mode
        self._hashes: dict[str, str] = {}
        self._extra = sorted(
            {
                path.relative_to(root).as_posix()
                for pattern in extra_patterns
                for path in root.glob(pattern)
                if path.is_file()
            }
        )

    def for_test(self, dependencies: dict | None) -> tuple[str, str]:
        """Return ``(fingerprint, kind)`` for a test given its entry from ``dependencies_by_test``.

        Without per-test coverage the whole git tree is used, so any change counts as new code.
        """
        if self.mode == TREE or not dependencies:
            return f"tree:{self.tree}", TREE
        test_file = dependencies["file"]
        files = {test_file, *dependencies["deps"], *self._conftests(test_file), *self._extra}
        digest = hashlib.sha256()
        for rel in sorted(files):
            digest.update(f"{rel}\0{self._hash(rel)}\n".encode())
        return digest.hexdigest()[:20], PRECISE

    def _hash(self, rel: str) -> str:
        if rel not in self._hashes:
            self._hashes[rel] = blob_hash(self.root / rel)
        return self._hashes[rel]

    def _conftests(self, test_file: str) -> list[str]:
        parent = PurePosixPath(test_file).parent
        candidates = ((directory / "conftest.py").as_posix() for directory in (parent, *parent.parents))
        return [path for path in candidates if (self.root / path).is_file()]
