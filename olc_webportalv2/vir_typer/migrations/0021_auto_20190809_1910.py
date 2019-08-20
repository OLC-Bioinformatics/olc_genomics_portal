# Generated by Django 2.1.5 on 2019-08-09 19:10

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vir_typer', '0020_virtyperresults'),
    ]

    operations = [
        migrations.AddField(
            model_name='virtyperresults',
            name='allele',
            field=models.CharField(default='ND', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='virtyperresults',
            name='forward_primer',
            field=models.CharField(default='ND', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='virtyperresults',
            name='orientation',
            field=models.CharField(default='ND', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='virtyperresults',
            name='reverse_primer',
            field=models.CharField(default='ND', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='virtyperresults',
            name='trimmed_quality_max',
            field=models.CharField(default='ND', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='virtyperresults',
            name='trimmed_quality_mean',
            field=models.CharField(default='ND', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='virtyperresults',
            name='trimmed_quality_min',
            field=models.CharField(default='ND', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='virtyperresults',
            name='trimmed_quality_stdev',
            field=models.CharField(default='ND', max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='virtyperresults',
            name='trimmed_sequence',
            field=models.CharField(default='ND', max_length=50),
            preserve_default=False,
        ),
    ]
