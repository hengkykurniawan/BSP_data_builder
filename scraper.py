import os
import sys
import re
import time
import sqlite3
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.bps.go.id"
DB_NAME = "bps_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Create metadata table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS table_metadata (
            id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            subject_id TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    print(f"Database initialized: {DB_NAME}")

def save_metadata(table_id, title, url, subject_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO table_metadata (id, title, url, subject_id)
        VALUES (?, ?, ?, ?)
    """, (table_id, title, url, subject_id))
    conn.commit()
    conn.close()

def clean_table_name(name):
    # Remove special characters to make it a valid SQL table name
    clean = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    # Remove leading numbers or double underscores
    clean = re.sub(r'^_+', '', clean)
    clean = re.sub(r'_+', '_', clean)
    if not clean or clean[0].isdigit():
        clean = "table_" + clean
    return clean[:64].lower()

def store_table_data(table_name, df):
    conn = sqlite3.connect(DB_NAME)
    # Store the dataframe into sqlite table
    # Convert all columns to string to avoid schema errors, as BPS tables can be complex
    df_str = df.astype(str)
    df_str.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.close()
    print(f"Stored table: {table_name} ({len(df)} rows)")

def extract_table_id_from_url(url):
    # Extract unique part of the URL as the table ID
    # E.g., /id/statistics-table/2/MTA0OCM0/cpi-inflation.html -> MTA0OCM0
    match = re.search(r'/statistics-table/\d+/([^/]+)', url)
    if match:
        return match.group(1)
    # Fallback to hash of url
    import hashlib
    return hashlib.md5(url.encode('utf-8')).hexdigest()[:8]

def scrape_subject(subject_id, browser_type="chromium"):
    print(f"Starting scrape for subject: {subject_id} using {browser_type}")
    
    with sync_playwright() as p:
        # Select browser
        if browser_type == "firefox":
            browser = p.firefox.launch(headless=True)
        elif browser_type == "webkit":
            browser = p.webkit.launch(headless=True)
        else:
            browser = p.chromium.launch(headless=True)
            
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        
        page = context.new_page()
        
        # Navigate to the subject tables list
        url = f"{BASE_URL}/id/statistics-table?subject={subject_id}"
        print(f"Navigating to {url}...")
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"Navigation timed out: {e}. Trying to proceed anyway...")
            
        # Let's wait for table container or links to load
        page.wait_for_timeout(5000)
        
        # Get list of tables
        tables_to_scrape = []
        
        # We will scan the page for all table links
        # Let's iterate pages if pagination exists
        page_num = 1
        while True:
            print(f"Scanning page {page_num} for table links...")
            
            # Find all links containing statistics-table
            links = page.query_selector_all("a")
            for link in links:
                href = link.get_attribute("href") or ""
                text = link.inner_text().strip()
                
                # Check if it matches the pattern of a table detail page
                if "/statistics-table/" in href and "?subject=" not in href and text:
                    full_href = href if href.startswith("http") else BASE_URL + href
                    if full_href not in [t["url"] for t in tables_to_scrape]:
                        tables_to_scrape.append({
                            "title": text,
                            "url": full_href
                        })
            
            print(f"Found {len(tables_to_scrape)} table links so far.")
            
            # Look for the "Next" pagination button/link
            # Common selectors for Next button: text "Berikutnya", "Next", or page-link with chevron icon
            next_button = None
            for selector in [
                "a:has-text('Berikutnya')", 
                "a:has-text('Next')", 
                "ul.pagination li.next a", 
                "ul.pagination li:last-child a",
                "a.page-link[aria-label='Next']"
            ]:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible() and btn.is_enabled():
                        # Make sure it's not the current active page or disabled
                        parent = btn.evaluate_handle("el => el.parentElement")
                        parent_class = parent.evaluate("el => el.className") if parent else ""
                        if "disabled" not in parent_class:
                            next_button = btn
                            break
                except Exception:
                    pass
            
            if next_button:
                print("Clicking 'Next' page button...")
                try:
                    next_button.click()
                    page.wait_for_timeout(5000)
                    page_num += 1
                except Exception as e:
                    print(f"Failed to click next button: {e}")
                    break
            else:
                print("No more pages found or 'Next' button is disabled.")
                break
                
        print(f"Total tables found to scrape: {len(tables_to_scrape)}")
        
        # Scrape each table
        for idx, table_info in enumerate(tables_to_scrape, 1):
            title = table_info["title"]
            table_url = table_info["url"]
            table_id = extract_table_id_from_url(table_url)
            print(f"\n[{idx}/{len(tables_to_scrape)}] Scraping table: {title}")
            print(f"URL: {table_url}")
            
            save_metadata(table_id, title, table_url, subject_id)
            
            try:
                page.goto(table_url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(5000)
                
                # Check for the <table> element in DOM
                # BPS detail pages render a main table
                # We can grab the HTML content of the main table or the container
                table_element = page.query_selector("table")
                if not table_element:
                    print("No table element found in the DOM. Trying to wait longer...")
                    page.wait_for_timeout(5000)
                    table_element = page.query_selector("table")
                
                if table_element:
                    html_content = table_element.evaluate("el => el.outerHTML")
                    
                    # Parse table HTML using pandas
                    dfs = pd.read_html(html_content)
                    if dfs:
                        df = dfs[0]
                        # Clean headers: flatten multi-index if present, remove NaN headers
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = ['_'.join(col).strip() for col in df.columns.values]
                        
                        # Clean column names
                        df.columns = [clean_table_name(str(c)) for c in df.columns]
                        
                        db_table_name = clean_table_name(f"data_{table_id}_{title[:30]}")
                        store_table_data(db_table_name, df)
                    else:
                        print("Pandas read_html could not parse the table.")
                else:
                    print("Failed to find <table> element on detail page.")
                    
            except Exception as e:
                print(f"Error scraping table {title}: {e}")
                
        browser.close()

if __name__ == "__main__":
    init_db()
    subject = "530"
    if len(sys.argv) > 1:
        subject = sys.argv[1]
        
    browser_opt = "chromium"
    if len(sys.argv) > 2:
        browser_opt = sys.argv[2]
        
    scrape_subject(subject, browser_opt)
    print("\nScraping complete.")
