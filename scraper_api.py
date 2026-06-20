import os
import sys
import re
import socket
import sqlite3
import urllib.request
import urllib.parse
import json
import ssl
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
requests.packages.urllib3.disable_warnings()


def patch_archive_dns():
    """Resolve www.archive.bps.go.id (dead DNS) by mapping it to www.bps.go.id's IP.
    This mirrors the /etc/hosts approach but works in pure Python."""
    try:
        bps_ip = socket.gethostbyname("www.bps.go.id")
        print(f"[DNS patch] www.bps.go.id resolved to {bps_ip}")
    except Exception as e:
        print(f"[DNS patch] Could not resolve www.bps.go.id: {e}")
        return

    _real_getaddrinfo = socket.getaddrinfo

    def _patched_getaddrinfo(host, port, *args, **kwargs):
        if host in ("www.archive.bps.go.id", "archive.bps.go.id"):
            print(f"[DNS patch] Redirecting {host} -> {bps_ip}")
            host = bps_ip
        return _real_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = _patched_getaddrinfo
    print("[DNS patch] archive.bps.go.id now mapped to www.bps.go.id IP.")


class ForceHttpArchiveAdapter(HTTPAdapter):
    """Custom adapter that rewrites any https://archive.bps.go.id redirect back
    to http:// to bypass the broken TLS certificate on that host."""

    def send(self, request, **kwargs):
        if 'archive.bps.go.id' in request.url and request.url.startswith('https://'):
            request.url = request.url.replace('https://', 'http://', 1)
        kwargs['verify'] = False
        return super().send(request, **kwargs)


def make_session():
    """Create a requests Session with the ForceHttpArchiveAdapter mounted."""
    session = requests.Session()
    adapter = ForceHttpArchiveAdapter()
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

BASE_API_URL = "https://webapi.bps.go.id/v1/api"
DB_NAME = "bps_data.db"

# All available BPS subject IDs and their names (from API discovery)
ALL_SUBJECTS = {
    2: "Komunikasi",
    3: "Inflasi",
    4: "Konstruksi",
    5: "Konsumsi dan Pengeluaran",
    6: "Tenaga Kerja",
    7: "Energi",
    8: "Ekspor-Impor",
    9: "Industri Besar dan Sedang",
    10: "Pertambangan",
    11: "Produk Domestik Bruto (Lapangan Usaha)",
    12: "Kependudukan",
    13: "Keuangan",
    16: "Pariwisata",
    17: "Transportasi",
    19: "Upah Buruh",
    20: "Harga Perdagangan Besar",
    22: "Nilai Tukar Petani",
    23: "Kemiskinan dan Ketimpangan",
    24: "Peternakan",
    25: "ITB-ITK",
    26: "Indeks Pembangunan Manusia",
    27: "Sosial Budaya",
    28: "Pendidikan",
    29: "Perumahan",
    30: "Kesehatan",
    34: "Politik dan Keamanan",
    35: "Usaha Mikro Kecil",
    36: "Harga Produsen",
    40: "Gender",
    52: "Produk Domestik Regional Bruto (Lapangan Usaha)",
    53: "Tanaman Pangan",
    54: "Perkebunan",
    55: "Hortikultura",
    56: "Perikanan",
    60: "Kehutanan",
    100: "Neraca Arus Dana",
    101: "Pemerintahan",
    102: "Harga Eceran",
    104: "Neraca Sosial Ekonomi",
    105: "Input output",
    151: "Iklim",
    152: "Lingkungan Hidup",
    153: "Geografi",
    168: "Potensi Desa",
    169: "Produk Domestik Bruto (Pengeluaran)",
    170: "Industri Mikro dan Kecil",
    171: "Produk Domestik Regional Bruto (Pengeluaran)",
    173: "Perdagangan Dalam Negeri",
    178: "Neraca Institusi Terintegrasi",
    179: "Matrik Investasi",
    180: "Tujuan Pembangunan Berkelanjutan 2025",
    181: "Tujuan Pembangunan Berkelanjutan Edisi I",
}

# Default subjects to scrape (key economic indicators)
DEFAULT_SUBJECTS = [3, 8, 11, 13, 169, 6, 23, 26]

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Check if table exists and has the right schema; drop & recreate if not
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='table_metadata'")
    exists = cursor.fetchone()
    if exists:
        cursor.execute("PRAGMA table_info(table_metadata)")
        cols = [row[1] for row in cursor.fetchall()]
        if "subject_name" not in cols:
            print("Migrating database schema (adding subject_name column)...")
            cursor.execute("DROP TABLE table_metadata")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS table_metadata (
            id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            subject_id TEXT,
            subject_name TEXT,
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
    print(f"  -> Stored: {table_name} ({len(df)} rows)")

def save_metadata(table_id, title, url, subject_id, subject_name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO table_metadata (id, title, url, subject_id, subject_name)
        VALUES (?, ?, ?, ?, ?)
    """, (str(table_id), title, url, str(subject_id), subject_name))
    conn.commit()
    conn.close()

def api_get(url, api_key):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))

def get_tables_for_subject(api_key, subject_id, domain="0000"):
    """Fetch all static tables for a given subject, handling pagination."""
    all_tables = []
    for page in range(1, 100):
        url = f"{BASE_API_URL}/list/model/statictable/key/{api_key}/domain/{domain}/subject/{subject_id}/page/{page}/"
        try:
            res = api_get(url, api_key)
        except Exception as e:
            print(f"  API error on page {page}: {e}")
            break

        if res.get("status") != "OK":
            print(f"  API Error status: {res.get('status')}")
            print(f"  API Error message: {res.get('message', 'no message')}")
            print(f"  Full response: {res}")
            break

        data = res.get("data", [])

        # data-availability check
        if res.get("data-availability") == "list-not-available":
            break

        # API returns: data = [meta_dict, [table1, table2, ...]]
        if not isinstance(data, list) or len(data) < 2:
            break

        meta = data[0]   # {page, pages, per_page, count, total}
        tables = data[1] # [[table1, table2, ...]] — nested list

        if not isinstance(tables, list) or len(tables) == 0:
            break

        all_tables.extend(tables)
        total_pages = meta.get("pages", 1) if isinstance(meta, dict) else 1
        print(f"  Page {page}/{total_pages}: fetched {len(tables)} tables (total so far: {len(all_tables)})")
        if page >= total_pages:
            break

    return all_tables

def fetch_table_html(table_id, session):
    """Fetch table data directly from the live BPS website HTML.
    Falls back through multiple URL patterns."""
    urls = [
        f"https://www.bps.go.id/id/statistics-table/2/{table_id}/table.html",
        f"https://www.bps.go.id/id/statistics-table/1/{table_id}/table.html",
    ]
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'id-ID,id;q=0.9,en;q=0.8',
        'Referer': 'https://www.bps.go.id/',
    }
    for url in urls:
        try:
            resp = session.get(url, headers=headers, timeout=30, allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 500:
                return resp.text, url
        except Exception as e:
            print(f"  -> HTML fetch error ({url}): {e}")
    return None, None


def parse_html_table(html_text):
    """Extract and parse the first <table> from HTML using pandas.read_html."""
    try:
        dfs = pd.read_html(html_text, flavor='html5lib')
        if dfs:
            return dfs[0]
    except Exception:
        pass
    try:
        dfs = pd.read_html(html_text)
        if dfs:
            return dfs[0]
    except Exception:
        pass
    return None


def scrape_subject(api_key, subject_id, domain="0000"):
    subject_name = ALL_SUBJECTS.get(subject_id, f"Subject {subject_id}")
    print(f"\n{'='*60}")
    print(f"Scraping subject {subject_id}: {subject_name}")
    print(f"{'='*60}")

    tables = get_tables_for_subject(api_key, subject_id, domain)
    if not tables:
        print(f"  No tables found for subject {subject_id}")
        return 0

    print(f"  Found {len(tables)} tables")
    scraped = 0
    session = make_session()

    for idx, table_item in enumerate(tables, 1):
        if not isinstance(table_item, dict):
            continue

        table_id = table_item.get("table_id")
        title = table_item.get("title", f"Table_{table_id}").strip()

        if not table_id:
            continue

        print(f"\n  [{idx}/{len(tables)}] {title} (ID: {table_id})")

        source_url = f"https://www.bps.go.id/id/statistics-table/2/{table_id}/table.html"
        save_metadata(table_id, title, source_url, subject_id, subject_name)

        # Strategy: fetch HTML directly from live BPS website (archive Excel is dead)
        html_text, fetched_url = fetch_table_html(table_id, session)
        if not html_text:
            print(f"  -> Could not fetch HTML for table {table_id}")
            continue

        df = parse_html_table(html_text)
        if df is None or df.empty:
            print(f"  -> No parseable table found in HTML from {fetched_url}")
            continue

        # Flatten MultiIndex columns if present
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = ['_'.join(str(c) for c in col).strip() for col in df.columns.values]

        df.dropna(how='all', inplace=True)
        df.columns = [clean_table_name(str(c)) for c in df.columns]
        db_table_name = clean_table_name(f"s{subject_id}_{table_id}_{title[:25]}")
        store_table_data(db_table_name, df)
        scraped += 1

    return scraped

if __name__ == "__main__":
    init_db()

    # Patch DNS so archive.bps.go.id (dead domain) resolves to www.bps.go.id's IP
    patch_archive_dns()

    api_key = os.environ.get("BPS_API_KEY")
    subjects_to_scrape = list(DEFAULT_SUBJECTS)

    # Parse command-line args: python scraper_api.py [subject1,subject2,...] [api_key]
    for arg in sys.argv[1:]:
        if len(arg) > 15:  # Looks like an API key
            api_key = arg
        elif ',' in arg:
            subjects_to_scrape = [int(x.strip()) for x in arg.split(',')]
        elif arg.lower() == 'all':
            subjects_to_scrape = list(ALL_SUBJECTS.keys())
        else:
            try:
                subjects_to_scrape = [int(arg)]
            except ValueError:
                pass

    if not api_key:
        print("Error: BPS_API_KEY environment variable or argument is missing.")
        print("Usage: python scraper_api.py [subject_ids] <API_KEY>")
        print(f"Available subject IDs: {list(ALL_SUBJECTS.keys())}")
        sys.exit(1)

    total_scraped = 0
    print(f"Scraping subjects: {subjects_to_scrape}")
    for subject_id in subjects_to_scrape:
        count = scrape_subject(api_key, subject_id)
        total_scraped += count

    print(f"\n{'='*60}")
    print(f"DONE. Total tables scraped: {total_scraped}")
    print(f"Database: {DB_NAME}")
