# Generated by Django 2.1.11 on 2019-12-17 18:51

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cowbat', '0020_auto_20190913_1543'),
    ]

    operations = [
        migrations.AddField(
            model_name='sequencingrun',
            name='errors',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=24), blank=True, default=list, size=None),
        ),
    ]
