from django.contrib import admin
from .models import AthleteSub4

class AthleteSub4Admin(admin.ModelAdmin):
    search_fields = (['name'])

admin.site.register(AthleteSub4, AthleteSub4Admin)


