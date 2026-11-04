from django.db import migrations, models
import django.contrib.postgres.fields.jsonb


class Migration(migrations.Migration):

    dependencies = [
        ('cowbat', '0049_nanoporeprocessingbatch_azure_pool_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='nanoporerun', name='barcode_kit',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='nanoporerun', name='barcode_values',
            field=django.contrib.postgres.fields.jsonb.JSONField(
                blank=True, default=list
            ),
        ),
        migrations.AddField(
            model_name='nanoporerun', name='sample_metadata',
            field=django.contrib.postgres.fields.jsonb.JSONField(
                blank=True, default=dict
            ),
        ),
        migrations.AddField(
            model_name='nanoporerun', name='scheduler_input_csv',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='nanoporerun', name='scheduler_metadata_csv',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='nanoporerun', name='scheduler_input_sha256',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.AddField(
            model_name='nanoporerun', name='scheduler_metadata_sha256',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]