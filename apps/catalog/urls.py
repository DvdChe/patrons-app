from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("", views.catalogue, name="catalogue"),
    path("patterns/<int:pk>/sheet", views.pattern_sheet, name="sheet"),
    path("api/catalogue", views.api_catalogue, name="api_catalogue"),
    path("api/recommendations", views.api_recommendations, name="api_recommendations"),
    path("api/profiles", views.api_profiles, name="api_profiles"),
    path("api/profiles/<int:pk>", views.api_profile, name="api_profile"),
]
