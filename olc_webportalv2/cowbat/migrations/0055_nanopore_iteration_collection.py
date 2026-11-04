# Generated for sequential Nanopore iteration collection.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cowbat', '0054_nanopore_batch_manifest_cascade'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sequencingrun',
            name='workflow_state',
            field=models.CharField(
                choices=(
                    ('created', 'Created'),
                    ('uploading', 'Uploading'),
                    ('upload_complete', 'Upload complete'),
                    ('submitting', 'Submitting'),
                    ('submitted', 'Submitted'),
                    ('processing', 'Processing'),
                    ('ready', 'Ready'),
                    ('stopping', 'Stopping'),
                    ('complete', 'Complete'),
                    ('error', 'Error'),
                ),
                db_index=True,
                default='created',
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name='nanoporeprocessingbatch',
            name='status',
            field=models.CharField(
                choices=(
                    ('pending', 'Pending'),
                    ('published', 'Published'),
                    ('submitting', 'Submitting'),
                    ('submitted', 'Submitted'),
                    ('running', 'Running'),
                    ('complete', 'Complete'),
                    ('failed', 'Failed'),
                ),
                db_index=True,
                default='pending',
                max_length=16,
            ),
        ),
    ]
