# Shopify Crawl

A scraper for the [Shopify App Store](https://apps.shopify.com) — extracts app info, pricing, and reviews. Written in Python, wrapped with npm scripts so it can be installed and run with familiar `npm` commands.

## Requirements

- Python 3.9+
- Node.js + npm (only needed for the npm wrapper scripts)
- Google Chrome (used by Selenium for JS-rendered pages, unless `--no-selenium` is passed)

## Setup

```bash
npm run setup
```

This installs the Python dependencies listed in `requirements.txt` (`requests`, `beautifulsoup4`, `pandas`, `selenium`, `webdriver-manager`).

## Usage

### Interactive menu

```bash
npm start
```

### Scrape a single app

```bash
npm run scrape:url -- https://apps.shopify.com/some-app
```

### Scrape a batch of apps from a file (one URL per line)

```bash
npm run scrape:file -- path/to/urls.txt
```

### Crawl a single category page for app names/links

```bash
npm run crawl:category -- https://apps.shopify.com/categories/some-category/all
```

### Crawl every category listed in `categories.txt`

```bash
npm run crawl:categories
```

Extra flags (`--no-reviews`, `--max-review-pages`, `--delay`, `--no-selenium`, `-o/--output`) can be passed through after `--`, e.g.:

```bash
npm run scrape:url -- https://apps.shopify.com/some-app --no-reviews --no-selenium
```

Or call the script directly instead of through npm:

```bash
python shopifycrawl.py --help
```

## Output

Results are saved under the output directory (`output/` by default, override with `-o`):

- `<slug>_info.json` / `<slug>_info.csv` — app metadata
- `<slug>_reviews.json` / `<slug>_reviews.csv` — reviews
- `<slug>_complete.json` — combined info + reviews
- `category_<slug>.json` / `.csv` — category crawl results

## Files

- `shopifycrawl.py` — the scraper
- `categories.txt` — list of Shopify category URLs used by `npm run crawl:categories`
- `requirements.txt` — Python dependencies
- `package.json` — npm wrapper scripts
