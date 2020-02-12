from selenium import webdriver
from django.test import LiveServerTestCase
from django.urls import reverse_lazy
from olc_webportalv2.users.models import User
from olc_webportalv2.primer_finder.models import PrimerFinder
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.select import Select

class PrimerFinderIntegrationTest(LiveServerTestCase):
    def setUp(self):
        options = Options()
        options.headless = True
        self.driver = webdriver.Firefox(options=options,executable_path='/data/web/geckodriver')
        user = User.objects.create(username='testuser', password='password', email='test@test.com')
        user.set_password('password')
        user.save()        

    def login(self):
        self.driver.get('%s%s' % (self.live_server_url, reverse_lazy('primer_finder:primer_home')))
        self.driver.find_element_by_id('id_login').send_keys('test@test.com')
        self.driver.find_element_by_id('id_password').send_keys('password')
        self.driver.find_element_by_xpath('//button[text()="Sign In"]').click() 
        
    def tearDown(self):
        self.driver.close()

    def test_create_epcr_request(self):
        # Login.
        test_file = '/data/web/olc_webportalv2/primer_finder/tests/test.fasta'
        self.login()
        # This takes us to home page - navigate to primer_finder page
        # Dropdown menu
        self.driver.find_element_by_xpath('//button[text()="Analyze"]').click()
        self.driver.find_element_by_link_text('Primer Finder').click()
        # Now move to create validation button.
        self.driver.find_element_by_link_text('Create A Primer Validation Request').click()
        # Now actually submit a request.
        self.driver.find_element_by_id('id_primer_file').send_keys(test_file)
        self.driver.find_element_by_xpath("/html//div[@class='container']/form/button[@type='submit']").click()
        self.tearDown

    def test_create_vtyper_request(self):
        # Login.
        test_file = '/data/web/olc_webportalv2/primer_finder/tests/test.fasta'
        self.login()
        # This takes us to home page - navigate to primer_finder page
        # Dropdown menu
        self.driver.find_element_by_xpath('//button[text()="Analyze"]').click()
        self.driver.find_element_by_link_text('Primer Finder').click()
        # Now move to create validation button.
        self.driver.find_element_by_link_text('Create A Primer Validation Request').click()
        # Now actually submit a request.
        select = Select(self.driver.find_element_by_id('id_analysistype'))
        select.select_by_value("vtyper")
        self.driver.find_element_by_xpath("/html//div[@class='container']/form/button[@type='submit']").click()
        self.tearDown

