#!/usr/bin/env python3
"""
Scrape GIPHY browse categories and subcollections from giphy.com (no API keys).
Writes collections.json in the same shape as before: title, id, thumbnail, collections[].

Optionally scrape up to N GIFs per subcollection (search results) into:
  collection_gifs/{category_id}/{sub_id}.json

Requires: pip install -r requirements.txt
  Then (required once per machine / after upgrading playwright):
  python3 -m playwright install chromium

Usage:
  python3 scripts/scrape_collections.py
  python3 scripts/scrape_collections.py --output /path/to/collections.json

  # GIFs only (uses existing collections.json)
  python3 scripts/scrape_collections.py --collection-gifs
  python3 scripts/scrape_collections.py --collection-gifs --max-gifs 500 --gifs-dir collection_gifs

  # Metadata + GIFs in one run
  python3 scripts/scrape_collections.py --all
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
DEFAULT_GIFS_DIR = "collection_gifs"
DEFAULT_MAX_GIFS = 500

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


def launch_chromium(p, headless: bool = True):
    """
    Launch Chromium. If browser binaries are missing (common after pip upgrade), print fix and exit.
    """
    try:
        return p.chromium.launch(headless=headless)
    except Exception as e:
        err = str(e).lower()
        if "executable doesn't exist" in err or "browserType.launch" in err:
            print(
                "Playwright browser binaries are missing or do not match this Playwright version.\n"
                "Run this with the same Python you use for this script, then retry:\n\n"
                "  python3 -m playwright install chromium\n\n"
                "Or install all browsers:\n\n"
                "  python3 -m playwright install\n",
                file=sys.stderr,
            )
            sys.exit(1)
        raise


def media_url_to_i_giphy_webp(url: str) -> str:
    """Normalize Giphy media URLs to i.giphy.com/{id}.webp when possible."""
    if not url or "giphy.com" not in url:
        return url or ""
    m = re.search(r"/media/([^/?.]+)", url)
    if m:
        return f"https://i.giphy.com/{m.group(1)}.webp"
    return url


def media_id_and_type_from_giphy_url(href: str) -> tuple[str | None, str]:
    """
    Parse Giphy /gifs/... or /stickers/... URL tail; return (id, 'gif'|'sticker').
    IDs are the trailing segment after the last hyphen when the path has hyphens; else full tail.
    """
    path = urlparse(href).path.rstrip("/")
    for prefix, kind in (("/gifs/", "gif"), ("/stickers/", "sticker")):
        if prefix not in path:
            continue
        tail = path.split(prefix, 1)[-1]
        if not tail or "/" in tail:
            return None, ""
        if "-" in tail:
            gid = tail.rsplit("-", 1)[-1]
        else:
            gid = tail
        if len(gid) >= 8 and re.match(r"^[a-zA-Z0-9]+$", gid):
            return gid, kind
    return None, ""


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


def scrape_search_gifs_for_slug(
    page,
    search_slug: str,
    max_gifs: int,
    scroll_pause_ms: int = 1200,
    max_stall_rounds: int = 25,
) -> list[dict]:
    """
    Load giphy.com/search/{search_slug}, scroll to load results, collect up to max_gifs items.
    Each item: { "id", "title", "type" } matching emoji/trending JSON style.
    """
    url = f"https://giphy.com/search/{search_slug}"
    page.goto(url, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(2000)

    ordered: list[dict] = []
    seen: set[str] = set()
    stall = 0
    last_total = 0

    selectors = 'a[href*="/gifs/"], a[href*="/stickers/"]'

    while len(ordered) < max_gifs and stall < max_stall_rounds:
        for link in page.locator(selectors).all():
            if len(ordered) >= max_gifs:
                break
            href = link.get_attribute("href") or ""
            gid, kind = media_id_and_type_from_giphy_url(href)
            if not gid or gid in seen:
                continue
            title = (link.get_attribute("aria-label") or link.get_attribute("title") or "").strip()
            if not title:
                try:
                    img = link.locator("img").first
                    if img.count():
                        title = (img.get_attribute("alt") or "").strip()
                except Exception:
                    pass
            if not title:
                try:
                    title = (link.inner_text() or "").strip()
                except Exception:
                    title = ""
            seen.add(gid)
            ordered.append({"id": gid, "title": title[:500] if title else "", "type": kind})

        if len(ordered) == last_total:
            stall += 1
        else:
            stall = 0
        last_total = len(ordered)

        if len(ordered) >= max_gifs:
            break

        page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 1.5))")
        page.wait_for_timeout(scroll_pause_ms)

    return ordered[:max_gifs]


def run_collection_gifs_scraper(
    collections_path: str,
    gifs_dir: str,
    max_gifs: int,
    headless: bool = True,
    limit_subcollections: int | None = None,
) -> None:
    """Read collections.json and write one JSON file per subcollection under gifs_dir/{category_id}/."""
    if not os.path.isfile(collections_path):
        raise FileNotFoundError(collections_path)

    with open(collections_path, encoding="utf-8") as f:
        tree = json.load(f)

    os.makedirs(gifs_dir, exist_ok=True)

    tasks: list[tuple[str, str]] = []
    for cat in tree:
        cat_id = cat.get("id") or ""
        if not cat_id:
            continue
        for sub in cat.get("collections", []):
            sid = sub.get("id") or ""
            if sid:
                tasks.append((cat_id, sid))

    if limit_subcollections is not None:
        tasks = tasks[: max(0, limit_subcollections)]

    total = len(tasks)
    print(f"Scraping up to {max_gifs} GIFs per subcollection ({total} files) …")

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        for i, (cat_id, sub_id) in enumerate(tasks, 1):
            sub_dir = os.path.join(gifs_dir, cat_id)
            os.makedirs(sub_dir, exist_ok=True)
            out_path = os.path.join(sub_dir, f"{sub_id}.json")

            print(f"  [{i}/{total}] {cat_id}/{sub_id}.json")
            try:
                gifs = scrape_search_gifs_for_slug(page, sub_id, max_gifs=max_gifs)
            except Exception as e:
                print(f"    Warning: {e}", file=sys.stderr)
                gifs = []

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(gifs, f, indent=2, ensure_ascii=False)

        browser.close()

    print(f"Done! Wrote GIF lists under {os.path.abspath(gifs_dir)}")


def run_scraper(output_path: str, headless: bool = True) -> None:
    out: list[dict] = []

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=headless)
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
        help="Output JSON path for collections metadata",
    )
    parser.add_argument(
        "--collections-json",
        default=None,
        help="collections.json to use for --collection-gifs (default: same as -o)",
    )
    parser.add_argument(
        "--collection-gifs",
        action="store_true",
        help="Scrape GIF lists for every subcollection (requires collections.json)",
    )
    parser.add_argument(
        "--gifs-dir",
        default=os.path.join(PROJECT_ROOT, DEFAULT_GIFS_DIR),
        help=f"Directory for per-subcollection JSON (default: {DEFAULT_GIFS_DIR}/)",
    )
    parser.add_argument(
        "--max-gifs",
        type=int,
        default=DEFAULT_MAX_GIFS,
        help=f"Max GIFs per subcollection (default: {DEFAULT_MAX_GIFS})",
    )
    parser.add_argument(
        "--limit-subcollections",
        type=int,
        default=None,
        metavar="N",
        help="Only process first N subcollections (debug / smoke test)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run metadata scrape first, then --collection-gifs",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser with UI (debugging)",
    )
    args = parser.parse_args()
    headless = not args.headed
    collections_path = os.path.abspath(args.collections_json or args.output)

    try:
        if args.all:
            run_scraper(os.path.abspath(args.output), headless=headless)
            print()
            run_collection_gifs_scraper(
                collections_path=os.path.abspath(args.output),
                gifs_dir=os.path.abspath(args.gifs_dir),
                max_gifs=args.max_gifs,
                headless=headless,
                limit_subcollections=args.limit_subcollections,
            )
        elif args.collection_gifs:
            run_collection_gifs_scraper(
                collections_path=collections_path,
                gifs_dir=os.path.abspath(args.gifs_dir),
                max_gifs=args.max_gifs,
                headless=headless,
                limit_subcollections=args.limit_subcollections,
            )
        else:
            run_scraper(os.path.abspath(args.output), headless=headless)
    except (FileNotFoundError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
