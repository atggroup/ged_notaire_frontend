from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0004_ocr_trash_saved_search"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="encryption_key_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
