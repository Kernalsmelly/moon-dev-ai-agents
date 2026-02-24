#!/usr/bin/env python3
"""Compile-check runtime Python code while quarantining non-runtime datasets.

Default policy:
- include: src/, scripts/, tests/
- exclude: src/data/** and other non-runtime folders
"""
from __future__ import annotations

import argparse
import py_compile
from pathlib import Path


DEFAULT_ROOTS = ("src", "scripts", "tests")
DEFAULT_EXCLUDE_PREFIXES = (
    "src/data/",
    "data/",
    "docs/",
    ".venv/",
    "venv/",
    "rich_stub/",
    "rich_unused/",
)


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def iter_python_files(base: Path, roots: list[str], exclude_prefixes: list[str]) -> list[Path]:
    out: list[Path] = []
    excludes = tuple(p.rstrip("/") + "/" for p in exclude_prefixes if p.strip())
    for root in roots:
        root_path = (base / root).resolve()
        if not root_path.exists():
            continue
        for p in root_path.rglob("*.py"):
            rel = p.resolve().relative_to(base.resolve()).as_posix()
            if rel.startswith(excludes):
                continue
            out.append(p)
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", default=",".join(DEFAULT_ROOTS))
    ap.add_argument("--exclude-prefixes", default=",".join(DEFAULT_EXCLUDE_PREFIXES))
    ap.add_argument("--max-errors", type=int, default=30)
    args = ap.parse_args()

    base = Path.cwd()
    roots = _split_csv(args.roots)
    excludes = _split_csv(args.exclude_prefixes)
    files = iter_python_files(base, roots, excludes)

    errors: list[tuple[str, str]] = []
    for f in files:
        try:
            py_compile.compile(str(f), doraise=True)
        except Exception as e:  # pragma: no cover - exercised in integration usage
            rel = f.resolve().relative_to(base.resolve()).as_posix()
            errors.append((rel, str(e).splitlines()[-1]))

    print(
        "runtime_compile_check "
        f"roots={roots} excludes={excludes} files={len(files)} errors={len(errors)}"
    )
    for rel, err in errors[: max(1, int(args.max_errors))]:
        print(f"  {rel}: {err}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
