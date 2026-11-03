from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('api', '0001_initial')]

    operations = [
        migrations.CreateModel(
            name='DesktopPairing',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True,
                                        serialize=False, verbose_name='ID')),
                ('pairing_id', models.CharField(max_length=64, unique=True)),
                ('code_hash', models.CharField(blank=True, max_length=128)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('approved_user', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='desktop_pairings',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]
