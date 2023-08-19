from django.shortcuts import render
from django.db.models.expressions import Window
from django.db.models.functions import RowNumber, Coalesce
from django.db.models import Case, When, Value, IntegerField
from django.http import HttpResponse


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