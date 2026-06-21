# BPS Database Builder Agent

This is an automated agent designed to scrape and build a structured SQLite database from the official **Badan Pusat Statistik (BPS) Indonesia** website (e.g. macroeconomics, agriculture, social st[...]

Since the BPS website uses modern web frameworks with dynamic tables and Cloudflare protection, this agent uses **Playwright** (headless browser automation) to simulate a real user browsing the pag[...]

## How it works

1. **Scrapes the Subject Page:** Navigates to `https://www.bps.go.id/id/statistics-table?subject=<subject_id>`.
2. **Finds Tables:** Dynamically scans the table list, follows pagination ("Berikutnya" / "Next"), and builds a checklist of all available statistics tables for that subject.
3. **Downloads & Parses Detail Pages:** Navigates to each table's detail page, waits for the `<table>` element to render, and reads the table content using `pandas.read_html`.
4. **Builds SQLite Database (`bps_data.db`):** 
   - Saves a central log of all downloaded tables in `table_metadata`.
   - Saves each statistics dataset in its own database table with columns mapped to the BPS table headers.

---

## 🚀 Running on GitHub Actions (Recommended)

This agent is configured to run automatically on **GitHub Actions**. This avoids any local installation issues or network blocks.

### 1. Enable Workflow Write Permissions (Crucial for auto-commit)
To allow the GitHub Actions runner to commit the SQLite database (`bps_data.db`) back to your repository, you need to grant it write permissions:
1. Go to your repository on GitHub.
2. Click on **Settings** -> **Actions** -> **General**.
3. Scroll down to **Workflow permissions**.
4. Select **Read and write permissions**.
5. Click **Save**.

### 2. How to trigger the scraper manually
1. Go to your repository on GitHub.
2. Click on the **Actions** tab.
3. Click on the **BPS Database Scraper** workflow in the left sidebar.
4. Click the **Run workflow** dropdown on the right.
5. Enter the BPS **Subject ID** you wish to scrape (default is `530` for Macroeconomic Statistics) and select the browser type (default is `chromium`).
6. Click **Run workflow**.

After completion, the workflow will commit `bps_data.db` and the interactive summary page inside the `docs/` folder directly to your repository.

### 3. Enable GitHub Pages to view the Interactive Dashboard
The repository automatically generates an interactive HTML dashboard in the `docs/` folder. To view it live:
1. Go to your repository on GitHub.
2. Click on **Settings** -> **Pages**.
3. Under **Build and deployment** -> **Source**, select **Deploy from a branch**.
4. Under **Branch**, select `main` (or the branch you push to) and select `/docs` from the folder dropdown (instead of `/ (root)`).
5. Click **Save**.

Your interactive dashboard will be live at https://hengkykurniawan.github.io/BSP_data_builder/

(If you fork this repository, replace `hengkykurniawan` with your GitHub username to get your pages URL.)

---

## 💻 Running Locally

If you want to run the script on a machine that has Python and Chrome/Firefox installed:

### 1. Install dependencies:
```bash
pip install -r requirements.txt
```

### 2. Install Playwright browsers:
```bash
playwright install
```

### 3. Run the script:
```bash
# Scrape Subject 530 (Macroeconomic Statistics)
python scraper.py 530 chromium

# Scrape using Firefox instead
python scraper.py 530 firefox
```

---

## 📊 Database Schema

The database file `bps_data.db` contains:

1. **`table_metadata`**: Contains a list of all tables scraped:
   - `id`: Unique hash/ID of the BPS table.
   - `title`: Original Indonesian title of the table.
   - `url`: Direct link to the table detail page on BPS website.
   - `subject_id`: The BPS subject number.
   - `fetched_at`: Timestamp of when the table was scraped.

2. **`data_<id>_<title_slug>`**: A separate table for each statistics table containing the actual structured columns and rows.
