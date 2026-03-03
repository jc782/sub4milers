from django.core.management.base import BaseCommand
from sub4.models import Athlete

class Command(BaseCommand):
    help = 'Normalise all country codes to uppercase'

    def handle(self, *args, **options):
        updated = 0
        for athlete in Athlete.objects.exclude(countries__isnull=True).exclude(countries=''):
            codes = [c.strip().upper() for c in athlete.countries.replace('/', ',').split(',')]
            normalised = ','.join(codes)
            if normalised != athlete.countries:
                athlete.countries = normalised
                athlete.save()
                updated += 1
        self.stdout.write(self.style.SUCCESS(f'Updated {updated} athletes'))