from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cowbat', '0044_sequencingrun_task_observability'),
    ]

    operations = [
        migrations.CreateModel(
            name='NanoporeFile',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True,
                                        serialize=False, verbose_name='ID')),
                ('relative_path', models.CharField(max_length=512)),
                ('blob_name', models.CharField(max_length=512)),
                ('size_bytes', models.BigIntegerField()),
                ('sha256', models.CharField(blank=True, default='', max_length=64)),
                ('status', models.CharField(choices=[('prepared', 'Prepared'),
                    ('uploading', 'Uploading'), ('uploaded', 'Uploaded'),
                    ('error', 'Error')], default='prepared', max_length=16)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('uploaded_at', models.DateTimeField(blank=True, null=True)),
                ('error', models.TextField(blank=True, default='')),
                ('sequencing_run', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='nanopore_files', to='cowbat.sequencingrun')),
            ],
            options={'unique_together': {('sequencing_run', 'relative_path')}},
        ),
    ]