import os


from .prod import *


ENVIRONMENT = 'TEST'
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
os.environ.pop('EMAIL_HOST_USER', None)
os.environ.pop('EMAIL_HOST_PASSWORD', None)