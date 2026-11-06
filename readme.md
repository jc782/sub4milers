This repo is to manage the sub4milers record. Now hosted as a static site on github.
Any corrections to the data please contact Josh through sub4milers@gmail.com    

### Updating Data & Building the Static Site

The site automatically scrapes World Athletics and rebuilds every Monday via GitHub Actions (`.github/workflows/update_sub4.yml`).

To run manually:
```bash
# 1. Scrape all sub-4 performances for the current year from World Athletics
python manage.py scrape_world_athletics

# (Optional) Preview changes without writing to db.sqlite3, or scrape a specific year
python manage.py scrape_world_athletics --year 2026 --dry-run

# 2. Render the static site into docs/ and build/
python manage.py build_static
```