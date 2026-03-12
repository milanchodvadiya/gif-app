#!/usr/bin/env python3
"""
Generate emoji.json and trending.json from Giphy APIs.
Unified script with rate limit handling and multi-API-key support.

Usage:
  python generate_giphy_data.py              # Generate all (emoji, trending, categories)
  python generate_giphy_data.py --emoji-only
  python generate_giphy_data.py --trending-only
  python generate_giphy_data.py --categories-only

API Keys (Giphy beta: 100 calls/hour per key):
  GIPHY_API_KEY=key1           # Single key
  GIPHY_API_KEYS=key1,key2,key3   # Multiple keys - rotates on 429 or at 95 calls
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..")
LIMIT = 50
MAX_TRENDING_OFFSET = 499
CATEGORY_LIMIT = 50  # GIFs per request (search API)
CATEGORY_TARGET = 1000  # Total GIFs to fetch per category
CATEGORY_FILE = "category.json"
RATE_LIMIT_PER_KEY = 100  # Giphy beta: 100 calls/hour per key
SAFE_CALLS_BEFORE_SWITCH = 95  # Switch key before hitting limit


def get_api_keys() -> list[str]:
    """Get API keys from env. Supports GIPHY_API_KEYS (comma-separated) or GIPHY_API_KEY."""
    keys_str = os.environ.get("GIPHY_API_KEYS") or os.environ.get("GIPHY_API_KEY")
    if not keys_str:
        return ["u6S5Gu1SXT0B8AE330d2M1zPLKjufe0H"]  # Default fallback
    return [k.strip() for k in keys_str.split(",") if k.strip()]


class GiphyApiClient:
    """Handles Giphy API requests with rate limit awareness and key rotation."""

    def __init__(self):
        self._keys = get_api_keys()
        self._key_index = 0
        self._calls_this_key = 0

    @property
    def current_key(self) -> str:
        return self._keys[self._key_index]

    def _rotate_key(self) -> bool:
        """Switch to next API key. Returns False if no more keys."""
        self._key_index += 1
        self._calls_this_key = 0
        if self._key_index >= len(self._keys):
            return False
        print(f"  Switched to API key {self._key_index + 1}/{len(self._keys)}", file=sys.stderr)
        return True

    def _fetch(self, base_url: str, params: dict) -> dict:
        """Make a GET request. Handles 429 by rotating keys and retrying with new key."""
        while True:
            req_params = {**params, "api_key": self.current_key}
            query = urllib.parse.urlencode(req_params)
            url = f"{base_url}?{query}"
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")

            try:
                with urllib.request.urlopen(req) as response:
                    self._calls_this_key += 1
                    return json.loads(response.read().decode())
            except urllib.error.URLError as e:
                if "CERTIFICATE_VERIFY_FAILED" in str(e):
                    context = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, context=context) as response:
                        self._calls_this_key += 1
                        return json.loads(response.read().decode())
                raise
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if self._rotate_key():
                        time.sleep(1)  # Brief pause before retry
                        continue
                    raise RuntimeError("Rate limit exceeded. No more API keys available.") from e
                if e.code == 401:
                    raise RuntimeError("Invalid API key. Check GIPHY_API_KEY or GIPHY_API_KEYS.") from e
                raise

    def fetch(self, base_url: str, params: dict) -> dict:
        """Fetch and proactively rotate key when approaching rate limit."""
        if self._calls_this_key >= SAFE_CALLS_BEFORE_SWITCH and len(self._keys) > 1:
            if self._rotate_key():
                time.sleep(1)
        return self._fetch(base_url, params)


def generate_emoji(client: GiphyApiClient) -> None:
    """Fetch all emojis and write to emoji.json."""
    all_emojis = []
    offset = 0
    base_url = "https://api.giphy.com/v2/emoji"

    print("Fetching emojis from Giphy API...")

    while True:
        data = client.fetch(base_url, {"limit": LIMIT, "offset": offset})
        items = data.get("data", [])

        for item in items:
            all_emojis.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "type": item.get("type", "emoji"),
            })

        if len(items) == 0 or len(items) < LIMIT:
            break

        offset += LIMIT
        print(f"  Fetched {len(all_emojis)} emojis so far...")

    output_path = os.path.join(PROJECT_ROOT, "emoji.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_emojis, f, indent=2, ensure_ascii=False)
    print(f"Done! Wrote {len(all_emojis)} emojis to {output_path}")


def generate_trending(client: GiphyApiClient) -> None:
    """Fetch trending GIFs and write to trending.json."""
    all_gifs = []
    offset = 0
    base_url = "https://api.giphy.com/v1/gifs/trending"

    print("Fetching trending GIFs from Giphy API...")

    while offset <= MAX_TRENDING_OFFSET:
        params = {"limit": LIMIT, "offset": offset, "rating": "g", "bundle": "messaging_non_clips"}
        data = client.fetch(base_url, params)
        items = data.get("data", [])

        for item in items:
            all_gifs.append({
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "type": item.get("type", "gif"),
            })

        if len(items) == 0 or len(items) < LIMIT:
            break

        offset += LIMIT
        print(f"  Fetched {len(all_gifs)} GIFs so far...")

    output_path = os.path.join(PROJECT_ROOT, "trending.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_gifs, f, indent=2, ensure_ascii=False)
    print(f"Done! Wrote {len(all_gifs)} trending GIFs to {output_path}")


def generate_categories(client: GiphyApiClient) -> None:
    """Fetch GIFs by category from category.json and write {id}.json for each."""
    category_path = os.path.join(PROJECT_ROOT, CATEGORY_FILE)
    if not os.path.exists(category_path):
        raise FileNotFoundError(f"Category file not found: {category_path}")

    with open(category_path, encoding="utf-8") as f:
        categories = json.load(f)

    base_url = "https://api.giphy.com/v1/gifs/search"

    print(f"Fetching GIFs for {len(categories)} categories from Giphy API...")

    for cat in categories:
        cat_id = cat.get("id", "")
        title = cat.get("title", "")
        if not title:
            print(f"  Skipping category '{cat_id}' (no title)", file=sys.stderr)
            continue

        gifs = []
        offset = 0

        while len(gifs) < CATEGORY_TARGET:
            params = {
                "q": title,
                "limit": CATEGORY_LIMIT,
                "offset": offset,
                "rating": "g",
                "bundle": "messaging_non_clips",
                "lang": "en",
            }
            data = client.fetch(base_url, params)
            items = data.get("data", [])

            for item in items:
                gifs.append({
                    "id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "type": item.get("type", "gif"),
                })

            if len(items) < CATEGORY_LIMIT:
                break

            offset += CATEGORY_LIMIT
            if len(gifs) >= CATEGORY_TARGET:
                break

        gifs = gifs[:CATEGORY_TARGET]  # Cap at target
        filename = f"{cat_id}.json"
        output_path = os.path.join(PROJECT_ROOT, filename)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(gifs, f, indent=2, ensure_ascii=False)

        print(f"  Wrote {len(gifs)} GIFs to {filename} (q={title})")

    print(f"Done! Wrote {len(categories)} category files")


def main():
    parser = argparse.ArgumentParser(description="Generate emoji.json, trending.json, and category GIFs from Giphy APIs")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--emoji-only", action="store_true", help="Generate only emoji.json")
    group.add_argument("--trending-only", action="store_true", help="Generate only trending.json")
    group.add_argument("--categories-only", action="store_true", help="Generate only category GIF files")
    args = parser.parse_args()

    client = GiphyApiClient()
    keys_count = len(client._keys)
    if keys_count > 1:
        print(f"Using {keys_count} API keys for rate limit handling")

    try:
        if args.trending_only:
            generate_trending(client)
        elif args.emoji_only:
            generate_emoji(client)
        elif args.categories_only:
            generate_categories(client)
        else:
            generate_emoji(client)
            print()
            generate_trending(client)
            print()
            generate_categories(client)
    except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
