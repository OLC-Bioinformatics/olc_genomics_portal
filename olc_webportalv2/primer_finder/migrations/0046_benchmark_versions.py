from django.contrib.postgres.fields import JSONField
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('primer_finder', '0045_validator_sample_salmonella_or'),
    ]

    operations = [
        migrations.AddField(
            model_name='verifierrequest',
            name='benchmark_versions',
            field=JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name='validatorrequest',
            name='benchmark_versions',
            field=JSONField(default=dict, blank=True),
        ),
    ]