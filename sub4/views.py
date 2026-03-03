from django.shortcuts import render
from django.db.models import Min, Subquery, OuterRef, Count
from django.db.models.functions import Coalesce, ExtractYear
from django.db.models import Value
import datetime
import logging

from .models import Athlete, Performance

logger = logging.getLogger(__name__)


def viewSub4(request):
    the_past = datetime.datetime.now().date()
    athletes = (
        Athlete.objects
        .annotate(first_sub4_date=Coalesce(
            Subquery(
                Performance.objects.filter(athlete=OuterRef('pk'), is_first_sub4=True)
                .values('date')[:1]
            ),
            Value(the_past)
        ))
        .annotate(first_sub4_time=Subquery(
            Performance.objects.filter(athlete=OuterRef('pk'), is_first_sub4=True)
            .values('time')[:1]
        ))
        .annotate(best_time=Min('performances__time'))
        .order_by('first_sub4_date')
    )
    return render(request, 'sub4table.html', {'athletes': athletes})


def viewSub4Time(request):
    athletes = (
        Athlete.objects
        .annotate(best_time=Min('performances__time'))
        .annotate(best_date=Subquery(
            Performance.objects
            .filter(athlete=OuterRef('pk'))
            .order_by('time')
            .values('date')[:1]
        ))
        .order_by('best_time')
    )
    return render(request, 'sub4fastest.html', {'athletes': athletes})

def mostRecent(request):
    the_past = datetime.datetime(1900, 1, 1).date()
    athletes = (
        Athlete.objects
        .annotate(first_sub4_date=Coalesce(
            Subquery(
                Performance.objects.filter(athlete=OuterRef('pk'), is_first_sub4=True)
                .values('date')[:1]
            ),
            Value(the_past)
        ))
        .annotate(first_sub4_time=Subquery(
            Performance.objects.filter(athlete=OuterRef('pk'), is_first_sub4=True)
            .values('time')[:1]
        ))
        .order_by('-first_sub4_date')
    )
    return render(request, 'sub4recent.html', {'athletes': athletes})

def mostPerformances(request):
    from django.db.models import Count
    athletes = (
        Athlete.objects
        .annotate(performance_count=Count('performances'))
        .annotate(best_time=Min('performances__time'))
        .order_by('-performance_count', 'best_time')
    )
    return render(request, 'sub4mostperformances.html', {'athletes': athletes})


def analytics(request):
    """Analytics dashboard with charts and fun facts."""
    import json
    from collections import defaultdict

    # ── Fun facts ─────────────────────────────────────────────────────────────
    total_athletes   = Athlete.objects.count()
    total_performances = Performance.objects.count()

    fastest = Performance.objects.order_by('time').select_related('athlete').first()
    most_performances = (
        Athlete.objects
        .annotate(count=Count('performances'))
        .order_by('-count')
        .first()
    )
    most_recent = Performance.objects.filter(date__isnull=False).order_by('-date').select_related('athlete').first()
    most_countries = (
        Athlete.objects
        .values('countries')
        .annotate(count=Count('id'))
        .order_by('-count')
        .first()
    )

    fun_facts = {
        'total_athletes':      total_athletes,
        'total_performances':  total_performances,
        'fastest_time':        fastest.time if fastest else None,
        'fastest_athlete':     fastest.athlete.name if fastest else None,
        'fastest_date':        fastest.date if fastest else None,
        'most_perf_name':      most_performances.name if most_performances else None,
        'most_perf_count':     most_performances.count if most_performances else None,
        'most_recent_name':    most_recent.athlete.name if most_recent else None,
        'most_recent_date':    most_recent.date if most_recent else None,
        'most_recent_time':    most_recent.time if most_recent else None,
    }

    # ── Performances per year ─────────────────────────────────────────────────
    perfs_by_year = defaultdict(int)
    for p in Performance.objects.filter(date__isnull=False).values_list('date', flat=True):
        perfs_by_year[p.year] += 1
    perfs_by_year = dict(sorted(perfs_by_year.items()))

    # ── Cumulative unique athletes over time ───────────────────────────────────
    # Use the earliest performance date per athlete as their "debut" year
    debut_years = defaultdict(int)
    athlete_first_perf = (
        Performance.objects
        .filter(date__isnull=False)
        .values('athlete_id')
        .annotate(first_date=Min('date'))
    )
    for row in athlete_first_perf:
        debut_years[row['first_date'].year] += 1
    debut_years = dict(sorted(debut_years.items()))

    cumulative = {}
    running = 0
    for year, count in debut_years.items():
        running += count
        cumulative[year] = running

    # ── Depth over time: 10th and 100th fastest times each year ───────────────
    from django.db.models.functions import ExtractYear

    depth_data = defaultdict(list)
    perfs_with_year = (
        Performance.objects
        .filter(date__isnull=False)
        .annotate(year=ExtractYear('date'))
        .values('year', 'time')
        .order_by('year', 'time')
    )
    for row in perfs_with_year:
        depth_data[row['year']].append(row['time'])

    depth_10th  = {}
    depth_100th = {}
    for year, times in sorted(depth_data.items()):
        if len(times) >= 10:
            t = times[9]
            depth_10th[year] = t.minute * 60 + t.second + t.microsecond / 1_000_000
        if len(times) >= 100:
            t = times[99]
            depth_100th[year] = t.minute * 60 + t.second + t.microsecond / 1_000_000

    # ── Country counts for world map ───────────────────────────────────────────
    country_counts = defaultdict(int)
    for countries_str in Athlete.objects.values_list('countries', flat=True):
        if not countries_str:
            continue
        for code in countries_str.replace('/', ',').split(','):
            code = code.strip()
            if code:
                country_counts[code] += 1
    print(country_counts)

    context = {
        'fun_facts': fun_facts,
        'perfs_by_year_json':  json.dumps(perfs_by_year),
        'cumulative_json':     json.dumps(cumulative),
        'depth_10th_json':     json.dumps(depth_10th),
        'depth_100th_json':    json.dumps(depth_100th),
        'country_counts_json': json.dumps(dict(country_counts)),
    }
    return render(request, 'sub4analytics.html', context)