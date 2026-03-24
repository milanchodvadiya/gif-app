#!/usr/bin/env python3
"""
Run the full data pipeline in one command:

  1. Scrape collections.json + collection_gifs/ (Playwright)
  2. Build search_index.json from all JSON sources

Usage:
  python3 scripts/update_all_data.py
  python3 scripts/update_all_data.py --index-only          # only step 2 (fast)
  python3 scripts/update_all_data.py --max-gifs 500
  python3 scripts/update_all_data.py --limit-subcollections 10   # smoke test scrape

Environment (optional):
  UPDATE_INDEX_ONLY=1   same as --index-only
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..")


def run(cmd: list[str], cwd: str) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update collections data + search index in one run",
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="Only rebuild search_index.json (skip Playwright scrape)",
    )
    parser.add_argument(
        "--max-gifs",
        type=int,
        default=500,
        help="Max GIFs per subcollection when scraping (default: 500)",
    )
    parser.add_argument(
        "--gifs-dir",
        default=None,
        help="Output dir for collection_gifs (default: project collection_gifs/)",
    )
    parser.add_argument(
        "--collections-output",
        default=None,
        help="Path for collections.json (default: project root)",
    )
    parser.add_argument(
        "--limit-subcollections",
        type=int,
        default=None,
        metavar="N",
        help="Only scrape first N subcollections (testing)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Playwright with visible browser",
    )
    args = parser.parse_args()

    index_only = args.index_only or os.environ.get("UPDATE_INDEX_ONLY", "").strip() in ("1", "true", "yes")

    py = sys.executable
    root = os.path.abspath(PROJECT_ROOT)
    scrape = os.path.join(SCRIPT_DIR, "scrape_collections.py")
    build = os.path.join(SCRIPT_DIR, "build_search_index.py")

    if not index_only:
        cmd = [
            py,
            scrape,
            "--all",
            "--max-gifs",
            str(args.max_gifs),
        ]
        if args.gifs_dir:
            cmd.extend(["--gifs-dir", os.path.abspath(args.gifs_dir)])
        if args.collections_output:
            cmd.extend(["-o", os.path.abspath(args.collections_output)])
        if args.limit_subcollections is not None:
            cmd.extend(["--limit-subcollections", str(args.limit_subcollections)])
        if args.headed:
            cmd.append("--headed")
        run(cmd, cwd=root)

    run([py, build], cwd=root)

    print("Done.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
