from django import template

register = template.Library()

@register.filter
def format_mile_time(t):
    """Formats a time object as M:SS.cc (centiseconds)."""
    if not t:
        return ''
    centiseconds = t.microsecond // 10000
    return f"{t.minute}:{t.second:02d}.{centiseconds:02d}"