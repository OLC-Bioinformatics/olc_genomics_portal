import os
import time
import selenium
from selenium import webdriver
from django.test import LiveServerTestCase
from django.urls import reverse_lazy
from olc_webportalv2.users.models import User


class CowbatIntegrationTest(LiveServerTestCase):
    def setUp(self):
        self.driver = webdriver.Firefox(executable_path='/data/web/geckodriver')
        user = User.objects.create(username='testuser', password='password', email='test@test.com')
        user.set_password('password')
        user.save()

    def login(self):
        self.driver.get('%s%s' % (self.live_server_url, reverse_lazy('geneseekr:geneseekr_home')))
        self.driver.find_element_by_id('id_login').send_keys('test@test.com')
        self.driver.find_element_by_id('id_password').send_keys('password')
        self.driver.find_element_by_xpath('//button[text()="Sign In"]').click()

    def test_cowbat_run(self):
        # Login.
        self.login()
        # This takes us to home page - navigate to cowbat page
        self.driver.find_element_by_link_text('Assembly').click()
        # Start uploading a new run
        self.driver.find_element_by_link_text('Upload a New Run').click()
        # This takes us to the upload sequence metadata page - need to upload files, put in run name, validate, and then
        # upload
        test_file_dir = '/data/web/olc_webportalv2/test_files'
        self.driver.find_element_by_id('id_run_name').send_keys('123456_TEST')
        # Dropzone isn't loading for travis? Give up to a minute to find input
        start_time = time.time()
        max_time = 60
        element_found = False
        while time.time() - start_time < max_time and element_found is False:
            try:
                self.driver.find_element_by_css_selector('.dz-hidden-input')
                element_found = True
            except selenium.common.exceptions.NoSuchElementException:
                time.sleep(1)
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'SampleSheet.csv'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'config.xml'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'CompletedJobInfo.xml'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'GenerateFASTQRunStatistics.xml'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'RunInfo.xml'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'runParameters.xml'))
        # Validate!
        self.driver.find_element_by_xpath('//button[text()="Validate Metadata Files"]').click()
        self.driver.find_element_by_xpath('//button[text()="Upload Metadata Files"]').click()
        # Now we should be on the upload interop page.
        time.sleep(10)  # Give time for upload to finish.
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'InterOp/ControlMetricsOut.bin'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'InterOp/CorrectedIntMetricsOut.bin'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'InterOp/ErrorMetricsOut.bin'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'InterOp/ExtractionMetricsOut.bin'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'InterOp/IndexMetricsOut.bin'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'InterOp/QMetricsOut.bin'))
        self.driver.find_element_by_css_selector('.dz-hidden-input').send_keys(os.path.join(test_file_dir, 'InterOp/TileMetricsOut.bin'))
        self.driver.find_element_by_xpath('//button[text()="Validate InterOp Files"]').click()
        self.driver.find_element_by_xpath('//button[text()="Upload InterOp Files"]').click()
        time.sleep(10)  # Give time for upload to finish.
        # Now should be on sequence data upload page.
        self.assertTrue('upload_sequence_data' in self.driver.current_url)

    def tearDown(self):
        self.driver.close()
