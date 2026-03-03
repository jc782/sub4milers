# sub4/management/commands/import_csv_performances.py

import csv
import datetime
from difflib import SequenceMatcher

from django.core.management.base import BaseCommand

from sub4.models import Athlete, Performance


def parse_time(raw):
    """Parse 'M:SS.cc' or 'M:SS.cc+' into a datetime.time object."""
    raw = raw.strip().rstrip('+')
    try:
        minutes, rest = raw.split(':')
        if '.' in rest:
            seconds, centiseconds = rest.split('.')
            # Pad centiseconds to 2 digits, then convert to microseconds
            centiseconds = centiseconds.ljust(2, '0')[:2]
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
    """Parse 'DD.MM.YYYY' into a datetime.date object."""
    raw = raw.strip()
    try:
        return datetime.datetime.strptime(raw, '%d.%m.%Y').date()
    except Exception:
        return None


def name_similarity(a, b):
    """Return a similarity ratio between two names, case-insensitive."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def find_matching_athlete(name, country, dob, existing_athletes, threshold=0.85):
    """
    Try to find an existing athlete matching name + optionally country/DOB.
    Returns (athlete, score) or (None, 0).
    """
    best_match = None
    best_score = 0

    for athlete in existing_athletes:
        score = name_similarity(name, athlete.name)

        # Boost score if country matches
        if country and athlete.countries:
            csv_country = country.strip().title()
            db_countries = [c.strip().title() for c in athlete.countries.replace('/', ',').split(',')]
            if csv_country in db_countries:
                score = min(1.0, score + 0.05)

        # Boost score if DOB matches
        if dob and athlete.DOB and dob == athlete.DOB:
            score = min(1.0, score + 0.1)

        if score > best_score:
            best_score = score
            best_match = athlete

    if best_score >= threshold:
        return best_match, best_score
    return None, best_score


def find_matching_performance(time, date, name, name_threshold=0.60):
    """
    If a performance with the same time and date exists, and the name is
    vaguely similar, treat it as the same performance and return the athlete.
    """
    if not time or not date:
        return None

    matches = Performance.objects.filter(time=time, date=date).select_related('athlete')
    for perf in matches:
        if name_similarity(name, perf.athlete.name) >= name_threshold:
            return perf.athlete
    return None


class Command(BaseCommand):
    help = 'Import performances from a CSV file, merging with existing Athlete/Performance data'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Path to the CSV file')
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without writing to the database',
        )
        parser.add_argument(
            '--threshold',
            type=float,
            default=0.60,
            help='Name similarity threshold for matching athletes (default: 0.60)',
        )
        parser.add_argument(
            '--show-unmatched',
            action='store_true',
            help='Print rows where no athlete match was found',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        threshold = options['threshold']
        csv_path = options['csv_file']

        new_athletes = 0
        new_performances = 0
        skipped_performances = 0
        unmatched = []

        # Load all existing athletes once for matching
        existing_athletes = list(Athlete.objects.all())

        with open(csv_path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)

            for row in reader:
                raw_time = row.get('Time', '').strip()
                name     = row.get('Name', '').strip()
                country  = row.get('Country', '').strip()
                raw_dob  = row.get('DOB', '').strip()
                location = row.get('Location', '').strip()
                raw_date = row.get('Date', '').strip()

                if not name or not raw_time:
                    continue

                is_split = raw_time.endswith('+')
                time = parse_time(raw_time)
                date = parse_date(raw_date)
                dob  = parse_date(raw_dob) if raw_dob else None

                if time is None:
                    self.stdout.write(self.style.WARNING(
                        f'  Could not parse time "{raw_time}" for {name}, skipping'
                    ))
                    continue

                notes = 'Split in longer race' if is_split else None

                # 1. Try to match athlete by name (+ country/DOB bonuses)
                athlete, score = find_matching_athlete(
                    name, country, dob, existing_athletes, threshold
                )

                # 2. Fallback: if time+date match an existing performance with a
                #    vaguely similar name, trust that over the name threshold
                if athlete is None:
                    athlete = find_matching_performance(time, date, name)
                    if athlete:
                        self.stdout.write(
                            f'  [TIME+DATE MATCH] "{name}" matched to '
                            f'"{athlete.name}" via identical time/date'
                        )

                # 3. No match found — create a new athlete
                if athlete is None:
                    unmatched.append((name, country, score))
                    self.stdout.write(
                        f'  [NEW ATHLETE] {name} ({country}) — '
                        f'best name match score was {score:.2f}'
                    )
                    if not dry_run:
                        athlete = Athlete.objects.create(
                            name=name,
                            DOB=dob,
                            countries=country,
                        )
                        existing_athletes.append(athlete)
                    else:
                        athlete = Athlete(name=name, DOB=dob, countries=country)
                    new_athletes += 1

                # Check if this exact performance already exists (skip if so)
                existing_perf = None
                if athlete.pk:
                    existing_perf = Performance.objects.filter(
                        athlete=athlete,
                        time=time,
                        date=date,
                    ).first()

                if existing_perf:
                    skipped_performances += 1
                    self.stdout.write(
                        f'  [DUPLICATE] {athlete.name} — {time} on {date}, already exists, skipping'
                    )
                    continue

                # Create the new performance
                new_performances += 1
                if not dry_run:
                    Performance.objects.create(
                        athlete=athlete,
                        time=time,
                        date=date,
                        location=location or None,
                        notes=notes,
                    )
                self.stdout.write(self.style.SUCCESS(
                    f'  [NEW PERFORMANCE] {athlete.name} — {time} on {date} at {location}'
                    + (' (split)' if is_split else '')
                    + (' [DRY RUN]' if dry_run else '')
                ))

        if options['show_unmatched'] and unmatched:
            self.stdout.write('\n--- Unmatched athletes (would be created new) ---')
            for name, country, score in unmatched:
                self.stdout.write(f'  {name} ({country}) — closest name match score: {score:.2f}')

        self.stdout.write(self.style.SUCCESS(
            f'\n{"[DRY RUN] " if dry_run else ""}Done: '
            f'{new_athletes} new athletes, {new_performances} new performances added, '
            f'{skipped_performances} duplicate performances skipped.'
        ))