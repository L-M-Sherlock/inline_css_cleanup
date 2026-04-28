#!/usr/bin/env python3
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = PROJECT_ROOT / "dist" / "inline-css-cleanup.ankiaddon"
REQUIRED_FILES = {
    "__init__.py",
    "manifest.json",
    "config.json",
    "config.md",
    "README.md",
    "user_files/README.txt",
}
FORBIDDEN_PARTS = {"__pycache__", ".git", ".ruff_cache", ".vscode"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".DS_Store"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the packaged Inline CSS Cleanup add-on."
    )
    parser.add_argument(
        "archive",
        nargs="?",
        type=Path,
        default=DEFAULT_ARCHIVE,
        help="Path to .ankiaddon archive",
    )
    return parser.parse_args()


def validate_archive(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        bad_file = archive.testzip()
        if bad_file:
            raise ValueError(f"Invalid file in archive: {bad_file}")

        names = set(archive.namelist())
        missing = sorted(REQUIRED_FILES - names)
        if missing:
            raise ValueError(f"Missing packaged files: {missing}")

        unexpected = sorted(
            name
            for name in names
            if any(part in FORBIDDEN_PARTS for part in Path(name).parts)
            or any(name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES)
        )
        if unexpected:
            raise ValueError(f"Unexpected packaged files: {unexpected}")


def main() -> int:
    args = parse_args()
    path = args.archive.expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Missing add-on archive: {path}")

    validate_archive(path)
    print(f"Validated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
