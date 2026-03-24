#!/usr/bin/env python3
"""
Scrape GIPHY browse categories and subcollections from giphy.com (no API keys).
Writes collections.json in the same shape as before: title, id, thumbnail, collections[].

Requires: pip install -r requirements.txt && playwright install chromium

Usage:
  python3 scripts/scrape_collections.py
  python3 scripts/scrape_collections.py --output /path/to/collections.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.parse import unquote, urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(SCRIPT_DIR, "..")
COLLECTIONS_FILE = "collections.json"

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print(
        "Playwright is required. Install with:\n"
        "  python3 -m pip install playwright\n"
        "  python3 -m playwright install chromium",
        file=sys.stderr,
    )
    sys.exit(1)


def media_url_to_i_giphy_webp(url: str) -> str:
    """Normalize Giphy media URLs to i.giphy.com/{id}.webp when possible."""
    if not url or "giphy.com" not in url:
        return url or ""
    m = re.search(r"/media/([^/?.]+)", url)
    if m:
        return f"https://i.giphy.com/{m.group(1)}.webp"
    return url


def parse_search_slug(href: str) -> str | None:
    if not href:
        return None
    path = urlparse(href).path
    m = re.search(r"/search/([^/?#]+)", path)
    if not m:
        return None
    return unquote(m.group(1))


def scrape_category_slugs(page) -> list[tuple[str, str]]:
    """Return [(slug, display_title), ...] from /categories index."""
    page.goto("https://giphy.com/categories", wait_until="domcontentloaded", timeout=90000)
    try:
        page.wait_for_selector('a[href*="/categories/"]', timeout=60000)
    except PlaywrightTimeout:
        pass

    seen: dict[str, str] = {}
    for link in page.locator('a[href*="/categories/"]').all():
        href = link.get_attribute("href") or ""
        path = urlparse(href).path.strip("/")
        parts = path.split("/")
        if len(parts) < 2 or parts[0] != "categories":
            continue
        slug = parts[1]
        if not slug or slug.startswith("["):
            continue
        text = (link.inner_text() or "").strip()
        if slug not in seen:
            seen[slug] = text or slug.replace("-", " ").title()

    return sorted(seen.items(), key=lambda x: x[0].lower())


def scrape_category_page(page, slug: str) -> tuple[str, str, list[dict]]:
    """
    Visit /categories/{slug}. Returns (title, category_thumbnail_url, subcollections).
    """
    url = f"https://giphy.com/categories/{slug}"
    page.goto(url, wait_until="domcontentloaded", timeout=90000)

    try:
        page.wait_for_selector('a[href*="/search/"]', timeout=60000)
    except PlaywrightTimeout:
        pass

    # Title: first h1 or fallback from slug
    title_el = page.locator("h1").first
    title = slug.replace("-", " ").title()
    try:
        if title_el.count():
            t = title_el.inner_text().strip()
            if t:
                title = t
    except Exception:
        pass

    # Category hero thumbnail: first meaningful img in main (often above sub-grid)
    cat_thumb = ""
    for sel in ("main img[src*='giphy.com']", "main picture img", "[data-testid] img"):
        try:
            img = page.locator(sel).first
            if img.count():
                src = img.get_attribute("src") or ""
                if src and "giphy" in src:
                    cat_thumb = media_url_to_i_giphy_webp(src)
                    break
        except Exception:
            continue

    # Subcollections: unique /search/{slug} links with optional img
    subs_map: dict[str, dict] = {}
    for link in page.locator('a[href*="/search/"]').all():
        href = link.get_attribute("href") or ""
        sub_id = parse_search_slug(href)
        if not sub_id:
            continue

        label = (link.inner_text() or "").strip()
        thumb = ""
        try:
            img = link.locator("img").first
            if img.count():
                src = img.get_attribute("src") or ""
                if src:
                    thumb = media_url_to_i_giphy_webp(src)
        except Exception:
            pass

        if sub_id not in subs_map:
            subs_map[sub_id] = {
                "title": label or sub_id.replace("-", " "),
                "id": sub_id,
                "thumbnail": thumb,
            }
        else:
            if label and not subs_map[sub_id]["title"]:
                subs_map[sub_id]["title"] = label
            if thumb and not subs_map[sub_id]["thumbnail"]:
                subs_map[sub_id]["thumbnail"] = thumb

    subcollections = sorted(subs_map.values(), key=lambda x: x["id"].lower())

    if not cat_thumb and subcollections:
        cat_thumb = subcollections[0].get("thumbnail") or ""

    return title, cat_thumb, subcollections


def run_scraper(output_path: str, headless: bool = True) -> None:
    out: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        print("Loading giphy.com/categories …")
        categories = scrape_category_slugs(page)
        print(f"Found {len(categories)} top-level categories")

        for i, (slug, _hint) in enumerate(categories, 1):
            print(f"  [{i}/{len(categories)}] /categories/{slug}")
            try:
                title, cat_thumb, subs = scrape_category_page(page, slug)
            except Exception as e:
                print(f"    Warning: {e}", file=sys.stderr)
                title = slug.replace("-", " ").title()
                cat_thumb, subs = "", []

            out.append({
                "title": title,
                "id": slug,
                "thumbnail": cat_thumb,
                "collections": subs,
            })

        browser.close()

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    n_sub = sum(len(x["collections"]) for x in out)
    print(f"Done! Wrote {len(out)} categories, {n_sub} subcollections → {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Scrape Giphy browse categories into collections.json (no API)")
    parser.add_argument(
        "-o",
        "--output",
        default=os.path.join(PROJECT_ROOT, COLLECTIONS_FILE),
        help="Output JSON path",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser with UI (debugging)",
    )
    args = parser.parse_args()
    output_path = os.path.abspath(args.output)
    run_scraper(output_path, headless=not args.headed)


if __name__ == "__main__":
    main()
