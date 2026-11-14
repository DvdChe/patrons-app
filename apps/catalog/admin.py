from django.contrib import admin

from .models import (
    Brand,
    MeasurementProfile,
    Pattern,
    PatternFile,
    SizeChart,
    SizeChartRow,
    Tag,
)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ["name", "seam_allowance"]
    list_filter = ["seam_allowance"]
    search_fields = ["name", "aliases"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["group", "value", "position"]
    list_filter = ["group"]


class PatternFileInline(admin.TabularInline):
    model = PatternFile
    extra = 0


@admin.register(Pattern)
class PatternAdmin(admin.ModelAdmin):
    list_display = ["name", "brand", "family", "audience", "difficulty", "seam_allowance", "sewn"]
    list_filter = ["family", "audience", "difficulty", "fabric_kind", "seam_allowance", "sewn"]
    search_fields = ["search_text"]
    autocomplete_fields = ["brand"]
    filter_horizontal = ["tags", "brand_size_charts"]
    inlines = [PatternFileInline]


class SizeChartRowInline(admin.TabularInline):
    model = SizeChartRow
    extra = 0


@admin.register(SizeChart)
class SizeChartAdmin(admin.ModelAdmin):
    list_display = ["__str__", "measure_type"]
    list_filter = ["measure_type"]
    inlines = [SizeChartRowInline]


@admin.register(MeasurementProfile)
class MeasurementProfileAdmin(admin.ModelAdmin):
    list_display = ["name", "owner"]
