from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cowbat', '0050_nanoporerun_scheduler_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='sequencingrun',
            name='rename_status',
            field=models.CharField(blank=True, default='', max_length=16),
        ),
        migrations.AddField(
            model_name='sequencingrun',
            name='rename_target',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
