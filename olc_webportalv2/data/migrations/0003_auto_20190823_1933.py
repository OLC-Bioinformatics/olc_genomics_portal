# Generated by Django 2.1.5 on 2019-08-23 19:33

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('data', '0002_datarequest_user'),
    ]

    operations = [
        migrations.AlterField(
            model_name='datarequest',
            name='missing_seqids',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=24), blank=True, default=list, size=None),
        ),
        migrations.AlterField(
            model_name='datarequest',
            name='seqids',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=24), blank=True, default=list, size=None),
        ),
    ]
