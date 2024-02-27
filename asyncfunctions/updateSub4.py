import django, os, logging
from datetime import datetime
from decouple import config
import pandas as pd

# python manage.py shell < asyncFunctions/updateSub4.py

def startDjango():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sub4milers.settings")
    django.setup()
    

startDjango()
from sub4.models import AthleteSub4 

## Takes a CSV and checks whether or not the athlete is already in the DB and then either updates or creates a new entry
# From this point we just take race results and work out if it's indoor / outdoor, a pb, first time, or whatever

# CSV format:
# id,name,athlcountries,firstTime,indoor,date,pb,datepb
DEBUG = config('DEBUG', cast=bool)
if DEBUG:
    df = pd.read_csv("/home/josh/sub4milers/static/data/Indoor24_26Feb.csv")
else:
    df = pd.read_csv("/home/joshcarr/sub4milers/static/data/Indoor24_26Feb.csv")

print(df.head(50))

for runner in df.itertuples():
    # Name is the only definite row
    name = runner.Name.lower()
    
    try:
        country = runner.Country
    except: 
        country = None

    try: 
        Location = runner.Location
    except:
        Location = None

    try:
        Indoor = runner.Indoor
    except:
        Indoor = None

    try:
        Time = datetime.strptime(runner.Time, '%M:%S.%f').time()
    except:
        Time = None

    try:
        Date = datetime.strptime(runner.Date, "%d %b %Y")
    except:
        Date = None

    try:
        DOB = datetime.strptime(runner.DOB, "%d %b %Y")
    except:
        DOB = None


    athlete, created = AthleteSub4.objects.get_or_create(name = name)

    print(DOB)
    print(Time)
    print(Date)

    if created:
        # new athlete entry
        athlete.DOB = DOB
        athlete.countries = country
        athlete.firstDate = Date
        athlete.firstTime = Time
        athlete.firstLocation = Location
        athlete.bestDate = Date
        athlete.bestTime = Time
        
        if Indoor == 1:
            athlete.IndoorDate = Date
            athlete.IndoorTime = Time
            athlete.IndoorLocation = Location
        elif Indoor == 0:
            athlete.OutdoorDate = Date
            athlete.OutdoorTime = Time
            athlete.OutdoorLocation = Location
    else:
        try:
            if Time < athlete.bestTime:
                athlete.bestDate = Date
                athlete.bestTime = Time         
                if Indoor == 1:
                    athlete.IndoorDate = Date
                    athlete.IndoorTime = Time
                    athlete.IndoorLocation = Location
                elif Indoor == 0:
                    athlete.OutdoorDate = Date
                    athlete.OutdoorTime = Time
                    athlete.OutdoorLocation = Location
        except:
            athlete.bestDate = Date
            athlete.bestTime = Time         
            if Indoor == 1:
                athlete.IndoorDate = Date
                athlete.IndoorTime = Time
                athlete.IndoorLocation = Location
            elif Indoor == 0:
                athlete.OutdoorDate = Date
                athlete.OutdoorTime = Time
                athlete.OutdoorLocation = Location
    athlete.save()
                
        

            



 