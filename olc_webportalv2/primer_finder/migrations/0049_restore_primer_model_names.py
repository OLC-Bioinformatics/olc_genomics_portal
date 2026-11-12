from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('primer_finder', '0048_auto_20260831_1402'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RenameModel(
                    old_name='VerifierRequest',
                    new_name='PrimerVerifierRequest',
                ),
                migrations.AlterModelTable(
                    name='primerverifierrequest',
                    table='primer_finder_verifierrequest',
                ),
                migrations.RenameModel(
                    old_name='ValidatorAzureRequest',
                    new_name='PrimerValidatorAzureRequest',
                ),
                migrations.AlterModelTable(
                    name='primervalidatorazurerequest',
                    table='primer_finder_validatorazurerequest',
                ),
            ],
        ),
    ]
