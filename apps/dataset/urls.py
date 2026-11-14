from django.urls import path

from . import views

app_name = "dataset"

urlpatterns = [
    path("audit/", views.index, name="index"),
    path("audit/start", views.start, name="start"),
    path("audit/report", views.report, name="report"),
    path("audit/<int:pk>/", views.audit, name="audit"),
]
