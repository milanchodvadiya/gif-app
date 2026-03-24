#!/usr/bin/env python3
"""
Build a flat search_index.json listing every GIF/sticker/emoji from all JSON sources.

Sources:
  - collection_gifs/<category>/<slug>.json  -> sourcePath: collection_gifs/<category>/<slug>
  - trending.json                             -> sourcePath: trending
  - emoji.json                                -> sourcePath: emoji
  - <name>.json in project root (category GIF lists) -> sourcePath: <name>

Skips: collections.json, category.json, search_index.json

Usage:
  python3 scripts/build_search_index.py
  python3 scripts/build_search_index.py -o search_index.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..")
OUTPUT_DEFAULT = "search_index.json"

SKIP_ROOT_JSON = frozenset(
    {
        "collections.json",
        "category.json",
        "search_index.json",
    }
)


def load_json_array(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return data


def iter_collection_gifs(project_root: str) -> list[tuple[str, str]]:
    """Yield (absolute_path, sourcePath) for each JSON under collection_gifs/."""
    base = os.path.join(project_root, "collection_gifs")
    if not os.path.isdir(base):
        return []
    out: list[tuple[str, str]] = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, project_root)
            # sourcePath: collection_gifs/actions/breaking-up (no .json)
            source = rel[:-5] if rel.endswith(".json") else rel
            source = source.replace(os.sep, "/")
            out.append((full, source))
    return sorted(out, key=lambda x: x[1].lower())


def iter_root_gif_lists(project_root: str) -> list[tuple[str, str]]:
    """trending.json, emoji.json, and flat <id>.json category lists."""
    items: list[tuple[str, str]] = []
    for name in os.listdir(project_root):
        if not name.endswith(".json"):
            continue
        if name in SKIP_ROOT_JSON:
            continue
        if name == "search_index.json":
            continue
        full = os.path.join(project_root, name)
        if not os.path.isfile(full):
            continue
        stem = name[:-5]
        items.append((full, stem))
    return sorted(items, key=lambda x: x[1].lower())


def build_index(project_root: str) -> list[dict]:
    rows: list[dict] = []

    for path, source_path in iter_collection_gifs(project_root):
        for item in load_json_array(path):
            if not isinstance(item, dict):
                continue
            gid = item.get("id")
            if not gid:
                continue
            rows.append(
                {
                    "id": gid,
                    "title": item.get("title") or "",
                    "type": item.get("type") or "gif",
                    "sourcePath": source_path,
                }
            )

    for path, source_path in iter_root_gif_lists(project_root):
        for item in load_json_array(path):
            if not isinstance(item, dict):
                continue
            gid = item.get("id")
            if not gid:
                continue
            rows.append(
                {
                    "id": gid,
                    "title": item.get("title") or "",
                    "type": item.get("type") or "gif",
                    "sourcePath": source_path,
                }
            )

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build flat search_index.json from all GIF JSON sources")
    parser.add_argument(
        "-o",
        "--output",
        default=os.path.join(PROJECT_ROOT, OUTPUT_DEFAULT),
        help="Output path (default: search_index.json in project root)",
    )
    parser.add_argument(
        "--dedupe-id",
        action="store_true",
        help="Keep only the first occurrence of each id (drops duplicate ids from other sources)",
    )
    args = parser.parse_args()

    project_root = os.path.abspath(PROJECT_ROOT)
    out_path = os.path.abspath(args.output)

    rows = build_index(project_root)

    if args.dedupe_id:
        seen: set[str] = set()
        deduped: list[dict] = []
        for r in rows:
            rid = r["id"]
            if rid in seen:
                continue
            seen.add(rid)
            deduped.append(r)
        rows = deduped

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(rows)} entries to {out_path}")


if __name__ == "__main__":
    main()
