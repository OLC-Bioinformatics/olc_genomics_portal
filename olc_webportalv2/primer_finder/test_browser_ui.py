import os
import unittest
from unittest.mock import patch

from axe_selenium_python import Axe
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from olc_webportalv2.cowsnphr.models import COWSNPhRRequest
from olc_webportalv2.filezone.models import ContainerName
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

    def assert_axe_has_no_violations(self):
        axe = Axe(self.browser)
        axe.inject()
        violations = axe.run()['violations']
        if violations:
            self.fail(
                f'Axe violations on {self.browser.current_url}:\n'
                f'{axe.report(violations)}'
            )

    def test_homepage_responsive_layout_and_accessible_shell(self):
        self.browser.set_window_size(1280, 900)
        self.open_page('home')
        self.wait.until(
            EC.presence_of_element_located((By.ID, 'main-content'))
        )

        self.assertTrue(self.browser.find_element(By.TAG_NAME, 'html').get_attribute('lang'))
        self.assertEqual(
            len(self.browser.find_elements(By.CSS_SELECTOR, '#main-content h1')),
            1,
        )
        skip_link = self.browser.find_element(
            By.CSS_SELECTOR, '.portal-skip-link'
        )
        self.assertEqual(skip_link.get_attribute('href').split('#')[-1], 'main-content')
        self.assertTrue(
            self.browser.find_element(By.CSS_SELECTOR, 'main#main-content')
        )

        language_select = self.browser.find_element(
            By.ID, 'portal-language'
        )
        self.assertTrue(
            self.browser.find_element(
                By.CSS_SELECTOR, 'label[for="portal-language"]'
            ).get_attribute('textContent').strip()
        )
        nav_toggle = self.browser.find_element(
            By.CSS_SELECTOR, '.navbar-toggler'
        )
        self.assertTrue(nav_toggle.get_attribute('aria-label').strip())
        self.assertTrue(
            self.browser.find_element(
                By.ID, nav_toggle.get_attribute('aria-controls')
            )
        )
        self.assertTrue(language_select.is_displayed())

        cards = self.browser.find_elements(
            By.CSS_SELECTOR, '.card-grid > .row:first-child > .card'
        )
        self.assertEqual(len(cards), 3)
        for width, expected_columns in ((390, 1), (768, 2), (1280, 3)):
            with self.subTest(viewport_width=width):
                self.browser.set_window_size(width, 900)
                self.assertTrue(
                    self.browser.execute_script(
                        'return document.documentElement.scrollWidth <= '
                        'document.documentElement.clientWidth'
                    ),
                    f'Horizontal overflow at viewport width {width}px',
                )
                positions = [card.location for card in cards]
                for index in range(1, expected_columns):
                    self.assertAlmostEqual(
                        positions[0]['y'], positions[index]['y'], delta=2
                    )
                if expected_columns < len(cards):
                    self.assertGreater(
                        positions[expected_columns]['y'],
                        positions[0]['y'],
                    )

        explore = self.browser.find_element(By.ID, 'metadataDropdownButton')
        explore.send_keys(Keys.ENTER)
        self.wait.until(
            lambda _browser: explore.get_attribute('aria-expanded') == 'true'
        )
        self.assertTrue(
            self.browser.find_element(
                By.CSS_SELECTOR,
                '.dropdown-menu[aria-labelledby="metadataDropdownButton"]',
            ).is_displayed()
        )
        explore.send_keys(Keys.ESCAPE)
        self.wait.until(
            lambda _browser: explore.get_attribute('aria-expanded') == 'false'
        )

        analyze = self.browser.find_element(By.ID, 'dropdownMenuButton')
        analyze.send_keys(Keys.ENTER)
        self.wait.until(
            lambda _browser: analyze.get_attribute('aria-expanded') == 'true'
        )
        self.assertTrue(
            self.browser.find_element(
                By.CSS_SELECTOR,
                '.dropdown-menu[aria-labelledby="dropdownMenuButton"]',
            ).is_displayed()
        )
        self.assert_axe_has_no_violations()

    def test_filezone_cards_share_a_row_on_desktop(self):
        self.authenticate_browser()
        self.browser.set_window_size(1280, 900)
        self.open_page('filezone:filezone')

        cards = self.browser.find_elements(
            By.CSS_SELECTOR, '.portal-choice-grid > .row > .card'
        )
        self.assertEqual(len(cards), 3)
        positions = [card.location for card in cards]
        self.assertTrue(
            all(
                abs(position['y'] - positions[0]['y']) <= 2
                for position in positions[1:]
            )
        )

        self.browser.set_window_size(390, 900)
        self.assertTrue(
            self.browser.execute_script(
                'return document.documentElement.scrollWidth <= '
                'document.documentElement.clientWidth'
            ),
            'Horizontal overflow at viewport width 390px',
        )

    def test_validator_form_controls_have_accessible_names(self):
        self.authenticate_browser()
        self.open_page('primer_finder:primer_validator_request')

        controls = self.browser.find_elements(
            By.CSS_SELECTOR,
            '#main-content input:not([type="hidden"]), '
            '#main-content select, #main-content textarea',
        )
        unlabeled_controls = []
        for control in controls:
            if not control.is_displayed():
                continue
            has_label = self.browser.execute_script(
                'return Boolean(arguments[0].labels?.length)',
                control,
            )
            has_aria_name = bool(
                control.get_attribute('aria-label')
                or control.get_attribute('aria-labelledby')
            )
            if not has_label and not has_aria_name:
                unlabeled_controls.append(
                    control.get_attribute('id') or control.get_attribute('name')
                )

        self.assertEqual(unlabeled_controls, [])
        self.assert_axe_has_no_violations()

    def test_processing_email_checkbox_has_a_label_and_supports_space(self):
        self.user.email = 'browser@example.org'
        self.user.save(update_fields=['email'])
        request = ValidatorRequest.objects.create(
            user=self.user,
            project_name='browser-validator-email-accessibility',
            status='Processing',
            emails_array=['browser@example.org'],
            forward_primer='ACGT',
            reverse_primer='TGCA',
        )
        self.authenticate_browser()
        self.open_page(
            'primer_finder:primer_validator_processing',
            validator_pk=request.pk,
        )

        checkbox = self.browser.find_element(By.ID, 'cbx')
        label = self.browser.execute_script(
            'return Array.from(arguments[0].labels, '
            '(element) => element.textContent.trim()).join(" ")',
            checkbox,
        )
        self.assertIn('Would you like to add another email?', label)
        email_form = self.browser.find_element(By.ID, 'addEmail')
        self.assertFalse(email_form.is_displayed())

        checkbox.send_keys(Keys.SPACE)
        self.wait.until(lambda _browser: email_form.is_displayed())
        self.assertTrue(checkbox.is_selected())
        self.assert_axe_has_no_violations()

    def test_validator_dashboard_action_column_has_a_header(self):
        self.authenticate_browser()
        self.open_page('primer_finder:primer_validator_home')

        headers = self.browser.find_elements(
            By.CSS_SELECTOR, '#primer_validator-request-table thead th'
        )
        self.assertTrue(headers)
        self.assertTrue(
            all(header.get_attribute('scope') == 'col' for header in headers)
        )
        action_header_text = self.browser.execute_script(
            'return arguments[0].textContent.trim()',
            headers[-1],
        )
        self.assertEqual(action_header_text, 'Actions')
        self.assert_axe_has_no_violations()

    @patch(
        'olc_webportalv2.filezone.views.list_blobs',
        return_value=(
            {
                'sample.fasta': {
                    'pk': 1,
                    'blob_name': 'sample.fasta',
                    'blob_size': '1 KB',
                    'blob_date': '2026/01/01',
                    'blob_md5': 'checksum',
                    'blob_download_link': 'https://storage.example/download',
                }
            },
            {},
        ),
    )
    def test_filezone_container_view_passes_axe(self, _list_blobs):
        container = ContainerName.objects.create(
            container_name='axe-accessibility-test'
        )
        self.authenticate_browser()
        self.open_page(
            'filezone:container_view', filezone_pk=container.pk
        )
        self.wait.until(
            EC.presence_of_element_located((By.ID, 'file-system-view'))
        )
        self.browser.execute_script(
            "document.getElementById('file-system-view').style.display = 'block';"
            "document.querySelectorAll('tr.folder, tr.file').forEach("
            "row => row.classList.add('visible'));"
        )

        self.assert_axe_has_no_violations()

    def test_cowsnphr_tree_controls_pass_axe(self):
        request = COWSNPhRRequest.objects.create(
            user=self.user,
            project_name='axe-tree-accessibility-test',
            phylogenetic_tree='((sample-a,sample-b),sample-c);',
        )
        self.open_page(
            'cowsnphr:cowsnphr_tree', cowsnphr_pk=request.pk
        )
        self.wait.until(EC.presence_of_element_located((By.ID, 'tree')))

        self.assertTrue(
            self.browser.find_element(By.TAG_NAME, 'html').get_attribute('lang')
        )
        self.assertEqual(
            self.browser.find_element(By.ID, 'dw-s1').get_attribute(
                'aria-hidden'
            ),
            'false',
        )
        self.assertIsNone(
            self.browser.find_element(By.ID, 'dw-s1').get_attribute(
                'aria-expanded'
            )
        )
        self.assert_axe_has_no_violations()

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
        self.assert_axe_has_no_violations()

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
