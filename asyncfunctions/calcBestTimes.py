import django, os, logging
from decouple import config

# python manage.py shell < asyncfunctions/calcBestTimes.py

def startDjango():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sub4milers.settings")
    django.setup()
    

startDjango()
from sub4.models import AthleteSub4 
[athlete.calcBestTime() for athlete in AthleteSub4.objects.all()]
