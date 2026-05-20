#!/usr/bin/env python
"""
API helper methods for email relay.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from time import sleep

# Django imports
from django.conf import settings


def send_email(*, subject, body, recipient, sender_name=None):
    """Send an email via the AWS SMTP endpoint."""
    from_addr = 'cfia.foodport.donotreply-nepasrepondre.aliport.acia@inspection.gc.ca'
    from_display = (
        formataddr((sender_name, from_addr)) if sender_name else from_addr
    )
    to_addr = recipient

    msg = MIMEMultipart()
    msg['From'] = from_display
    msg['To'] = to_addr
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    for _ in range(50):
        server = None
        try:
            server = smtplib.SMTP('email-smtp.ca-central-1.amazonaws.com', 587)
            server.starttls()
            server.login(
                user=os.environ.get('EMAIL_HOST_USER'),
                password=os.environ.get('EMAIL_HOST_PASSWORD')
            )
            server.sendmail(from_addr, to_addr, msg.as_string())
            break
        except smtplib.SMTPDataError as exc:
            if exc.smtp_code == 554 and b"Access denied" in exc.smtp_error:
                sleep(5)
                continue
            raise
        except smtplib.SMTPServerDisconnected as exc:
            if 'wrong version number' in str(exc):
                sleep(5)
                continue
            raise
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    pass
