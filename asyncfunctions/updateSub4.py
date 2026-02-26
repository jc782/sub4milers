import django, os, logging
from datetime import datetime
from decouple import config
import pandas as pd

# python manage.py shell < asyncfunctions/updateSub4.py

logger = logging.getLogger(__name__)

def startDjango():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sub4milers.settings")
    django.setup()

startDjango()
from sub4.models import AthleteSub4


# ── Date parsing ──────────────────────────────────────────────────────────────
# Your CSV has inconsistent date formats across rows, e.g.:
#   "14 February 2026"  →  %d %B %Y
#   "1 February 2026"   →  %d %B %Y
#   "1 Feb 2026"        →  %d %b %Y
#   "31 Jan 2026"       →  %d %b %Y
# We try each format in order and return None (not crash) if all fail.

DATE_FORMATS = [
    "%d %B %Y",   # 14 February 2026
    "%d %b %Y",   # 14 Feb 2026
    "%B %d %Y",   # February 14 2026  (just in case)
    "%b %d %Y",   # Feb 14 2026
    "%d/%m/%Y",   # 14/02/2026
    "%Y-%m-%d",   # 2026-02-14
]

def parse_date(raw):
    if not raw or pd.isna(raw):
        return None
    raw = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    logger.warning(f"Could not parse date: '{raw}'")
    print(f"  ⚠️  Unparseable date: '{raw}'")
    return None


def parse_time(raw):
    if not raw or pd.isna(raw):
        return None
    raw = str(raw).strip()
    try:
        return datetime.strptime(raw, '%M:%S.%f').time()
    except ValueError:
        logger.warning(f"Could not parse time: '{raw}'")
        print(f"  ⚠️  Unparseable time: '{raw}'")
        return None


# ── Load CSV ──────────────────────────────────────────────────────────────────
DEBUG = config('DEBUG', cast=bool)
if DEBUG:
    df = pd.read_csv("/home/josh/sub4milers/static/data/26Feb26.csv")
else:
    df = pd.read_csv("/home/joshcarr/sub4milers/static/data/End25.csv")

print(df.head(50))

# ── Process rows ──────────────────────────────────────────────────────────────
for runner in df.itertuples():
    name = runner.Name.lower()

    # Use getattr with fallback so missing columns never crash
    country  = getattr(runner, 'Country',  None)
    location = getattr(runner, 'Location', None)
    indoor   = getattr(runner, 'Indoor',   None)
    time_raw = getattr(runner, 'Time',     None)
    date_raw = getattr(runner, 'Date',     None)
    dob_raw  = getattr(runner, 'DOB',      None)

    Time = parse_time(time_raw)
    Date = parse_date(date_raw)
    DOB  = parse_date(dob_raw)

    print(f"{name}  |  time={Time}  |  date={Date}")

    if Time is None:
        print(f"  ⏭️  Skipping {name} — no valid time")
        continue

    athlete, created = AthleteSub4.objects.get_or_create(name=name)

    if created:
        print(f"  ✅  Created new entry: {name}")
        athlete.DOB           = DOB
        athlete.countries     = country
        athlete.firstDate     = Date
        athlete.firstTime     = Time
        athlete.firstLocation = location
        athlete.bestDate      = Date
        athlete.bestTime      = Time

        if indoor == 1:
            athlete.IndoorDate     = Date
            athlete.IndoorTime     = Time
            athlete.IndoorLocation = location
        elif indoor == 0:
            athlete.OutdoorDate     = Date
            athlete.OutdoorTime     = Time
            athlete.OutdoorLocation = location

    else:
        # Always update country in case it was blank or corrected
        if country and not athlete.countries:
            athlete.countries = country

        # Fill in any missing fields even if the time is the same
        if not athlete.firstDate and Date:
            athlete.firstDate = Date
        if not athlete.firstTime and Time:
            athlete.firstTime = Time
        if not athlete.firstLocation and location:
            athlete.firstLocation = location
        if not athlete.bestDate and Date:
            athlete.bestDate = Date
        if not athlete.bestTime and Time:
            athlete.bestTime = Time
        if not athlete.DOB and DOB:
            athlete.DOB = DOB

        # Update best time if this run is faster
        if athlete.bestTime is None or Time < athlete.bestTime:
            print(f"  🏃  {name}: new best time {Time} (was {athlete.bestTime})")
            athlete.bestDate = Date
            athlete.bestTime = Time

            if indoor == 1:
                athlete.IndoorDate     = Date
                athlete.IndoorTime     = Time
                athlete.IndoorLocation = location
            elif indoor == 0:
                athlete.OutdoorDate     = Date
                athlete.OutdoorTime     = Time
                athlete.OutdoorLocation = location

        # Update first time if this run is earlier in date than what we have stored
        if Date and athlete.firstDate and Date < athlete.firstDate:
            print(f"  📅  {name}: earlier first date found {Date} (was {athlete.firstDate})")
            athlete.firstDate     = Date
            athlete.firstTime     = Time
            athlete.firstLocation = location

    athlete.save()