import os
import sys
import re
import sqlite3
import urllib.request
import urllib.parse
import json
import pandas as pd

BASE_API_URL = "https://webapi.bps.go.id/v1/api"
DB_NAME = "bps_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
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

def clean_table_name(name):
    clean = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    clean = re.sub(r'^_+', '', clean)
    clean = re.sub(r'_+', '_', clean)
    if not clean or clean[0].isdigit():
        clean = "table_" + clean
    return clean[:64].lower()

def store_table_data(table_name, df):
    conn = sqlite3.connect(DB_NAME)
    df_str = df.astype(str)
    df_str.to_sql(table_name, conn, if_exists='replace', index=False)
    conn.close()
    print(f"Stored table: {table_name} ({len(df)} rows)")

def save_metadata(table_id, title, url, subject_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO table_metadata (id, title, url, subject_id)
        VALUES (?, ?, ?, ?)
    """, (str(table_id), title, url, subject_id))
    conn.commit()
    conn.close()

def scrape_via_api(api_key, subject_id="530", domain="0000"):
    print(f"Starting API scrape for subject: {subject_id} on domain: {domain}")
    
    # 1. Fetch static tables list for the subject
    list_url = f"{BASE_API_URL}/list/model/statictable/key/{api_key}/domain/{domain}/subject/{subject_id}/"
    print(f"Fetching tables list from: {list_url.replace(api_key, '***')}")
    
    try:
        req = urllib.request.Request(list_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            res_json = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Failed to fetch list from API: {e}")
        return

    if res_json.get("status") == "Error":
        print(f"BPS API Error: {res_json.get('message')}")
        return

    tables_data = res_json.get("data", [])
    if not tables_data or len(tables_data) == 0:
        if isinstance(tables_data, list):
            print(f"No static tables found for subject {subject_id}.")
        else:
            print("Unexpected API response structure:", res_json)
        return

    print(f"Found {len(tables_data)} tables in BPS API response.")
    
    for idx, table_item in enumerate(tables_data, 1):
        if not isinstance(table_item, dict):
            continue
            
        table_id = table_item.get("table_id")
        title = table_item.get("title", f"Table_{table_id}")
        excel_url = table_item.get("excel")
        
        if not table_id or not excel_url:
            continue
            
        print(f"\n[{idx}/{len(tables_data)}] Processing table: {title} (ID: {table_id})")
        
        # Save metadata
        source_url = f"https://www.bps.go.id/id/statistics-table/2/{table_id}/table.html" # placeholder
        save_metadata(table_id, title, source_url, subject_id)
        
        # 2. Download the Excel file
        print(f"Downloading Excel from: {excel_url.replace(api_key, '***')}")
        try:
            excel_req = urllib.request.Request(excel_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(excel_req) as excel_res:
                excel_data = excel_res.read()
                
            # Save excel temporarily
            temp_filename = f"temp_{table_id}.xlsx"
            with open(temp_filename, "wb") as f:
                f.write(excel_data)
                
            # 3. Parse Excel using pandas
            try:
                df = pd.read_excel(temp_filename)
                df.dropna(how='all', inplace=True)
                df.columns = [clean_table_name(str(c)) for c in df.columns]
                
                db_table_name = clean_table_name(f"data_{table_id}_{title[:30]}")
                store_table_data(db_table_name, df)
            except Exception as parse_err:
                print(f"Failed to parse Excel file: {parse_err}")
            finally:
                if os.path.exists(temp_filename):
                    os.remove(temp_filename)
        except Exception as dl_err:
            print(f"Failed to download/process table {table_id}: {dl_err}")

if __name__ == "__main__":
    init_db()
    
    # Load API key from env variable or sys args
    api_key = os.environ.get("BPS_API_KEY")
    subject = "530"
    
    if len(sys.argv) > 1:
        if len(sys.argv[1]) > 10:
            api_key = sys.argv[1]
        else:
            subject = sys.argv[1]
            
    if len(sys.argv) > 2:
        if len(sys.argv[2]) > 10:
            api_key = sys.argv[2]
        else:
            subject = sys.argv[2]
            
    if not api_key:
        print("Error: BPS_API_KEY environment variable or argument is missing.")
        print("Usage: python scraper_api.py [subject_id] <API_KEY>")
        sys.exit(1)
        
    scrape_via_api(api_key, subject)
    print("\nAPI Scraping complete.")
