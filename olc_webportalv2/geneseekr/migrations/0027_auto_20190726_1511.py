# Generated by Django 2.1.5 on 2019-07-26 15:11

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geneseekr', '0026_nearestneighbors_created_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='nearestneighbors',
            name='uploaded_file_name',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AlterField(
            model_name='nearestneighbors',
            name='seqid',
            field=models.CharField(blank=True, max_length=24, null=True),
        ),
    ]
