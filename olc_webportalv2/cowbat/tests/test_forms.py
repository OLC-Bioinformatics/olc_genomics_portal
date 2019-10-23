from django.test import TestCase, Client
from django import forms

from olc_webportalv2.cowbat.forms import RunNameForm
from olc_webportalv2.geneseekr.forms import EmailForm


class FormTest(TestCase):

    def test_run_form_external_lab(self):
        form = RunNameForm({
            'run_name': '191218_CAL'
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data.get('run_name'), '191218_CAL')

    def test_run_form_miseq_id(self):
        form = RunNameForm({
            'run_name': '191218_M02345'
        })
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data.get('run_name'), '191218_M02345')

    def test_run_form_invalid_name_date_second(self):
        form = RunNameForm({
            'run_name': 'M02345_191215'
        })
        self.assertFalse(form.is_valid())
        try:
            form.cleaned_data.get('run_name')
        except forms.ValidationError as e:
            self.assertEqual('BadRunName', e.code)

    def test_run_form_invalid_date_too_short(self):
        form = RunNameForm({
            'run_name': '12345_CAL'
        })
        self.assertFalse(form.is_valid())
        try:
            form.cleaned_data.get('run_name')
        except forms.ValidationError as e:
            self.assertEqual('BadRunName', e.code)

    def test_run_form_invalid_date_too_long(self):
        form = RunNameForm({
            'run_name': '1234567_CAL'
        })
        self.assertFalse(form.is_valid())
        try:
            form.cleaned_data.get('run_name')
        except forms.ValidationError as e:
            self.assertEqual('BadRunName', e.code)

    def test_run_form_invalid_lowercase(self):
        form = RunNameForm({
            'run_name': '1234567_aaa'
        })
        self.assertFalse(form.is_valid())
        try:
            form.cleaned_data.get('run_name')
        except forms.ValidationError as e:
            self.assertEqual('BadRunName', e.code)

    # These pretty much just test that we actually remembered to use an email form
    def test_email_form_good(self):
        form = EmailForm({
            'email': 'good_email@email.good'
        })
        self.assertTrue(form.is_valid())

    def test_email_form_bad(self):
        form = EmailForm({
            'email': 'not_an_email_at_all'
        })
        self.assertFalse(form.is_valid())
