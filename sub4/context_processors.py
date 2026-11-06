import datetime
from .models import Performance
from .templatetags.time_filters import format_mile_time


def _ordinal(n):
    if 11 <= (n % 100) <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def format_ordinal_date(d):
    """Formats a date like '23rd Feb 2026'."""
    return f"{_ordinal(d.day)} {d.strftime('%b %Y')}"


def site_metadata(request):
    """Provides dynamic last_updated_str and world_record_str to base.html."""
    today = datetime.date.today()
    fastest = Performance.objects.order_by('time').first()
    world_record_str = format_mile_time(fastest.time) if fastest else "3:43.13"
    return {
        'last_updated_str': format_ordinal_date(today),
        'world_record_str': world_record_str,
    }
