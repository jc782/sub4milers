import csv
import datetime
import time as time_module
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sub4.models import Athlete, Performance

WA_TOPLIST_URL = (
    "https://worldathletics.org/records/toplists/middlelong/one-mile/all/men/senior/{year}"
)
SUB4_CUTOFF = datetime.time(minute=4, second=0, microsecond=0)

DATE_FORMATS = [
    "%d %b %Y",     # 18 JUL 2026 / 14 Feb 2026
    "%d %B %Y",     # 14 February 2026
    "%d.%m.%Y",     # 07.07.1999
    "%Y-%m-%d",     # 2026-07-18
    "%d/%m/%Y",     # 18/07/2026
]


def normalize_text(text):
    """Lowercase, strip accents/diacritics, and collapse whitespace."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(text))
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_str.lower().replace("-", " ").split())


def parse_time(raw):
    """Parse 'M:SS.cc', 'M:SS.cc+', or 'M:SS.cch' into a datetime.time object."""
    if not raw:
        return None
    cleaned = str(raw).strip().rstrip("+hH").strip()
    try:
        minutes, rest = cleaned.split(":")
        if "." in rest:
            seconds, frac = rest.split(".")
            centiseconds = frac.ljust(2, "0")[:2]
            microseconds = int(centiseconds) * 10000
        else:
            seconds = rest
            microseconds = 0
        return datetime.time(
            minute=int(minutes),
            second=int(seconds),
            microsecond=microseconds,
        )
    except Exception:
        return None


def parse_date(raw):
    """Parse World Athletics date string into datetime.date."""
    if not raw:
        return None
    cleaned = " ".join(str(raw).strip().split())
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def parse_dob(raw):
    """
    Parse DOB string into (datetime.date | None, birth_year: int | None).
    Handles both full dates ('08 OCT 1997') and year-only strings ('2000').
    """
    if not raw:
        return None, None
    cleaned = " ".join(str(raw).strip().split())
    full_date = parse_date(cleaned)
    if full_date:
        return full_date, full_date.year
    if cleaned.isdigit() and len(cleaned) == 4:
        return None, int(cleaned)
    return None, None


def name_similarity(a, b):
    """Similarity score between two names after normalizing accents and case."""
    norm_a = normalize_text(a)
    norm_b = normalize_text(b)
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0

    base_score = SequenceMatcher(None, norm_a, norm_b).ratio()

    tokens_a = norm_a.split()
    tokens_b = norm_b.split()
    if len(tokens_a) >= 2 and len(tokens_b) >= 2:
        # Handle middle-name variations (e.g. "Vincent Kibet Keter" vs "Vincent Keter")
        if tokens_a[0] == tokens_b[0] and tokens_a[-1] == tokens_b[-1]:
            base_score = max(base_score, 0.92)
        # Guard against siblings/twins sharing a surname (e.g. "Leo Young" vs "Lex Young",
        # "Ted Higgins" vs "Jack Higgins")
        elif tokens_a[-1] == tokens_b[-1]:
            first_sim = SequenceMatcher(None, tokens_a[0], tokens_b[0]).ratio()
            if first_sim < 0.75:
                base_score = min(base_score, 0.70)

    return base_score



def find_matching_athlete(name, country, dob, birth_year, existing_athletes, threshold=0.85):
    """
    Find an existing Athlete matching name + optional country/DOB.
    Protects against merging distinct athletes with the same name but different birth years
    (e.g. Reuben Reina Sr. b. 1967 vs Reuben Reina Jr. b. 2003).
    """
    best_match = None
    best_score = 0.0

    norm_country = country.strip().upper() if country else ""

    for athlete in existing_athletes:
        # If both have known birth years and they differ by > 2 years, do not match
        if birth_year and athlete.DOB and abs(birth_year - athlete.DOB.year) > 2:
            continue

        score = name_similarity(name, athlete.name)
        if score < 0.55:
            continue

        # Boost if country matches
        if norm_country and athlete.countries:
            db_countries = [
                c.strip().upper()
                for c in athlete.countries.replace("/", ",").split(",")
                if c.strip()
            ]
            if norm_country in db_countries:
                score = min(1.0, score + 0.05)

        # Boost if exact DOB matches
        if dob and athlete.DOB and dob == athlete.DOB:
            score = min(1.0, score + 0.10)

        if score > best_score:
            best_score = score
            best_match = athlete

    if best_score >= threshold:
        return best_match, best_score
    return None, best_score


def find_matching_performance(time_val, date_val, name, birth_year, name_threshold=0.65):
    """
    Fallback: if a performance with identical time and date exists for an athlete
    with a reasonably similar name (and no conflicting birth year), return that athlete.
    """
    if not time_val or not date_val:
        return None

    matches = Performance.objects.filter(time=time_val, date=date_val).select_related("athlete")
    for perf in matches:
        ath = perf.athlete
        if birth_year and ath.DOB and abs(birth_year - ath.DOB.year) > 2:
            continue
        if name_similarity(name, ath.name) >= name_threshold:
            return ath
    return None


def ensure_first_sub4(athlete, dry_run=False):
    """
    Ensure the athlete has `is_first_sub4=True` set on their earliest sub-4 performance.
    - If no performance has `is_first_sub4=True`, sets it on the earliest dated performance
      (or fastest if no dates exist).
    - If a performance already has `is_first_sub4=True`, only moves the flag if another
      performance has a strictly earlier non-null date.
    """
    if not athlete or not athlete.pk:
        return False

    perfs = list(athlete.performances.all())
    if not perfs:
        return False

    current_first = [p for p in perfs if p.is_first_sub4]
    dated_perfs = sorted(
        [p for p in perfs if p.date is not None],
        key=lambda p: (p.date, p.time),
    )

    target = None
    if not current_first:
        target = dated_perfs[0] if dated_perfs else min(perfs, key=lambda p: p.time)
    elif len(current_first) > 1:
        # Keep only one is_first_sub4
        target = dated_perfs[0] if dated_perfs else current_first[0]
    else:
        marked = current_first[0]
        if dated_perfs:
            earliest = dated_perfs[0]
            if marked.date is None or earliest.date < marked.date:
                target = earliest

    if target is not None:
        if not dry_run:
            athlete.performances.exclude(pk=target.pk).filter(is_first_sub4=True).update(
                is_first_sub4=False
            )
            if not target.is_first_sub4:
                target.is_first_sub4 = True
                target.save(update_fields=["is_first_sub4"])
        return True
    return False


class Command(BaseCommand):
    help = "Scrape sub-4:00.00 mile performances from World Athletics toplists and update the database."

    def add_arguments(self, parser):
        current_year = datetime.date.today().year
        parser.add_argument(
            "--year",
            type=int,
            nargs="+",
            default=[current_year],
            help=f"Season year(s) to scrape (default: {current_year})",
        )
        parser.add_argument(
            "--best-only",
            action="store_true",
            help="Only scrape best result per athlete (default: False, scrapes all sub-4 performances)",
        )
        parser.add_argument(
            "--threshold",
            type=float,
            default=0.85,
            help="Name similarity threshold for matching existing athletes (default: 0.85)",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=25,
            help="Maximum number of pages to scan per year (default: 25)",
        )
        parser.add_argument(
            "--save-csv",
            action="store_true",
            help="Save scraped rows to static/data/wa_<year>.csv",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without modifying the database",
        )

    def fetch_year_rows(self, session, year, best_only=False, max_pages=25):
        """Paginate through World Athletics toplist for `year` and return all sub-4 rows."""
        best_str = "true" if best_only else "false"
        scraped_rows = []

        for page in range(1, max_pages + 1):
            params = {
                "regionType": "world",
                "page": page,
                "bestResultsOnly": best_str,
                "maxResultsByCountry": "all",
                "eventId": "10229503",
                "ageCategory": "senior",
            }
            url = WA_TOPLIST_URL.format(year=year)
            self.stdout.write(f"Fetching {year} page {page} (bestResultsOnly={best_str})...")

            resp = None
            for attempt in range(3):
                try:
                    resp = session.get(url, params=params, timeout=20)
                    resp.raise_for_status()
                    break
                except requests.RequestException as exc:
                    if attempt == 2:
                        raise CommandError(f"Failed to fetch World Athletics page {page}: {exc}") from exc
                    time_module.sleep(2 * (attempt + 1))

            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", class_="records-table")
            if not table or not table.find("tbody"):
                self.stdout.write(f"  No results table found on page {page}; stopping.")
                break

            trs = table.find("tbody").find_all("tr")
            if not trs:
                self.stdout.write(f"  Empty table on page {page}; stopping.")
                break

            reached_cutoff = False
            page_sub4_count = 0

            for tr in trs:
                def cell_text(th_name):
                    td = tr.find("td", {"data-th": th_name})
                    return " ".join(td.get_text().split()) if td else ""

                raw_rank = cell_text("Rank")
                raw_mark = cell_text("Mark")
                raw_wind = cell_text("WIND")
                raw_name = cell_text("Competitor")
                raw_dob = cell_text("DOB")
                raw_nat = cell_text("Nat")
                raw_pos = cell_text("Pos")
                raw_venue = cell_text("Venue")
                raw_date = cell_text("Date")
                raw_score = cell_text("ResultScore")

                if not raw_mark or not raw_name:
                    continue

                parsed_time = parse_time(raw_mark)
                if parsed_time is None:
                    continue

                if parsed_time >= SUB4_CUTOFF:
                    reached_cutoff = True
                    continue

                page_sub4_count += 1
                scraped_rows.append({
                    "Rank": raw_rank,
                    "Time": raw_mark,
                    "Wind": raw_wind,
                    "Name": raw_name,
                    "DOB": raw_dob,
                    "Country": raw_nat,
                    "Pos": raw_pos,
                    "Location": raw_venue,
                    "Date": raw_date,
                    "Points": raw_score,
                })

            self.stdout.write(f"  Found {page_sub4_count} sub-4 performance(s) on page {page}.")
            if reached_cutoff or page_sub4_count == 0:
                break

            time_module.sleep(0.5)

        return scraped_rows

    def handle(self, *args, **options):
        years = options["year"]
        best_only = options["best_only"]
        threshold = options["threshold"]
        max_pages = options["max_pages"]
        save_csv = options["save_csv"]
        dry_run = options["dry_run"]

        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        })

        existing_athletes = list(Athlete.objects.all())

        total_new_athletes = 0
        total_new_perfs = 0
        total_updated_perfs = 0
        total_skipped_perfs = 0
        touched_athletes = set()

        for year in years:
            rows = self.fetch_year_rows(
                session=session,
                year=year,
                best_only=best_only,
                max_pages=max_pages,
            )
            self.stdout.write(
                self.style.NOTICE(f"Scraped {len(rows)} sub-4 performance(s) for {year}.")
            )

            if save_csv and rows and not dry_run:
                csv_dir = Path(settings.BASE_DIR) / "static" / "data"
                csv_dir.mkdir(parents=True, exist_ok=True)
                csv_path = csv_dir / f"wa_{year}.csv"
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(
                        f,
                        fieldnames=[
                            "Rank", "Time", "Wind", "Name", "DOB",
                            "Country", "Pos", "Location", "Date", "Points",
                        ],
                    )
                    writer.writeheader()
                    writer.writerows(rows)
                self.stdout.write(f"Saved CSV snapshot to {csv_path}")

            for row in rows:
                raw_time = row["Time"]
                raw_name = row["Name"]
                country = row["Country"].strip().upper()
                location = row["Location"].strip()
                raw_date = row["Date"]
                raw_dob = row["DOB"]

                is_split = raw_time.strip().endswith("+")
                perf_time = parse_time(raw_time)
                perf_date = parse_date(raw_date)
                dob_date, birth_year = parse_dob(raw_dob)

                if perf_time is None or perf_time >= SUB4_CUTOFF:
                    continue

                notes = "Split in longer race" if is_split else None

                # 1. Match existing athlete by name (+ country/DOB)
                athlete, score = find_matching_athlete(
                    name=raw_name,
                    country=country,
                    dob=dob_date,
                    birth_year=birth_year,
                    existing_athletes=existing_athletes,
                    threshold=threshold,
                )

                # 2. Fallback: identical time + date with similar name
                if athlete is None:
                    athlete = find_matching_performance(
                        time_val=perf_time,
                        date_val=perf_date,
                        name=raw_name,
                        birth_year=birth_year,
                    )

                # 3. Create new athlete if no match
                if athlete is None:
                    clean_name = raw_name.lower()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  [NEW ATHLETE] {raw_name} ({country}, DOB={dob_date or raw_dob or 'unknown'}) "
                            f"— best match score {score:.2f}"
                        )
                    )
                    if not dry_run:
                        athlete = Athlete.objects.create(
                            name=clean_name,
                            DOB=dob_date,
                            countries=country,
                        )
                        existing_athletes.append(athlete)
                    else:
                        athlete = Athlete(
                            name=clean_name,
                            DOB=dob_date,
                            countries=country,
                        )
                        existing_athletes.append(athlete)
                    total_new_athletes += 1
                else:
                    # Enrich missing DOB or country on existing athlete
                    updated_fields = []
                    if dob_date and not athlete.DOB:
                        athlete.DOB = dob_date
                        updated_fields.append("DOB")
                    if country and not athlete.countries:
                        athlete.countries = country
                        updated_fields.append("countries")
                    if updated_fields and athlete.pk and not dry_run:
                        athlete.save()

                # Check for existing performance
                existing_perf = None
                if athlete.pk:
                    existing_perf = Performance.objects.filter(
                        athlete=athlete,
                        time=perf_time,
                        date=perf_date,
                    ).first()

                if existing_perf:
                    if location and not existing_perf.location:
                        existing_perf.location = location
                        if not dry_run:
                            existing_perf.save(update_fields=["location"])
                        total_updated_perfs += 1
                    else:
                        total_skipped_perfs += 1
                    if athlete.pk:
                        touched_athletes.add(athlete.pk)
                    continue

                total_new_perfs += 1
                if not dry_run:
                    Performance.objects.create(
                        athlete=athlete,
                        time=perf_time,
                        date=perf_date,
                        location=location or None,
                        notes=notes,
                    )
                    touched_athletes.add(athlete.pk)

                self.stdout.write(
                    f"  [NEW PERF] {athlete.name} — {perf_time.strftime('%M:%S.%f')[:-4]} "
                    f"on {perf_date} at {location}"
                    + (" [DRY RUN]" if dry_run else "")
                )

        # Ensure all touched athletes AND any athletes missing is_first_sub4 are updated
        missing_first_ids = set(
            Athlete.objects.exclude(performances__is_first_sub4=True)
            .filter(performances__isnull=False)
            .values_list("pk", flat=True)
        )
        athletes_to_check = Athlete.objects.filter(
            pk__in=(touched_athletes | missing_first_ids)
        ).prefetch_related("performances")

        first_sub4_updated = 0
        for ath in athletes_to_check:
            if ensure_first_sub4(ath, dry_run=dry_run):
                first_sub4_updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'[DRY RUN] ' if dry_run else ''}Scrape complete: "
                f"{total_new_athletes} new athlete(s), "
                f"{total_new_perfs} new performance(s), "
                f"{total_updated_perfs} backfilled location(s), "
                f"{total_skipped_perfs} duplicate(s) skipped, "
                f"{first_sub4_updated} first-sub-4 flag(s) updated."
            )
        )
