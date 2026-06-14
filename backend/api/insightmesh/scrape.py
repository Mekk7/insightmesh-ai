# backend/api/insightmesh/scrape.py
"""
Reserved for shared scraping orchestration helpers.

Currently the per-platform scrapers live as endpoints under
backend/api/endpoints/ (scrape_reviews.py for YouTube, reddit_scraper.py for
Reddit), and the dispatching logic lives in plugins.py.

If we add more scrapers (Twitter/X, App Store, Play Store, Trustpilot) and
the dispatching grows complex, common pieces (rate-limit logic, retry, common
filtering) can live here.
"""
