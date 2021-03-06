# -*- coding: utf-8 -*-
# Generated by Django 1.10.8 on 2018-09-04 15:25
from __future__ import unicode_literals

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='MetaDataRequest',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('seqids', django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=24), blank=True, default=[], size=None)),
            ],
        ),
        migrations.CreateModel(
            name='SequenceData',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('seqid', models.CharField(max_length=24)),
                ('quality', models.CharField(choices=[('Fail', 'Fail'), ('Pass', 'Pass'), ('Reference', 'Reference')], max_length=128)),
                ('genus', models.CharField(max_length=48)),
            ],
        ),
    ]
