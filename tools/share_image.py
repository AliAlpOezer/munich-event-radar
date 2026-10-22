"""Render tools/share-image.html to site/share.png, the page's link preview (1200 x 630).

Run after changing the HTML: python tools/share_image.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

TOOLS = Path(__file__).resolve().parent

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1200, "height": 630})
    page.goto((TOOLS / "share-image.html").as_uri())
    page.evaluate("document.fonts.ready")
    page.screenshot(path=TOOLS.parent / "site" / "share.png")
    browser.close()
