from django.contrib.postgres.fields import JSONField
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('geneseekr', '0046_geneseekrrequest_blast_options'),
    ]

    operations = [
        migrations.AddField(
            model_name='geneseekrrequest',
            name='benchmark_versions',
            field=JSONField(default=dict, blank=True),
        ),
    ]