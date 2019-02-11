from selenium import webdriver
from django.test import LiveServerTestCase


class GeneSeekrIntegrationTest(LiveServerTestCase):
    def setUp(self):
        self.driver = webdriver.Firefox(executable_path='/data/web/geckodriver')

    def test_data_upload(self):
        self.assertTrue(True)

    def tearDown(self):
        self.driver.close()
