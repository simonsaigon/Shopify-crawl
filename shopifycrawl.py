import requests
from bs4 import BeautifulSoup
import json
import time
import csv
import re
import pandas as pd
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import logging
import os
import argparse
import sys

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scraper.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class ShopifyAppScraper:
    """Scraper for Shopify App Store - extracts app info, pricing, and reviews."""

    BASE_URL = "https://apps.shopify.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    def __init__(self, use_selenium=True):
        """Initialize the scraper."""
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.use_selenium = use_selenium
        self.driver = None

        if use_selenium:
            self._init_selenium()

    def _init_selenium(self):
        """Initialize Selenium WebDriver."""
        try:
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument("--no-sandbox")
            chrome_options.add_argument("--disable-dev-shm-usage")
            chrome_options.add_argument("--disable-gpu")
            chrome_options.add_argument("--window-size=1920,1080")
            chrome_options.add_argument(
                f"user-agent={self.HEADERS['User-Agent']}"
            )

            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            logger.info("Selenium WebDriver initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Selenium: {e}")
            self.use_selenium = False

    def _is_session_dead(self, exc):
        """Return True if the exception indicates a dead/crashed browser session."""
        msg = str(exc).lower()
        return any(k in msg for k in (
            "invalid session id",
            "session deleted",
            "no such session",
            "unable to connect",
            "connection refused",
            "chrome not reachable",
            "failed to establish a new connection",
        ))

    def _get_page(self, url, _retries=2):
        """Fetch a page using requests or Selenium, with session-recovery on crash."""
        if self.use_selenium and self.driver:
            try:
                self.driver.get(url)
                time.sleep(3)
                return BeautifulSoup(self.driver.page_source, "lxml")
            except Exception as e:
                if self._is_session_dead(e) and _retries > 0:
                    logger.warning(f"Browser session dead ({e}). Reinitializing...")
                    self._restart_selenium()
                    return self._get_page(url, _retries=_retries - 1)
                logger.error(f"Selenium error for {url}: {e}")
                return None
        else:
            try:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                return BeautifulSoup(response.text, "lxml")
            except Exception as e:
                logger.error(f"Request error for {url}: {e}")
                return None

    def _restart_selenium(self):
        """Quit crashed driver and start a fresh one."""
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None
        time.sleep(2)
        self._init_selenium()
        logger.info("Browser restarted successfully.")

    # ------------------------------------------------------------------ #
    #                    TXT FILE INPUT HANDLER                           #
    # ------------------------------------------------------------------ #

    def load_urls_from_file(self, filepath):
        """
        Load app URLs from a text file.
        
        Supported formats in the txt file:
          - Full URL: https://apps.shopify.com/omnisend
          - Slug only: omnisend
          - Lines starting with # are treated as comments
          - Blank lines are skipped
        """
        urls = []

        if not os.path.exists(filepath):
            logger.error(f"File not found: {filepath}")
            print(f"\n❌ ERROR: File '{filepath}' not found!")
            print("Please make sure the file exists and the path is correct.")
            return urls

        with open(filepath, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue

                # Normalize the URL
                url = self._normalize_url(line)

                if url:
                    urls.append(url)
                    logger.info(f"Line {line_num}: Loaded URL -> {url}")
                else:
                    logger.warning(f"Line {line_num}: Skipped invalid entry -> '{line}'")

        logger.info(f"Loaded {len(urls)} app URLs from {filepath}")
        print(f"\n✅ Loaded {len(urls)} app URLs from '{filepath}'")
        return urls

    def _normalize_url(self, entry):
        """Normalize a URL or slug to a full Shopify app URL."""
        entry = entry.strip()

        # Already a full URL
        if entry.startswith("https://apps.shopify.com/"):
            return entry.rstrip("/")

        # URL without https
        if entry.startswith("apps.shopify.com/"):
            return f"https://{entry}".rstrip("/")

        # Just the slug (e.g., "omnisend" or "klaviyo-email-marketing")
        if re.match(r'^[\w-]+$', entry):
            return f"{self.BASE_URL}/{entry}"

        # Partial path like /apps/omnisend
        if entry.startswith("/apps/") or entry.startswith("/"):
            return f"{self.BASE_URL}{entry}".rstrip("/")

        logger.warning(f"Could not normalize entry: '{entry}'")
        return None

    # ------------------------------------------------------------------ #
    #                      BATCH SCRAPE FROM FILE                         #
    # ------------------------------------------------------------------ #

    def batch_scrape_from_file(self, filepath, output_dir="output",
                                scrape_reviews=True, max_review_pages=None,
                                delay_between_apps=5):
        """
        Read app URLs from a .txt file and scrape all of them.
        
        Args:
            filepath: Path to .txt file containing app URLs/slugs
            output_dir: Directory to save output files
            scrape_reviews: Whether to scrape reviews (True/False)
            max_review_pages: Limit review pages per app (None = all)
            delay_between_apps: Seconds to wait between scraping each app
        
        Returns:
            List of results for all apps
        """
        urls = self.load_urls_from_file(filepath)

        if not urls:
            print("No valid URLs found in the file. Exiting.")
            return []

        os.makedirs(output_dir, exist_ok=True)

        all_results = []
        failed_apps = []
        total = len(urls)

        # Print scrape plan
        print(f"\n{'='*60}")
        print(f"  BATCH SCRAPE PLAN")
        print(f"{'='*60}")
        print(f"  Input file    : {filepath}")
        print(f"  Total apps    : {total}")
        print(f"  Scrape reviews: {'Yes' if scrape_reviews else 'No'}")
        print(f"  Max review pg : {'Unlimited' if not max_review_pages else max_review_pages}")
        print(f"  Output dir    : {output_dir}")
        print(f"  Delay between : {delay_between_apps}s")
        print(f"{'='*60}\n")

        # Confirm before starting
        confirm = input("Proceed with scraping? (y/n): ").strip().lower()
        if confirm not in ('y', 'yes', ''):
            print("Scraping cancelled.")
            return []

        start_time = datetime.now()

        for idx, url in enumerate(urls, 1):
            slug = url.rstrip("/").split("/")[-1]
            app_output_dir = os.path.join(output_dir, slug)

            print(f"\n{'─'*60}")
            print(f"  [{idx}/{total}] Scraping: {slug}")
            print(f"  URL: {url}")
            print(f"{'─'*60}")

            try:
                if scrape_reviews:
                    result = self.full_scrape(
                        url,
                        output_dir=app_output_dir,
                        max_review_pages=max_review_pages
                    )
                else:
                    # Only scrape app info, no reviews
                    result = self._scrape_info_only(url, app_output_dir)

                if result:
                    all_results.append(result)
                    info = result.get("app_info", {})
                    reviews = result.get("reviews", [])
                    print(f"  ✅ Success: {info.get('name', slug)}")
                    print(f"     Rating: {info.get('rating', 'N/A')}")
                    print(f"     Reviews scraped: {len(reviews)}")
                else:
                    failed_apps.append({"url": url, "error": "No data returned"})
                    print(f"  ❌ Failed: No data returned")

            except Exception as e:
                failed_apps.append({"url": url, "error": str(e)})
                logger.error(f"Error scraping {url}: {e}", exc_info=True)
                print(f"  ❌ Error: {e}")

            # Progress update
            elapsed = (datetime.now() - start_time).total_seconds()
            avg_time = elapsed / idx
            remaining = avg_time * (total - idx)
            print(f"  ⏱  Progress: {idx}/{total} | "
                  f"Elapsed: {elapsed:.0f}s | "
                  f"Est. remaining: {remaining:.0f}s")

            # Delay between apps (skip for last one)
            if idx < total:
                print(f"  ⏳ Waiting {delay_between_apps}s before next app...")
                time.sleep(delay_between_apps)

        # ---- Final Summary ---- #
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        print(f"\n{'='*60}")
        print(f"  BATCH SCRAPE COMPLETE")
        print(f"{'='*60}")
        print(f"  Total apps     : {total}")
        print(f"  Successful     : {len(all_results)}")
        print(f"  Failed         : {len(failed_apps)}")
        print(f"  Total time     : {duration:.1f}s ({duration/60:.1f} min)")
        print(f"  Output dir     : {output_dir}")
        print(f"{'='*60}")

        # Save batch summary
        summary = {
            "scrape_started": start_time.isoformat(),
            "scrape_completed": end_time.isoformat(),
            "duration_seconds": duration,
            "total_apps": total,
            "successful": len(all_results),
            "failed": len(failed_apps),
            "input_file": filepath,
            "failed_apps": failed_apps,
            "apps_scraped": [
                {
                    "name": r.get("app_info", {}).get("name"),
                    "url": r.get("app_info", {}).get("url"),
                    "rating": r.get("app_info", {}).get("rating"),
                    "reviews_scraped": len(r.get("reviews", [])),
                }
                for r in all_results
            ]
        }
        self.save_to_json(
            summary,
            os.path.join(output_dir, "batch_summary.json")
        )

        # Save combined master CSV of all apps
        self._save_master_csv(all_results, output_dir)

        # Save failed apps list
        if failed_apps:
            failed_file = os.path.join(output_dir, "failed_apps.txt")
            with open(failed_file, "w") as f:
                for fa in failed_apps:
                    f.write(f"{fa['url']}  # Error: {fa['error']}\n")
            print(f"\n⚠️  Failed apps saved to: {failed_file}")

        return all_results

    def _scrape_info_only(self, app_url, output_dir):
        """Scrape only app info without reviews."""
        os.makedirs(output_dir, exist_ok=True)
        slug = app_url.rstrip("/").split("/")[-1]

        app_info = self.scrape_app_info(app_url)

        if app_info:
            self.save_to_json(
                app_info,
                os.path.join(output_dir, f"{slug}_info.json")
            )
            self.save_app_info_to_csv(
                app_info,
                os.path.join(output_dir, f"{slug}_info.csv")
            )

        return {
            "app_info": app_info,
            "reviews": [],
            "summary": {
                "total_reviews_scraped": 0,
                "scrape_completed_at": datetime.now().isoformat(),
            }
        }

    def _save_master_csv(self, all_results, output_dir):
        """Save a master CSV with summary of all scraped apps."""
        rows = []
        for result in all_results:
            info = result.get("app_info", {})
            reviews = result.get("reviews", [])
            row = {
                "name": info.get("name"),
                "url": info.get("url"),
                "developer": info.get("developer"),
                "rating": info.get("rating"),
                "total_reviews": info.get("total_reviews"),
                "reviews_scraped": len(reviews),
                "description_preview": (info.get("description") or "")[:200],
                "pricing_plans": len(info.get("pricing", [])),
                "launch_date": info.get("launch_date"),
                "languages": info.get("languages"),
            }
            
            for i, benefit in enumerate(info.get("key_benefits", []), start=1):
                if isinstance(benefit, dict):
                    row[f"benefit_{i}_title"] = benefit.get("title")
                    row[f"benefit_{i}_description"] = benefit.get("description")
                else:
                    row[f"benefit_{i}_description"] = benefit
                    
            row["categories"] = ", ".join(info.get("categories", []))
                
            for i, integration in enumerate(info.get("integrations", []), start=1):
                row[f"integration_{i}"] = integration
                    
            breakdown = info.get("review_breakdown", {})
            for star in range(5, 0, -1):
                row[f"stars_{star}"] = breakdown.get(f"stars_{star}")

            for i, plan in enumerate(info.get("pricing", []), start=1):
                for p_key, p_val in plan.items():
                    if isinstance(p_val, (list, dict)):
                        row[f"pricing_{i}_{p_key}"] = json.dumps(p_val, ensure_ascii=False)
                    else:
                        row[f"pricing_{i}_{p_key}"] = p_val
            rows.append(row)

        if rows:
            df = pd.DataFrame(rows)
            master_file = os.path.join(output_dir, "all_apps_summary.csv")
            df.to_csv(master_file, index=False, encoding="utf-8-sig")
            logger.info(f"Master summary saved to {master_file}")
            print(f"📊 Master summary CSV: {master_file}")

        # Also save ALL reviews from all apps into one CSV
        all_reviews = []
        for result in all_results:
            info = result.get("app_info", {})
            for review in result.get("reviews", []):
                review["app_name"] = info.get("name")
                review["app_url"] = info.get("url")
                all_reviews.append(review)

        if all_reviews:
            df_reviews = pd.DataFrame(all_reviews)
            reviews_file = os.path.join(output_dir, "all_reviews_combined.csv")
            df_reviews.to_csv(reviews_file, index=False, encoding="utf-8-sig")
            logger.info(f"Combined reviews saved to {reviews_file}")
            print(f"📊 Combined reviews CSV: {reviews_file} ({len(all_reviews)} reviews)")

    # ------------------------------------------------------------------ #
    #                     CATEGORY CRAWLER                                #
    # ------------------------------------------------------------------ #

    def crawl_category(self, category_url, output_dir="output", save_results=True):
        """
        Crawl all pages of a Shopify App Store category URL and collect
        all app names and links.

        Args:
            category_url: e.g. https://apps.shopify.com/categories/store-management-support-chat/all
            output_dir: Directory to save output files
            save_results: Whether to save CSV/JSON output

        Returns:
            List of dicts with 'name' and 'url' for each app found
        """
        logger.info(f"Crawling category: {category_url}")
        all_apps = []
        page = 1

        while True:
            page_url = category_url if page == 1 else f"{category_url}?page={page}"
            logger.info(f"Fetching category page {page}: {page_url}")

            soup = self._get_page(page_url)
            if not soup:
                logger.error(f"Failed to fetch category page {page}")
                break

            apps = self._extract_apps_from_category_page(soup)

            if not apps:
                logger.info(f"No apps found on page {page}. Done.")
                break

            # Deduplicate by URL within this page
            seen = {a["url"] for a in all_apps}
            new_apps = [a for a in apps if a["url"] not in seen]

            if not new_apps:
                logger.info("No new apps on this page. Done.")
                break

            all_apps.extend(new_apps)
            logger.info(f"Page {page}: +{len(new_apps)} apps. Total: {len(all_apps)}")
            print(f"  Page {page}: found {len(new_apps)} apps (total: {len(all_apps)})")

            if not self._has_next_page(soup):
                logger.info("No next page. Category crawl complete.")
                break

            page += 1
            time.sleep(2)

        logger.info(f"Category crawl complete. Total apps found: {len(all_apps)}")

        if save_results and all_apps:
            os.makedirs(output_dir, exist_ok=True)
            # Derive a safe filename from the category URL
            category_slug = category_url.rstrip("/").split("/categories/")[-1].replace("/", "_")
            json_path = os.path.join(output_dir, f"category_{category_slug}.json")
            csv_path = os.path.join(output_dir, f"category_{category_slug}.csv")

            self.save_to_json(all_apps, json_path)
            df = pd.DataFrame(all_apps)
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            logger.info(f"Category results saved: {json_path}, {csv_path}")
            print(f"\n  JSON: {json_path}")
            print(f"  CSV : {csv_path}")

        return all_apps

    def _extract_apps_from_category_page(self, soup):
        """Extract app name + URL from a category listing page."""
        apps = []

        # App cards link to /app-slug directly (not /categories/, /partners/, etc.)
        # Try multiple selectors for the card links
        card_selectors = [
            'a[href*="apps.shopify.com/"]:not([href*="/categories/"]):not([href*="/partners/"])',
            '[class*="app-card"] a[href]',
            '[class*="AppCard"] a[href]',
            '[class*="listing"] a[href]',
        ]

        seen_urls = set()

        for sel in card_selectors:
            links = soup.select(sel)
            for link in links:
                href = link.get("href", "")
                # Normalize to full URL
                if href.startswith("/"):
                    href = self.BASE_URL + href
                if not href.startswith(self.BASE_URL):
                    continue

                # Filter out non-app pages
                path = href.replace(self.BASE_URL, "").strip("/")
                parts = path.split("/")
                if not parts or parts[0] in ("categories", "partners", "reviews", ""):
                    continue
                # Skip URLs with query strings in the path check
                slug = parts[0].split("?")[0]
                if not slug or not re.match(r'^[\w-]+$', slug):
                    continue

                canonical = f"{self.BASE_URL}/{slug}"
                if canonical in seen_urls:
                    continue
                seen_urls.add(canonical)

                # Extract app name from the link element
                name = self._extract_app_name_from_card(link)
                apps.append({"name": name, "url": canonical})

            if apps:
                break

        return apps

    def _extract_app_name_from_card(self, link_el):
        """Best-effort extraction of app name from a card anchor element."""
        # Try heading inside the link
        for tag in ("h2", "h3", "h4", "[class*='name']", "[class*='title']"):
            el = link_el.select_one(tag)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return text

        # aria-label on the link itself
        aria = link_el.get("aria-label", "").strip()
        if aria:
            return aria

        # Fallback: visible text of the link
        text = link_el.get_text(strip=True)
        if text:
            return text

        return None

    def crawl_multiple_categories(self, category_urls, output_dir="output"):
        """
        Crawl multiple category URLs and collect all app names and links.

        Args:
            category_urls: List of category page URLs
            output_dir: Directory to save output files

        Returns:
            Dict mapping category_url -> list of {'name', 'url'} dicts
        """
        os.makedirs(output_dir, exist_ok=True)
        all_results = {}
        combined_apps = []
        total = len(category_urls)

        print(f"\n{'='*60}")
        print(f"  MULTI-CATEGORY CRAWL")
        print(f"{'='*60}")
        print(f"  Categories : {total}")
        print(f"  Output dir : {output_dir}")
        print(f"{'='*60}\n")

        for idx, url in enumerate(category_urls, 1):
            url = url.strip()
            if not url or url.startswith("#"):
                continue

            print(f"\n[{idx}/{total}] {url}")
            print(f"{'─'*60}")

            try:
                apps = self.crawl_category(url, output_dir=output_dir, save_results=True)
                all_results[url] = apps

                # Tag each app with its source category for the combined file
                for app in apps:
                    combined_apps.append({
                        "category_url": url,
                        "name": app["name"],
                        "url": app["url"],
                    })

                print(f"  ✅ {len(apps)} apps found")

            except Exception as e:
                logger.error(f"Error crawling category {url}: {e}", exc_info=True)
                print(f"  ❌ Error: {e}")
                all_results[url] = []

            if idx < total:
                time.sleep(2)

        # Save combined output across all categories
        if combined_apps:
            combined_json = os.path.join(output_dir, "all_categories_combined.json")
            combined_csv = os.path.join(output_dir, "all_categories_combined.csv")
            self.save_to_json(combined_apps, combined_json)
            pd.DataFrame(combined_apps).drop_duplicates(subset="url").to_csv(
                combined_csv, index=False, encoding="utf-8-sig"
            )
            print(f"\n{'='*60}")
            print(f"  COMBINED OUTPUT")
            print(f"  JSON : {combined_json}")
            print(f"  CSV  : {combined_csv}")
            print(f"  Total unique apps: {pd.DataFrame(combined_apps)['url'].nunique()}")
            print(f"{'='*60}")

        return all_results

    def load_category_urls_from_file(self, filepath):
        """Load category URLs from a text file (one URL per line, # = comment)."""
        urls = []
        if not os.path.exists(filepath):
            logger.error(f"File not found: {filepath}")
            print(f"\n❌ ERROR: File '{filepath}' not found!")
            return urls

        with open(filepath, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.startswith("http"):
                    logger.warning(f"Line {line_num}: Skipped (not a URL) -> '{line}'")
                    continue
                urls.append(line)
                logger.info(f"Line {line_num}: Loaded category URL -> {line}")

        print(f"✅ Loaded {len(urls)} category URLs from '{filepath}'")
        return urls

    # ------------------------------------------------------------------ #
    #                       APP INFORMATION                               #
    # ------------------------------------------------------------------ #

    def scrape_app_info(self, app_url):
        """Scrape all information about a Shopify app."""
        if not app_url.startswith("http"):
            app_url = f"{self.BASE_URL}/{app_url}"

        logger.info(f"Scraping app info from: {app_url}")
        soup = self._get_page(app_url)

        if not soup:
            logger.error("Failed to fetch app page.")
            return None

        app_data = {
            "url": app_url,
            "scraped_at": datetime.now().isoformat(),
        }

        app_data["name"] = self._extract_text(
            soup, 'h1[class*="name"], h1[class*="title"], h1'
        )
        app_data["tagline"] = self._extract_text(
            soup, '[class*="tagline"], [class*="subtitle"], [class*="hero"] p'
        )
        app_data["developer"] = self._extract_developer(soup)
        app_data["developer_url"] = self._extract_developer_url(soup)
        app_data["rating"] = self._extract_rating(soup)
        app_data["total_reviews"] = self._extract_total_reviews(soup)
        app_data["description"] = self._extract_description(soup)
        app_data["key_benefits"] = self._extract_key_benefits(soup)
        app_data["pricing"] = self._extract_pricing(soup)
        app_data["screenshots"] = self._extract_screenshots(soup)
        app_data["categories"] = self._extract_categories(soup)
        app_data["launch_date"] = self._extract_launch_date(soup)
        app_data["languages"] = self._extract_languages(soup)
        app_data["integrations"] = self._extract_integrations(soup)
        app_data["support"] = self._extract_support_info(soup)
        app_data["review_breakdown"] = self._extract_review_breakdown(soup)

        structured = self._extract_json_ld(soup)
        if structured:
            app_data["structured_data"] = structured
            if not app_data["name"] and structured.get("name"):
                app_data["name"] = structured["name"]
            if not app_data["rating"] and structured.get("aggregateRating"):
                app_data["rating"] = structured["aggregateRating"].get("ratingValue")
                app_data["total_reviews"] = structured["aggregateRating"].get("reviewCount")
            if not app_data["description"] and structured.get("description"):
                app_data["description"] = structured["description"]

        logger.info(f"Successfully scraped app: {app_data.get('name', 'Unknown')}")
        return app_data

    def _extract_text(self, soup, selector):
        try:
            el = soup.select_one(selector)
            return el.get_text(strip=True) if el else None
        except Exception:
            return None

    def _extract_developer(self, soup):
        selectors = [
            '[class*="developer"] a',
            '[class*="by-line"] a',
            '[class*="merchant-header"] a',
            'a[href*="/partners/"]',
        ]
        for sel in selectors:
            text = self._extract_text(soup, sel)
            if text:
                return text
        return None

    def _extract_developer_url(self, soup):
        selectors = [
            '[class*="developer"] a',
            'a[href*="/partners/"]',
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el and el.get("href"):
                href = el["href"]
                if href.startswith("/"):
                    return self.BASE_URL + href
                return href
        return None

    def _extract_rating(self, soup):
        selectors = [
            '[class*="rating"] [class*="value"]',
            '[class*="star-rating"]',
            '[class*="average-rating"]',
            '[aria-label*="rating"]',
            '[class*="review-rating"]',
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if not text:
                    text = el.get("aria-label", "")
                match = re.search(r'(\d+\.?\d*)', str(text))
                if match:
                    return float(match.group(1))
        return None

    def _extract_total_reviews(self, soup):
        # Try the Reviews heading with count in parentheses: <h2>Reviews <span>(514)</span></h2>
        for h2 in soup.select('h2'):
            if 'review' in h2.get_text(strip=True).lower():
                count_span = h2.select_one('span.tw-text-body-md, span:last-child')
                if count_span:
                    match = re.search(r'(\d[\d,]*)', count_span.get_text())
                    if match:
                        return int(match.group(1).replace(',', ''))
        selectors = [
            '[class*="review-count"]',
            '[class*="reviews-count"]',
            '[class*="rating-count"]',
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                match = re.search(r'(\d[\d,]*)', el.get_text())
                if match:
                    return int(match.group(1).replace(',', ''))
        text = soup.get_text()
        match = re.search(r'([\d,]+)\s+reviews?', text, re.IGNORECASE)
        if match:
            return int(match.group(1).replace(',', ''))
        return None

    def _extract_review_breakdown(self, soup):
        """Extract per-star review counts (5-star, 4-star, etc.) from review metrics."""
        breakdown = {}
        metrics_section = soup.select_one('.app-reviews-metrics, [class*="review-metrics"]')
        if not metrics_section:
            return breakdown
        items = metrics_section.select('ul li')
        for item in items:
            # Star number from the small label div
            star_div = item.select_one('div.tw-mr-2xs, [class*="mr-2xs"]')
            if not star_div:
                continue
            star_text = star_div.get_text(strip=True)
            if not star_text.isdigit():
                continue
            star = int(star_text)
            # Count from the aria-label like "388 total reviews"
            count_link = item.select_one('a[aria-label*="total reviews"]')
            if count_link:
                match = re.search(r'(\d[\d,]*)', count_link.get('aria-label', ''))
                if match:
                    breakdown[f"stars_{star}"] = int(match.group(1).replace(',', ''))
                    continue
            # Fallback: count from span text
            count_span = item.select_one('.link-block--underline, a span')
            if count_span:
                match = re.search(r'(\d[\d,]*)', count_span.get_text(strip=True))
                if match:
                    breakdown[f"stars_{star}"] = int(match.group(1).replace(',', ''))
        return breakdown

    def _extract_description(self, soup):
        selectors = [
            '#app-details p.lg\\:tw-block',
            '[data-truncate-app-details]',
            '[class*="app-description"]',
            '[class*="description"] [class*="body"]',
            '[data-merchant-description]',
            'section[class*="description"]',
            '[class*="listing-description"]',
            '[class*="app-details"] [class*="body"]',
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(separator="\n", strip=True)
        return None

    def _extract_key_benefits(self, soup):
        benefits = []
        selectors = [
            '#app-details ul li',
            '[class*="key-benefit"]',
            '[class*="feature-list"] li',
            '[class*="key-values"] li',
            '[class*="benefits"] li',
        ]
        for sel in selectors:
            items = soup.select(sel)
            if items:
                for item in items:
                    title_el = item.select_one('h3, h4, [class*="title"]')
                    desc_el = item.select_one('p, [class*="description"]')
                    benefit = {
                        "title": title_el.get_text(strip=True) if title_el else None,
                        "description": desc_el.get_text(strip=True) if desc_el else item.get_text(strip=True),
                    }
                    benefits.append(benefit)
                break
        return benefits

    def _extract_pricing(self, soup):
        pricing_plans = []
        plan_selectors = [
            '.app-details-pricing-plan-card',
            '[class*="pricing-card"]',
            '[class*="plan-card"]',
            '[class*="pricing"] [class*="plan"]',
            '[class*="price-plan"]',
            '[class*="pricing-section"] > div',
        ]
        for sel in plan_selectors:
            plans = soup.select(sel)
            if plans:
                for plan in plans:
                    plan_data = self._parse_pricing_plan(plan)
                    if plan_data.get("name") or plan_data.get("price"):
                        pricing_plans.append(plan_data)
                break
        if not pricing_plans:
            pricing_section = soup.select_one(
                '[class*="pricing"], [id*="pricing"]'
            )
            if pricing_section:
                pricing_plans.append({
                    "raw_text": pricing_section.get_text(separator="\n", strip=True)
                })
        free_indicators = soup.select('[class*="free"], [class*="price-free"]')
        for fi in free_indicators:
            if "free" in fi.get_text(strip=True).lower():
                if not pricing_plans:
                    pricing_plans.append({"name": "Free", "price": "Free"})
        return pricing_plans

    def _parse_pricing_plan(self, plan_el):
        plan = {}
        name_el = plan_el.select_one(
            '[data-test-id="name"], h3, h4, [class*="plan-name"], [class*="name"], [class*="title"]'
        )
        plan["name"] = name_el.get_text(strip=True) if name_el else None
        
        price_el = plan_el.select_one(
            '[data-test-id="price"], [data-pricing-component-target="cardHeadingPrice"] h3 span:first-child, [class*="price"], [class*="cost"], [class*="amount"]'
        )
        plan["price"] = price_el.get_text(strip=True) if price_el else None
        
        cycle_el = plan_el.select_one(
            '[data-pricing-component-target="cardHeadingPrice"] span:nth-child(2), [class*="interval"], [class*="cycle"], [class*="period"]'
        )
        plan["billing_cycle"] = cycle_el.get_text(strip=True) if cycle_el else None
        
        additional_charges_el = plan_el.select_one('[data-test-id="additional-charges"]')
        if additional_charges_el:
            plan["additional_charges"] = additional_charges_el.get_text(strip=True)
            
        features = []
        feature_items = plan_el.select('li, [class*="feature"]')
        for fi in feature_items:
            text = fi.get_text(strip=True)
            if text and text != plan.get("name") and text != plan.get("price"):
                features.append(text)
        plan["features"] = features
        
        trial_el = plan_el.select_one('[class*="trial"]')
        if not trial_el:
            for p in plan_el.select('p, span'):
                if 'free trial' in p.get_text(strip=True).lower():
                    trial_el = p
                    break
        plan["free_trial"] = trial_el.get_text(strip=True) if trial_el else None
        return plan

    def _extract_screenshots(self, soup):
        screenshots = []
        img_selectors = [
            '[class*="screenshot"] img',
            '[class*="gallery"] img',
            '[class*="carousel"] img',
            '[class*="media"] img',
            '[class*="slider"] img',
        ]
        for sel in img_selectors:
            imgs = soup.select(sel)
            if imgs:
                for img in imgs:
                    src = img.get("src") or img.get("data-src") or img.get("data-lazy")
                    if src:
                        screenshots.append(src)
                break
        return list(set(screenshots))

    def _extract_categories(self, soup):
        categories = []
        # Main categories are the first link inside each accordion wrapper
        wrappers = soup.select('[data-accordion-target="wrapper"]')
        if wrappers:
            for wrapper in wrappers:
                link = wrapper.select_one('a[href*="/categories/"]')
                if link:
                    text = link.get_text(strip=True)
                    if text and text not in categories:
                        categories.append(text)
        if not categories:
            # Fallback for pages without accordion structure
            cat_selectors = [
                'a[href*="/categories/"]',
                '[class*="category"] a',
                '[class*="breadcrumb"] a',
                '[class*="tag"]',
            ]
            for sel in cat_selectors:
                items = soup.select(sel)
                for item in items:
                    text = item.get_text(strip=True)
                    if text and text.lower() not in ['home', 'apps', 'shopify app store']:
                        categories.append(text)
                if categories:
                    break
        return categories

    def _extract_launch_date(self, soup):
        selectors = ['[class*="launch-date"]', '[class*="released"]']
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                return el.get_text(strip=True)
        text = soup.get_text()
        match = re.search(
            r'(?:launched?|released?)\s*:?\s*(\w+\s+\d{1,2},?\s+\d{4})',
            text, re.IGNORECASE
        )
        if match:
            return match.group(1)
        return None

    def _extract_languages(self, soup):
        lang_el = soup.select_one('[class*="languages"], [class*="language-list"]')
        if lang_el:
            return lang_el.get_text(separator=", ", strip=True)
        for p in soup.select('p, h2, h3'):
            if p.get_text(strip=True).lower() == 'languages':
                parent_grid = p.find_parent('div', class_=lambda c: c and 'tw-grid' in c)
                if parent_grid:
                    vals = parent_grid.select('div > p, div > span')
                    if vals:
                        return ", ".join(v.get_text(strip=True) for v in vals)
        return None

    def _extract_integrations(self, soup):
        integrations = []
        for p in soup.select('p, h2, h3'):
            if p.get_text(strip=True).lower() == 'works with':
                parent_grid = p.find_parent('div', class_=lambda c: c and 'tw-grid' in c)
                if parent_grid:
                    items = parent_grid.select('ul li')
                    if items:
                        for item in items:
                            text = item.get_text(strip=True).rstrip(',')
                            if text: integrations.append(text)
                        return integrations
        int_selectors = [
            '[class*="integration"] a',
            '[class*="works-with"] a',
            '[class*="compatible"] li',
        ]
        for sel in int_selectors:
            items = soup.select(sel)
            for item in items:
                text = item.get_text(strip=True)
                if text:
                    integrations.append(text)
            if integrations:
                break
        return integrations

    def _extract_support_info(self, soup):
        support = {}
        email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', soup.get_text())
        if email_match:
            support["email"] = email_match.group()
        support_link = soup.select_one('a[href*="support"], a[href*="help"]')
        if support_link:
            support["url"] = support_link.get("href")
        faq_link = soup.select_one('a[href*="faq"]')
        if faq_link:
            support["faq_url"] = faq_link.get("href")
        privacy_link = soup.select_one('a[href*="privacy"]')
        if privacy_link:
            support["privacy_policy"] = privacy_link.get("href")
        return support

    def _extract_json_ld(self, soup):
        scripts = soup.select('script[type="application/ld+json"]')
        for script in scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get("@type") in [
                    "SoftwareApplication", "Product", "WebApplication"
                ]:
                    return data
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") in [
                            "SoftwareApplication", "Product", "WebApplication"
                        ]:
                            return item
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    # ------------------------------------------------------------------ #
    #                           REVIEWS                                   #
    # ------------------------------------------------------------------ #

    def scrape_all_reviews(self, app_url, max_pages=None):
        """Scrape ALL reviews for an app."""
        if not app_url.startswith("http"):
            app_url = f"{self.BASE_URL}/{app_url}"

        reviews_url = app_url.rstrip("/") + "/reviews"
        all_reviews = []
        page = 1

        logger.info(f"Starting review scraping from: {reviews_url}")

        while True:
            if max_pages and page > max_pages:
                logger.info(f"Reached max pages limit: {max_pages}")
                break

            page_url = f"{reviews_url}?page={page}" if page > 1 else reviews_url
            logger.info(f"Scraping reviews page {page}: {page_url}")

            soup = self._get_page(page_url)
            if not soup:
                logger.error(f"Failed to fetch reviews page {page}")
                break

            reviews = self._extract_reviews_from_page(soup)

            if not reviews:
                logger.info(f"No more reviews found on page {page}. Done.")
                break

            all_reviews.extend(reviews)
            logger.info(
                f"Page {page}: Found {len(reviews)} reviews. "
                f"Total: {len(all_reviews)}"
            )

            if not self._has_next_page(soup):
                logger.info("No next page found. Done scraping reviews.")
                break

            page += 1
            time.sleep(2)

        logger.info(f"Total reviews scraped: {len(all_reviews)}")
        return all_reviews

    def _extract_reviews_from_page(self, soup):
        reviews = []
        review_selectors = [
            '[data-merchant-review]',
            '[class*="review-listing"]',
            '[class*="review-card"]',
            '[class*="review-item"]',
            '[class*="review"][class*="container"]',
            'div[class*="review"]:has([class*="star"])',
            '[data-review-id]',
        ]
        review_elements = []
        for sel in review_selectors:
            review_elements = soup.select(sel)
            if review_elements:
                break
        if not review_elements:
            review_elements = soup.select(
                '[class*="review"]:has(p):has([class*="rating"])'
            )
        for review_el in review_elements:
            review = self._parse_single_review(review_el)
            if review and (review.get("body") or review.get("rating")):
                reviews.append(review)
        return reviews

    def _parse_single_review(self, review_el):
        review = {}
        name_selectors = [
            '[class*="reviewer-name"]', '[class*="author"]',
            '[class*="merchant-name"]', '[class*="review-header"] a',
            '[class*="text-heading"] span', 'span[title]',
            'h3', 'h4',
        ]
        for sel in name_selectors:
            el = review_el.select_one(sel)
            if el:
                review["reviewer_name"] = el.get_text(strip=True)
                break
        store_selectors = [
            '[class*="store-name"]', '[class*="merchant-location"]',
            '[class*="reviewer-info"]',
        ]
        for sel in store_selectors:
            el = review_el.select_one(sel)
            if el:
                review["store_info"] = el.get_text(strip=True)
                break
        if not review.get("store_info"):
            heading_el = review_el.select_one('[class*="text-heading"]')
            if heading_el:
                sib = heading_el.find_next_sibling('div')
                if sib:
                    review["store_info"] = sib.get_text(strip=True)
        star_selectors = [
            '[class*="star-rating"]', '[class*="review-rating"]',
            '[aria-label*="star"]', '[class*="rating"]',
        ]
        for sel in star_selectors:
            el = review_el.select_one(sel)
            if el:
                aria = el.get("aria-label", "")
                match = re.search(r'(\d+)', aria)
                if match:
                    review["rating"] = int(match.group(1))
                    break
                filled_stars = el.select(
                    '[class*="filled"], [class*="full"], svg[class*="star--filled"]'
                )
                if filled_stars:
                    review["rating"] = len(filled_stars)
                    break
                text_match = re.search(r'(\d+)', el.get_text())
                if text_match:
                    review["rating"] = int(text_match.group(1))
                    break
        date_selectors = [
            '[class*="review-date"]', '[class*="date"]',
            'time', '[datetime]',
        ]
        for sel in date_selectors:
            el = review_el.select_one(sel)
            if el:
                review["date"] = el.get("datetime") or el.get_text(strip=True)
                break
        if not review.get("date"):
            date_el = review_el.select_one('.tw-justify-between .tw-text-fg-tertiary')
            if date_el:
                review["date"] = date_el.get_text(strip=True)
        body_selectors = [
            '[data-truncate-content-copy]',
            '[class*="review-content"]', '[class*="review-body"]',
            '[class*="review-message"]', '[class*="review"] p', 'p',
        ]
        for sel in body_selectors:
            els = review_el.select(sel)
            if els:
                body_parts = [e.get_text(strip=True) for e in els if e.get_text(strip=True)]
                if body_parts:
                    review["body"] = "\n".join(body_parts)
                    break
        reply_selectors = [
            '[class*="reply"]', '[class*="developer-reply"]',
            '[class*="response"]',
        ]
        for sel in reply_selectors:
            el = review_el.select_one(sel)
            if el:
                review["developer_reply"] = el.get_text(strip=True)
                break
        usage_selectors = [
            '[class*="time-using"]', '[class*="usage"]',
        ]
        for sel in usage_selectors:
            el = review_el.select_one(sel)
            if el:
                review["time_using_app"] = el.get_text(strip=True)
                break
        if not review.get("time_using_app"):
            heading_el = review_el.select_one('[class*="text-heading"]')
            if heading_el:
                sibs = heading_el.find_next_siblings('div')
                if len(sibs) >= 2:
                    review["time_using_app"] = sibs[1].get_text(strip=True)
        edited_el = review_el.select_one('[class*="edited"]')
        review["edited"] = bool(edited_el)
        return review

    def _has_next_page(self, soup):
        next_selectors = [
            'a[rel="next"]', '[class*="next"] a',
            'a[aria-label="Next"]', '[class*="pagination"] a:last-child',
            'button[class*="next"]',
        ]
        for sel in next_selectors:
            el = soup.select_one(sel)
            if el:
                if 'disabled' not in el.get('class', []) and \
                   not el.get('disabled') and \
                   not el.get('aria-disabled') == 'true':
                    return True
        return False

    # ------------------------------------------------------------------ #
    #                         EXPORT METHODS                              #
    # ------------------------------------------------------------------ #

    def save_to_json(self, data, filename):
        os.makedirs(os.path.dirname(filename) or '.', exist_ok=True)
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"Data saved to {filename}")

    def save_reviews_to_csv(self, reviews, filename):
        if not reviews:
            logger.warning("No reviews to save.")
            return
        os.makedirs(os.path.dirname(filename) or '.', exist_ok=True)
        df = pd.DataFrame(reviews)
        df.to_csv(filename, index=False, encoding="utf-8-sig")
        logger.info(f"Reviews saved to {filename} ({len(reviews)} reviews)")

    def save_app_info_to_csv(self, app_data, filename):
        os.makedirs(os.path.dirname(filename) or '.', exist_ok=True)
        flat_data = {}
        for key, value in app_data.items():
            if key == "pricing" and isinstance(value, list):
                for i, plan in enumerate(value, start=1):
                    for p_key, p_val in plan.items():
                        if isinstance(p_val, (list, dict)):
                            flat_data[f"pricing_{i}_{p_key}"] = json.dumps(p_val, ensure_ascii=False)
                        else:
                            flat_data[f"pricing_{i}_{p_key}"] = p_val
                continue
            
            if key == "key_benefits" and isinstance(value, list):
                for i, benefit in enumerate(value, start=1):
                    if isinstance(benefit, dict):
                        for b_key, b_val in benefit.items():
                            flat_data[f"benefit_{i}_{b_key}"] = b_val
                    else:
                        flat_data[f"benefit_{i}_description"] = benefit
                continue
                
            if key == "categories" and isinstance(value, list):
                flat_data["categories"] = ", ".join(value)
                continue
                
            if key == "review_breakdown" and isinstance(value, dict):
                for star in range(5, 0, -1):
                    flat_data[f"stars_{star}"] = value.get(f"stars_{star}")
                continue

            if key == "integrations" and isinstance(value, list):
                for i, item in enumerate(value, start=1):
                    flat_data[f"integration_{i}"] = item
                continue
                
            if isinstance(value, (list, dict)):
                flat_data[key] = json.dumps(value, ensure_ascii=False)
            else:
                flat_data[key] = value
        df = pd.DataFrame([flat_data])
        df.to_csv(filename, index=False, encoding="utf-8-sig")
        logger.info(f"App info saved to {filename}")

    # ------------------------------------------------------------------ #
    #                      FULL SCRAPE PIPELINE                           #
    # ------------------------------------------------------------------ #

    def full_scrape(self, app_url, output_dir="output", max_review_pages=None):
        """Complete scrape: app info + all reviews."""
        os.makedirs(output_dir, exist_ok=True)
        slug = app_url.rstrip("/").split("/")[-1]

        logger.info(f"FULL SCRAPE: {app_url}")

        # 1. Scrape app information
        app_info = self.scrape_app_info(app_url)
        if app_info:
            self.save_to_json(
                app_info, os.path.join(output_dir, f"{slug}_info.json")
            )
            self.save_app_info_to_csv(
                app_info, os.path.join(output_dir, f"{slug}_info.csv")
            )

        # 2. Scrape all reviews
        reviews = self.scrape_all_reviews(app_url, max_pages=max_review_pages)
        if reviews:
            self.save_to_json(
                reviews, os.path.join(output_dir, f"{slug}_reviews.json")
            )
            self.save_reviews_to_csv(
                reviews, os.path.join(output_dir, f"{slug}_reviews.csv")
            )

        # 3. Combined output
        combined = {
            "app_info": app_info,
            "reviews": reviews,
            "summary": {
                "total_reviews_scraped": len(reviews),
                "scrape_completed_at": datetime.now().isoformat(),
            }
        }
        self.save_to_json(
            combined, os.path.join(output_dir, f"{slug}_complete.json")
        )

        return combined

    def close(self):
        """Close the scraper and clean up."""
        if self.driver:
            self.driver.quit()
            logger.info("WebDriver closed.")


# ====================================================================== #
#                        INTERACTIVE MENU                                 #
# ====================================================================== #

def interactive_menu():
    """Interactive menu for the scraper."""
    print("""
╔══════════════════════════════════════════════════════╗
║        SHOPIFY APP STORE SCRAPER v2.0                ║
║        Supports batch scraping from TXT file         ║
╚══════════════════════════════════════════════════════╝
    """)

    print("Choose an option:")
    print("  1. Scrape from TXT file (batch mode)")
    print("  2. Scrape a single app URL")
    print("  3. Scrape single app (info only, no reviews)")
    print("  4. Crawl category URL (collect all app names & links)")
    print("  5. Crawl multiple category URLs from TXT file")
    print("  6. Exit")

    choice = input("\nEnter choice (1-6): ").strip()

    scraper = None

    try:
        if choice == "1":
            # ---- Batch mode from TXT file ---- #
            print("\n--- BATCH SCRAPE FROM TXT FILE ---")
            filepath = input("Enter path to .txt file: ").strip()

            if not filepath:
                filepath = "apps.txt"
                print(f"Using default: {filepath}")

            output_dir = input("Output directory [output]: ").strip() or "output"

            scrape_reviews = input("Scrape reviews? (y/n) [y]: ").strip().lower()
            scrape_reviews = scrape_reviews != 'n'

            max_pages = input("Max review pages per app (blank=all): ").strip()
            max_pages = int(max_pages) if max_pages else None

            delay = input("Delay between apps in seconds [5]: ").strip()
            delay = int(delay) if delay else 5

            use_selenium = input("Use Selenium browser? (y/n) [y]: ").strip().lower()
            use_selenium = use_selenium != 'n'

            scraper = ShopifyAppScraper(use_selenium=use_selenium)
            scraper.batch_scrape_from_file(
                filepath=filepath,
                output_dir=output_dir,
                scrape_reviews=scrape_reviews,
                max_review_pages=max_pages,
                delay_between_apps=delay
            )

        elif choice == "2":
            # ---- Single app full scrape ---- #
            print("\n--- SINGLE APP FULL SCRAPE ---")
            app_url = input("Enter Shopify app URL or slug: ").strip()

            if not app_url:
                print("No URL provided. Exiting.")
                return

            output_dir = input("Output directory [output]: ").strip() or "output"
            max_pages = input("Max review pages (blank=all): ").strip()
            max_pages = int(max_pages) if max_pages else None

            scraper = ShopifyAppScraper(use_selenium=True)

            if not app_url.startswith("http"):
                app_url = f"https://apps.shopify.com/{app_url}"

            slug = app_url.rstrip("/").split("/")[-1]
            result = scraper.full_scrape(
                app_url,
                output_dir=os.path.join(output_dir, slug),
                max_review_pages=max_pages
            )

            info = result.get("app_info", {})
            print(f"\n✅ Done!")
            print(f"   App: {info.get('name')}")
            print(f"   Rating: {info.get('rating')}")
            print(f"   Reviews scraped: {len(result.get('reviews', []))}")

        elif choice == "3":
            # ---- Single app info only ---- #
            print("\n--- SINGLE APP (INFO ONLY) ---")
            app_url = input("Enter Shopify app URL or slug: ").strip()

            if not app_url:
                print("No URL provided. Exiting.")
                return

            scraper = ShopifyAppScraper(use_selenium=True)

            if not app_url.startswith("http"):
                app_url = f"https://apps.shopify.com/{app_url}"

            app_info = scraper.scrape_app_info(app_url)

            if app_info:
                slug = app_url.rstrip("/").split("/")[-1]
                scraper.save_to_json(app_info, f"output/{slug}_info.json")
                scraper.save_app_info_to_csv(app_info, f"output/{slug}_info.csv")

                print(f"\n✅ Done!")
                print(f"   App: {app_info.get('name')}")
                print(f"   Developer: {app_info.get('developer')}")
                print(f"   Rating: {app_info.get('rating')}")
                print(f"   Reviews: {app_info.get('total_reviews')}")
                print(f"   Description: {(app_info.get('description') or '')[:150]}...")
            else:
                print("❌ Failed to scrape app info.")

        elif choice == "4":
            # ---- Category crawler ---- #
            print("\n--- CRAWL CATEGORY (collect app names & links) ---")
            category_url = input("Enter category URL: ").strip()

            if not category_url:
                print("No URL provided. Exiting.")
                return

            output_dir = input("Output directory [output]: ").strip() or "output"

            use_selenium = input("Use Selenium browser? (y/n) [y]: ").strip().lower()
            use_selenium = use_selenium != 'n'

            scraper = ShopifyAppScraper(use_selenium=use_selenium)

            print(f"\nCrawling: {category_url}")
            apps = scraper.crawl_category(category_url, output_dir=output_dir)

            print(f"\n✅ Done! Found {len(apps)} apps.")
            if apps:
                print("\nFirst 10 apps:")
                for app in apps[:10]:
                    print(f"  {app['name']}  ->  {app['url']}")
                if len(apps) > 10:
                    print(f"  ... and {len(apps) - 10} more (see output files)")

        elif choice == "5":
            # ---- Multi-category crawl from TXT file ---- #
            print("\n--- CRAWL MULTIPLE CATEGORIES FROM TXT FILE ---")
            filepath = input("Enter path to .txt file (one category URL per line): ").strip()

            if not filepath:
                print("No file provided. Exiting.")
                return

            output_dir = input("Output directory [output]: ").strip() or "output"

            use_selenium = input("Use Selenium browser? (y/n) [y]: ").strip().lower()
            use_selenium = use_selenium != 'n'

            scraper = ShopifyAppScraper(use_selenium=use_selenium)
            category_urls = scraper.load_category_urls_from_file(filepath)

            if not category_urls:
                print("No valid category URLs found. Exiting.")
                return

            results = scraper.crawl_multiple_categories(category_urls, output_dir=output_dir)

            total_apps = sum(len(v) for v in results.values())
            print(f"\n✅ Done! Crawled {len(results)} categories, {total_apps} total app entries.")

        elif choice == "6":
            print("Goodbye!")
            return

        else:
            print("Invalid choice.")

    except KeyboardInterrupt:
        print("\n\nScraping interrupted by user.")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        print(f"\n❌ Error: {e}")
    finally:
        if scraper:
            scraper.close()


# ====================================================================== #
#                      COMMAND LINE INTERFACE                             #
# ====================================================================== #

def main():
    """Main entry point with CLI argument support."""
    parser = argparse.ArgumentParser(
        description="Shopify App Store Scraper - Scrape app info and reviews"
    )
    parser.add_argument(
        "-f", "--file",
        help="Path to .txt file containing app URLs (one per line)"
    )
    parser.add_argument(
        "-u", "--url",
        help="Single app URL or slug to scrape"
    )
    parser.add_argument(
        "-o", "--output",
        default="output",
        help="Output directory (default: output)"
    )
    parser.add_argument(
        "--no-reviews",
        action="store_true",
        help="Skip scraping reviews (info only)"
    )
    parser.add_argument(
        "--max-review-pages",
        type=int,
        default=None,
        help="Max pages of reviews to scrape per app"
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=5,
        help="Delay in seconds between scraping apps (default: 5)"
    )
    parser.add_argument(
        "--no-selenium",
        action="store_true",
        help="Use requests instead of Selenium"
    )
    parser.add_argument(
        "-c", "--category",
        help="Category URL to crawl for all app names and links "
             "(e.g. https://apps.shopify.com/categories/store-management-support-chat/all)"
    )
    parser.add_argument(
        "--category-file",
        help="Path to .txt file with category URLs (one per line) to crawl in batch"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Launch interactive menu"
    )

    args = parser.parse_args()

    # If no arguments provided, launch interactive menu
    if len(sys.argv) == 1 or args.interactive:
        interactive_menu()
        return

    # CLI mode
    use_selenium = not args.no_selenium
    scraper = ShopifyAppScraper(use_selenium=use_selenium)

    try:
        if args.category_file:
            # Multi-category crawl from file
            category_urls = scraper.load_category_urls_from_file(args.category_file)
            if category_urls:
                results = scraper.crawl_multiple_categories(category_urls, output_dir=args.output)
                total = sum(len(v) for v in results.values())
                print(f"\n✅ Multi-category crawl complete. {len(results)} categories, {total} app entries.")

        elif args.category:
            # Single category crawl mode
            apps = scraper.crawl_category(
                category_url=args.category,
                output_dir=args.output,
            )
            print(f"\n✅ Category crawl complete. Found {len(apps)} apps.")
            for app in apps:
                print(f"  {app['name']}  ->  {app['url']}")

        elif args.file:
            # Batch mode from file
            scraper.batch_scrape_from_file(
                filepath=args.file,
                output_dir=args.output,
                scrape_reviews=not args.no_reviews,
                max_review_pages=args.max_review_pages,
                delay_between_apps=args.delay
            )

        elif args.url:
            # Single app mode
            url = args.url
            if not url.startswith("http"):
                url = f"https://apps.shopify.com/{url}"

            slug = url.rstrip("/").split("/")[-1]
            app_dir = os.path.join(args.output, slug)

            if args.no_reviews:
                app_info = scraper.scrape_app_info(url)
                if app_info:
                    os.makedirs(app_dir, exist_ok=True)
                    scraper.save_to_json(
                        app_info, os.path.join(app_dir, f"{slug}_info.json")
                    )
                    print(f"✅ App info saved for: {app_info.get('name')}")
            else:
                result = scraper.full_scrape(
                    url, output_dir=app_dir,
                    max_review_pages=args.max_review_pages
                )
                info = result.get("app_info", {})
                print(f"✅ Full scrape complete: {info.get('name')}")
                print(f"   Reviews: {len(result.get('reviews', []))}")
        else:
            print("Please provide --file or --url argument. Use --help for usage.")

    except KeyboardInterrupt:
        print("\nScraping interrupted.")
    finally:
        scraper.close()


if __name__ == "__main__":
    main()