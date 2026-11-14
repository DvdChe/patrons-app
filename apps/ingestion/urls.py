from django.urls import path

from . import views

app_name = "ingestion"

urlpatterns = [
    path("imports/", views.upload, name="upload"),
    path("imports/<int:pk>/", views.review, name="review"),
    path("imports/<int:pk>/discard", views.discard, name="discard"),
    path(
        "imports/<int:pk>/files/<int:file_id>/pages/<int:page>.jpg",
        views.preview,
        name="preview",
    ),
    path("imports/<int:pk>/files/<int:file_id>/image.jpg", views.preview, name="preview_image"),
    path("api/brands/<int:pk>/charts", views.brand_charts, name="brand_charts"),
]
