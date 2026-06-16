#!/usr/bin/env python3
"""Build the static GitHub Pages artifact from web assets and persisted data."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def copy_tree_contents(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def build_site(web_dir: Path, data_dir: Path, output_dir: Path) -> None:
    """Create a clean deploy directory without changing the tracked source assets."""
    if output_dir.resolve() in {Path.cwd().resolve(), Path("/").resolve()}:
        raise ValueError("refusing to build into the repository root or filesystem root")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    copy_tree_contents(web_dir, output_dir)
    copy_tree_contents(data_dir, output_dir / "data")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the static AI Radar site.")
    parser.add_argument("--web-dir", default="web", help="Directory containing static dashboard files.")
    parser.add_argument("--data-dir", default="data", help="Directory containing persisted JSON data.")
    parser.add_argument("--out", default="_site", help="Output directory for GitHub Pages upload.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    build_site(Path(args.web_dir), Path(args.data_dir), Path(args.out))
    print(f"Built static site in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
