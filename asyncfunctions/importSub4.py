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
    df = pd.read_csv("/home/josh/sub4milers/static/data/sub4_complete22July.csv")
else:
    df = pd.read_csv("/home/joshcarr/sub4milers/static/data/sub4_complete22July.csv")

for runner in df.itertuples():
    name = runner.name
    countries = runner.athlcountries
    firstTime = datetime.strptime(runner.firstTime, '%M:%S.%f').time()
    try:
        date = datetime.strptime(runner.date, "%Y-%m-%d")
    except:
        date = None
    indoor = runner.indoor
    try:
        pbTime = datetime.strptime(runner.pb, '%M:%S.%f').time()
    except:
        pbTime = None
    try:
        pbDate = datetime.strptime(runner.datepb, '%Y-%M-%d')
    except:
        pbDate = None
        
    print(name, firstTime, date)

    athleteAdd = AthleteSub4.objects.create(
        name = name,
        firstTime = firstTime,
        #firstIndoor = indoor,
        countries = countries,
        firstDate = date,
        pbTime = pbTime,
        pbDate = pbDate,
    )

