from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cowbat", "0057_auto_20260925_1433"),
    ]

    operations = [
        migrations.AddField(
            model_name="sequencingrun",
            name="integration_test",
            field=models.BooleanField(default=False),
        ),
    ]
