# Generated by Django 2.1.5 on 2019-04-01 14:14

import django.contrib.postgres.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geneseekr', '0014_parsnptree_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='parsnptree',
            name='number_diversitree_strains',
            field=models.PositiveIntegerField(blank=True, default=0, null=True),
        ),
        migrations.AddField(
            model_name='parsnptree',
            name='seqids_diversitree',
            field=django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=24), blank=True, default=[], size=None),
        ),
        migrations.AddField(
            model_name='parsnptree',
            name='tree_program',
            field=models.CharField(choices=[('parsnp', 'mashtree')], default='parsnp', max_length=10000),
        ),
    ]
