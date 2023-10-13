import django, os, logging
from datetime import datetime
from decouple import config
import pandas as pd
import re

# python manage.py shell < asyncFunctions/importSub4.py

def startDjango():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sub4milers.settings")
    django.setup()
    

startDjango()
from sub4.models import AthleteSub4 

# CSV format:
# id,name,athlcountries,firstTime,indoor,date,pb,datepb
DEBUG = config('DEBUG', cast=bool)
if DEBUG:
    df = pd.read_csv("/home/josh/sub4milers/static/data/sub4_101023.csv")
else:
    df = pd.read_csv("/home/joshcarr/sub4milers/static/data/sub4_101023.csv")

for runner in df.itertuples():
    name = runner.Name
    country = runner.Country
    OutdoorLocation = runner.OutdoorLocation
    IndoorLocation = runner.IndoorLocation

    try:
        firstTime = datetime.strptime(runner.FirstTime, '%M:%S.%f').time()
    except:
        firstTime = None

    try:
        firstDate = datetime.strptime(runner.FirstDate, "%m/%d/%Y")
    except:
        firstDate = None

    try:
        pbTime = datetime.strptime(runner.PB, '%M:%S.%f').time()
    except:
        pbTime = None

    try:
        pbDate = datetime.strptime(runner.PB, "%m/%d/%Y")
    except:
        pbDate = None
    
    try:
        firstTime = datetime.strptime(runner.FirstTime, '%M:%S.%f').time()
    except:
        firstTime = None

    try:
        DOB = datetime.strptime(runner.DOB, "%m/%d/%Y")
    except:
        DOB = None

    try:
        OutdoorTime = datetime.strptime(runner.OutdoorTime, '%M:%S.%f').time()
    except:
        OutdoorTime = None

    try:
        OutdoorDate = datetime.strptime(runner.OutdoorDate, "%m/%d/%Y")
    except:
        OutdoorDate = None

    try:
        IndoorTime = datetime.strptime(runner.IndoorTime, '%M:%S.%f').time()
    except:
        IndoorTime = None

    try:
        IndoorDate = datetime.strptime(runner.IndoorDate, "%m/%d/%Y")
    except:
        IndoorDate = None

    try:
        FastestTime = datetime.strptime(runner.FastestTime, '%M:%S.%f').time()
    except:
        FastestTime = None

    try:
        FastestDate = datetime.strptime(runner.FastestDate, "%m/%d/%Y")
    except:
        FastestDate = None


    athleteAdd = AthleteSub4.objects.create(
        name = name,
        DOB = DOB,
        firstTime = firstTime,
        firstDate = firstDate,
        pbTime = pbTime,
        pbDate = pbDate,
        IndoorTime = IndoorTime,
        IndoorDate = IndoorDate,
        IndoorLocation = IndoorLocation,
        OutdoorTime = OutdoorTime,
        OutdoorDate = OutdoorDate,
        OutdoorLocation = OutdoorLocation,
        countries = country,
        bestTime = FastestTime,
        bestDate = FastestDate
        )

