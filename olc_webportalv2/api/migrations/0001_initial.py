from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [('users', '0004_user_language')]
    operations = [
        migrations.CreateModel(
            name='EntraIdentity',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True,
                                        serialize=False, verbose_name='ID')),
                ('tenant_id', models.CharField(max_length=128)),
                ('object_id', models.CharField(max_length=128)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'unique_together': {('tenant_id', 'object_id')}},
        ),
        migrations.CreateModel(
            name='DesktopAccessToken',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True,
                                        serialize=False, verbose_name='ID')),
                ('key', models.CharField(max_length=64, unique=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
    ]