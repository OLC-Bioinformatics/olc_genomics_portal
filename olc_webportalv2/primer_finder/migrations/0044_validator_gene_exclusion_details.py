from django.db import migrations, models
from django.contrib.postgres.fields import JSONField


class Migration(migrations.Migration):

    dependencies = [
        ('primer_finder', '0043_validator_gene_exclusions'),
    ]

    operations = [
        migrations.AddField(
            model_name='validatorrequest',
            name='gene_exclusion_details',
            field=JSONField(default=dict, blank=True),
        ),
    ]