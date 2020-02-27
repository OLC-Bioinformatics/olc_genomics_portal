# Django-related imports
from django.urls import reverse_lazy
from django.test import LiveServerTestCase
# Useful things!
from selenium import webdriver
from selenium.webdriver.support.select import Select
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.wait import WebDriverWait
# VirusTyper-specific code
from olc_webportalv2.users.models import User
from olc_webportalv2.vir_typer.models import VirTyperProject,VirTyperRequest, VirTyperResults
__author__ = 'adamkoziol'

# TODO: This has made me realize that sometimes I have buttons as links that look like buttons, and
 # sometimes I have them as actual buttons. Should standardize to one or the other.

class VirusTyper(LiveServerTestCase):
    def setUp(self):
        options = Options()
        options.headless = True
        self.driver = webdriver.Firefox(options=options,executable_path='/data/web/geckodriver')
        user = User.objects.create(username='testuser', password='password', email='test@test.com')
        user.set_password('password')
        user.save()

    def login(self):
        self.driver.get('%s%s' % (self.live_server_url, reverse_lazy('vir_typer:vir_typer_home')))
        self.driver.find_element_by_id('id_login').send_keys('test@test.com')
        self.driver.find_element_by_id('id_password').send_keys('password')
        self.driver.find_element_by_xpath('//button[text()="Sign In"]').click()

    def tearDown(self):
        self.driver.close()

    def test_create_query(self):
        # Login.
        self.login()
        # This takes us to home page - navigate to geneseekr page
        # Dropdown menu
        self.driver.find_element_by_xpath('//button[text()="Analyze"]').click()
        self.driver.find_element_by_link_text('Virus Typer').click()
        # Now move to create query button.
        
        self.driver.find_element_by_link_text('Create A Virus Typer Query').click()
        # Now actually submit a query.

        # self.driver.implicitly_wait(5)
        # print(self.driver.find_element_by_xpath("/html//div[@class='container']/form/a[@href='javascript:void(0)']"))
        # self.driver.find_element_by_css_selector('a.add-row')

        self.driver.find_element_by_id('id_project_name').send_keys('newProject')
        
        self.driver.find_element_by_id('id_form-0-sample_name').send_keys('newSample')
        self.driver.find_element_by_id('id_form-0-LSTS_ID').send_keys('newLSTS ID')
        self.driver.find_element_by_id('id_form-0-lab_ID').send_keys('Burnaby')
        self.driver.find_element_by_id('id_form-0-subunit').send_keys('1')
        self.driver.find_element_by_id('id_form-0-putative_classification').send_keys('Norovirus genogroup 1')
        self.driver.find_element_by_id('id_form-0-isolate_source').send_keys('newIsolate')
        self.driver.find_element_by_id('id_form-0-analyst_name').send_keys('newAnalyst')
        self.driver.find_element_by_id('id_form-0-date_received').send_keys('2020-01-01')

        # self.driver.find_element_by_id('id_form-1-sample_name').send_keys('newSample1')
        # self.driver.find_element_by_id('id_form-1-LSTS_ID').send_keys('newLSTS ID1')
        # self.driver.find_element_by_id('id_form-1-isolate_source').send_keys('newIsolate1')
        # self.driver.find_element_by_id('id_form-1-analyst_name').send_keys('newAnalyst1')
        # self.driver.find_element_by_id('id_form-1-date_received').send_keys('2020-01-02')

        self.driver.find_element_by_xpath("/html//div[@class='container']/form/button[@type='submit']").click()
