from argparse import ArgumentParser
import django
import os

parentdir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.sys.path.insert(0, parentdir)
print(parentdir)
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.prod'
django.setup()

from olc_webportalv2.cowbat.tasks import run_cowbat_batch


def cli():
    parser = ArgumentParser()
    parser.add_argument('pk',
                        type=int,
                        help='The primary key of the COWBAT run that needs to be started')
    arguments = parser.parse_args()
    run_cowbat_batch(arguments.pk)


if __name__ == '__main__':
    cli()
