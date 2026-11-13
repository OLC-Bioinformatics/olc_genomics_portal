import os
import unittest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from olc_webportalv2.primer_finder.models import (
    PrimerVerifierRequest,
    ValidatorPanel,
    ValidatorPrimerSet,
    ValidatorRequest,
)
from olc_webportalv2.users.models import User

SELENIUM_URL = os.environ.get('FOODPORT_SELENIUM_URL')


@unittest.skipUnless(
    SELENIUM_URL,
    'Set FOODPORT_SELENIUM_URL to run browser-driven Primer Finder tests.',
)
class PrimerFinderBrowserUiTests(StaticLiveServerTestCase):
    host = 'test-runner'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        options = webdriver.FirefoxOptions()
        options.add_argument('-headless')
        cls.browser = webdriver.Remote(
            command_executor=SELENIUM_URL,
            options=options,
        )
        cls.addClassCleanup(cls.browser.quit)

    def setUp(self):
        self.user = User.objects.create_user(
            username='primer-browser-user',
            password='browser-test-password',
        )
        self.wait = WebDriverWait(self.browser, 10)

    def authenticate_browser(self):
        self.client.force_login(self.user)
        self.browser.get(self.live_server_url)
        self.browser.delete_all_cookies()
        self.browser.add_cookie(
            {
                'name': settings.SESSION_COOKIE_NAME,
                'value': self.client.cookies[
                    settings.SESSION_COOKIE_NAME
                ].value,
            }
        )

    def open_page(self, route, **kwargs):
        self.browser.get(self.live_server_url + reverse(route, kwargs=kwargs))

    def test_add_target_button_adds_a_target_form(self):
        self.authenticate_browser()
        self.open_page('primer_finder:primer_validator_request')

        total_forms = self.browser.find_element(
            By.ID, 'id_targets-TOTAL_FORMS'
        )
        self.assertEqual(total_forms.get_attribute('value'), '1')
        self.browser.find_element(By.ID, 'add-target').click()

        added_target = self.wait.until(
            EC.presence_of_element_located(
                (By.NAME, 'targets-1-forward_primer')
            )
        )
        self.assertTrue(added_target.is_displayed())
        self.assertEqual(total_forms.get_attribute('value'), '2')

    def test_result_detail_button_toggles_the_matching_rows(self):
        request = ValidatorRequest.objects.create(
            user=self.user,
            project_name='browser-validator-results',
            summary=(
                '{"invA":{"inclusivity":{"combined":{"forward_details":[]},'
                '"listeria":{"forward_details":[["1:A>G",1]],'
                '"positive":1,"total":1,"percent":100}}}}'
            ),
            forward_primer='ACGT',
            reverse_primer='TGCA',
        )
        ValidatorPrimerSet.objects.create(
            validator_request=request,
            primer_name='invA',
        )
        ValidatorPanel.objects.create(
            validator_request=request,
            panel='inclusivity',
            genus='Listeria',
        )
        self.authenticate_browser()
        self.open_page(
            'primer_finder:primer_validator_results',
            validator_pk=request.pk,
        )

        toggle = self.wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, '[data-primer-detail-toggle]')
            )
        )
        detail_row = self.browser.find_element(
            By.ID, 'invAinclusivitylisteriaRow'
        )
        self.assertIsNotNone(detail_row.get_attribute('hidden'))

        toggle.click()
        self.wait.until(
            lambda _browser: detail_row.get_attribute('hidden') is None
        )
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'true')
        self.assertIn('Hide', toggle.get_attribute('value'))

        toggle.click()
        self.wait.until(
            lambda _browser: detail_row.get_attribute('hidden') is not None
        )
        self.assertEqual(toggle.get_attribute('aria-expanded'), 'false')
        self.assertIn('Show', toggle.get_attribute('value'))

    def test_delete_confirmation_submits_both_dashboard_forms(self):
        projects = (
            (
                ValidatorRequest,
                'primer_finder:primer_validator_home',
                'browser-validator-delete',
            ),
            (
                PrimerVerifierRequest,
                'primer_finder:primer_verifier_home',
                'browser-verifier-delete',
            ),
        )
        self.authenticate_browser()

        for model, route, project_name in projects:
            with self.subTest(project=project_name):
                project = model.objects.create(
                    user=self.user,
                    project_name=project_name,
                    status='Complete',
                )
                self.open_page(route)
                row = self.browser.find_element(By.ID, f'row{project.pk}')
                self.browser.find_element(By.ID, f'btn{project.pk}').click()
                self.wait.until(
                    EC.visibility_of_element_located(
                        (By.CSS_SELECTOR, '.swal2-popup')
                    )
                )
                self.browser.find_element(
                    By.CSS_SELECTOR, '.swal2-confirm'
                ).click()
                self.wait.until(EC.staleness_of(row))
                self.assertFalse(model.objects.filter(pk=project.pk).exists())

    def test_cancelled_delete_keeps_the_project(self):
        project = PrimerVerifierRequest.objects.create(
            user=self.user,
            project_name='browser-verifier-cancel',
            status='Complete',
        )
        self.authenticate_browser()
        self.open_page('primer_finder:primer_verifier_home')
        row = self.browser.find_element(By.ID, f'row{project.pk}')
        self.browser.find_element(By.ID, f'btn{project.pk}').click()
        self.wait.until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, '.swal2-popup'))
        )
        self.browser.find_element(By.CSS_SELECTOR, '.swal2-cancel').click()
        self.wait.until(
            EC.staleness_of(
                self.browser.find_element(By.CSS_SELECTOR, '.swal2-popup')
            )
        )

        self.assertTrue(
            PrimerVerifierRequest.objects.filter(pk=project.pk).exists()
        )
        self.assertTrue(row.is_displayed())
