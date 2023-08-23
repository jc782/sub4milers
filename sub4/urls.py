from django.urls import path
from django.contrib import admin
from django.views import generic
from .views import *

urlpatterns = [
    path('admin/', admin.site.urls), 
    path("", viewSub4, name="sub4"),
    path("fastest/", viewSub4Time, name="fastest"),
    
    ]