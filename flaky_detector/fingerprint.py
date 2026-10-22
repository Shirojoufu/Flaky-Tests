"""Fingerprint the code each test depends on, so outcomes can be compared on identical code.

A test depends on its test file, the conftest files above it, dependency manifests, and the
measured source files it executed. Each run records the hash of every measured file and which
files each test executed. Analysis then fingerprints a test over the *union* of files it
executed in any run. A failing run that touched an extra module (an error handler, say) is
still compared with passing runs on the same code, and a change to any of those files still
counts as new code.
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
ABSENT = "absent"
HASH_LENGTH = 12


def digest(paths, hash_of) -> str:
    """Fingerprint ``paths`` given a lookup of each path's content hash (``ABSENT`` if missing)."""
    sha = hashlib.sha256()
    for path in sorted(paths):
        sha.update(f"{path}\0{hash_of(path)}\n".encode())
    return sha.hexdigest()[:20]


class Fingerprinter:
    def __init__(self, root: Path, tree: str, extra_patterns=DEFAULT_EXTRA, mode: str = PRECISE):
        self.root = root
        self.tree = tree
        self.mode = mode
        self.hashes: dict[str, str] = {}
        self._extra = sorted(
            {
                path.relative_to(root).as_posix()
                for pattern in extra_patterns
                for path in root.glob(pattern)
                if path.is_file()
            }
        )

    def files_for(self, dependencies: dict | None) -> list[str] | None:
        """Files a test depends on in this run, given its entry from ``dependencies_by_test``.

        ``None`` without per-test coverage (or in tree mode): the whole git tree stands in.
        """
        if self.mode == TREE or not dependencies:
            return None
        test_file = dependencies["file"]
        return sorted({test_file, *dependencies["deps"], *self._conftests(test_file), *self._extra})

    def for_test(self, files: list[str] | None) -> tuple[str, str]:
        """``(fingerprint, kind)`` of a test in this run alone."""
        if files is None:
            return f"tree:{self.tree}", TREE
        return digest(files, self.hash), PRECISE

    def hash(self, rel: str) -> str:
        if rel not in self.hashes:
            full = blob_hash(self.root / rel)
            self.hashes[rel] = ABSENT if full == "missing" else full[:HASH_LENGTH]
        return self.hashes[rel]

    def _conftests(self, test_file: str) -> list[str]:
        parent = PurePosixPath(test_file).parent
        candidates = ((directory / "conftest.py").as_posix() for directory in (parent, *parent.parents))
        return [path for path in candidates if (self.root / path).is_file()]
