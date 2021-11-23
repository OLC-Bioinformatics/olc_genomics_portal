# Generated by Django 2.1.11 on 2020-03-18 19:28

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sequence_database', '0008_auto_20200318_1926'),
    ]

    operations = [
        migrations.AlterField(
            model_name='databasequery',
            name='database_fields',
            field=models.CharField(choices=[('GENUS', 'Genus'), ('SPECIES', 'Species'), ('MLST', 'MLST Profile'), ('MLSTCC', 'MLST Clonal Complex'), ('RMLST', 'rMLST Profile'), ('GENESEEKR', 'GeneSeekr Profile'), ('SEROVAR', 'Serovar'), ('VTYPER', 'Vtyper Profile'), ('VERSION', 'Pipeline Version')], default='GENUS', max_length=24),
        ),
    ]
