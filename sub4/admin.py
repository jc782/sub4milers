from django.contrib import admin
from .models import AthleteSub4, Athlete, Performance


class AthleteSub4Admin(admin.ModelAdmin):
    search_fields = ['name']


class PerformanceInline(admin.TabularInline):
    model = Performance
    extra = 0


@admin.register(Athlete)
class AthleteAdmin(admin.ModelAdmin):
    list_display = ['name', 'countries', 'DOB', 'instagram_handle', 'strava_id', 'world_athletics_id']
    search_fields = ['name', 'countries', 'instagram_handle', 'world_athletics_id']
    inlines = [PerformanceInline]


@admin.register(Performance)
class PerformanceAdmin(admin.ModelAdmin):
    list_display = ['athlete', 'time', 'date', 'location', 'is_first_sub4']
    search_fields = ['athlete__name', 'location']
    list_filter = ['is_first_sub4']


admin.site.register(AthleteSub4, AthleteSub4Admin)
