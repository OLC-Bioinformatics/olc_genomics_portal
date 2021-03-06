# Generated by Django 2.1.5 on 2019-07-26 17:25

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geneseekr', '0027_auto_20190726_1511'),
    ]

    operations = [
        migrations.AddField(
            model_name='prokkarequest',
            name='other_input_files',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(blank=True, default=[], max_length=64), null=True, size=None),
        ),
        migrations.AlterField(
            model_name='prokkarequest',
            name='seqids',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=24), blank=True, default=[], null=True, size=None),
        ),
    ]
