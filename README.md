# VolbyCZ Scraper Dashboard

Interactive Streamlit dashboard that scrapes the latest published chamber of deputies election results from [volby.cz](https://www.volby.cz) and visualises them for the Czech Parliamentary Elections 2025. When the official 2025 dataset is not yet available, the app automatically falls back to the most recent published election (currently 2021) so you can preview the experience.

## Features
- Live scraping of national turnout, vote totals, mandates and regional leaders from volby.cz
- Clean dashboard with vote share charts, seat distribution and data tables
- Optional CSV downloads for party-level and regional summaries
- Graceful fallback to archived results when the requested election year has not been released yet

## Getting Started

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the Streamlit app**
   ```bash
   streamlit run app.py
   ```

   The dashboard will open in your browser. Leave the sidebar fallback option enabled to preview 2021 data until the 2025 feed is published.

## Project Structure
- `app.py` – Streamlit UI that loads the scraped datasets and renders the dashboard.
- `volbycz_scraper/` – Reusable scraper utilities for fetching summary metrics, party results, seat allocation and regional leaders.

## Data Source
All data is scraped from the Czech Statistical Office official election portal (volby.cz). The scraper hits only public endpoints already used by the site and does not circumvent any restrictions.

## Notes
- The scraper currently supports the election namespace `psYYYY` used by volby.cz for lower-house elections. Adjust `PRIMARY_YEAR`/`FALLBACK_YEAR` in `app.py` as new datasets are published.
- When 2025 data goes live the dashboard will automatically switch to the fresh results without code changes.
