from django.urls import path
from django.views import generic
from .views import *

urlpatterns = [
    path("all/", viewSub4, name="sub4"),
    ]