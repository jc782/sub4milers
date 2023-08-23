from django.shortcuts import render
from django.db.models.expressions import Window
from django.db.models.functions import Coalesce
from django.db.models import Value
from django.db.models.functions import Least

from .models import AthleteSub4

import logging
import datetime

logger = logging.getLogger(__name__)

# Create your views here.

def viewSub4(request):
    the_past = datetime.datetime.now().date()
    athletes = AthleteSub4.objects.all().annotate(first_Date=Coalesce('firstDate', Value(the_past))).order_by('first_Date')
    context = {}
    context["athletes"] = athletes
    return render(request, 'sub4table.html', context)


def viewSub4Time(request):
    the_past = datetime.datetime.now().date()
    ## need to order by best time instead

    athletes = AthleteSub4.objects.all().order_by('bestTime')

    #athletes = AthleteSub4.objects.all().annotate(first_Date=Coalesce('firstDate', Value(the_past))).order_by('first_Date')
    context = {}
    context["athletes"] = athletes
    return render(request, 'sub4fastest.html', context)
