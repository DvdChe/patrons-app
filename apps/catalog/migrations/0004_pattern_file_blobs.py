# Content-addressed pattern files. The table is empty when this runs (no file import yet),
# so the new columns need no default.
from django.db import migrations, models

import apps.catalog.models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_verbose_names"),
    ]

    operations = [
        migrations.AddField(
            model_name="patternfile",
            name="sha256",
            field=models.CharField(db_index=True, max_length=64),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="patternfile",
            name="size",
            field=models.PositiveBigIntegerField(),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="patternfile",
            name="file",
            field=models.FileField(
                max_length=500, upload_to=apps.catalog.models.pattern_file_path
            ),
        ),
        migrations.AlterModelOptions(
            name="patternfile",
            options={"ordering": ["kind", "original_name"]},
        ),
        migrations.AddConstraint(
            model_name="patternfile",
            constraint=models.UniqueConstraint(
                fields=("pattern", "original_name"), name="patternfile_unique_name"
            ),
        ),
    ]
