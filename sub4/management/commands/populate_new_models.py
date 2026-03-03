# sub4/management/commands/populate_new_models.py

from django.core.management.base import BaseCommand
from sub4.models import AthleteSub4, Athlete, Performance


class Command(BaseCommand):
    help = 'Populates Athlete and Performance models from existing AthleteSub4 data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be created without writing to the database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        created_athletes = 0
        created_performances = 0

        # Clear existing data first
        if not dry_run:
            self.stdout.write('Clearing existing Athlete and Performance data...')
            Athlete.objects.all().delete()  # cascades to Performance
            self.stdout.write(self.style.WARNING('Cleared.'))

        for old in AthleteSub4.objects.all():
            if not dry_run:
                athlete = Athlete.objects.create(
                    name=old.name,
                    DOB=old.DOB,
                    countries=old.countries,
                )
            else:
                athlete = Athlete(name=old.name, DOB=old.DOB, countries=old.countries)

            created_athletes += 1

            candidates = [
                (old.firstTime,   old.firstDate,   old.firstLocation,   True),
                (old.IndoorTime,  old.IndoorDate,  old.IndoorLocation,  False),
                (old.OutdoorTime, old.OutdoorDate, old.OutdoorLocation, False),
                (old.bestTime,    old.bestDate,    None,                False),
            ]

            seen_times = set()
            performances = []
            for time, date, location, is_first in candidates:
                if time and time not in seen_times:
                    seen_times.add(time)
                    performances.append(Performance(
                        athlete=athlete,
                        time=time,
                        date=date,
                        location=location,
                        is_first_sub4=is_first,
                    ))

            created_performances += len(performances)

            if not dry_run:
                Performance.objects.bulk_create(performances)

            self.stdout.write(
                f'  {"[DRY RUN] " if dry_run else ""}{old.name}: {len(performances)} performance(s)'
            )

        self.stdout.write(self.style.SUCCESS(
            f'\n{"[DRY RUN] " if dry_run else ""}Done: '
            f'{created_athletes} athletes, {created_performances} performances created.'
        ))