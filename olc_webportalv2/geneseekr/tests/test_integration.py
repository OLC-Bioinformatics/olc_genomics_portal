from selenium import webdriver
from django.test import LiveServerTestCase
from django.urls import reverse_lazy
from olc_webportalv2.users.models import User
from olc_webportalv2.geneseekr.models import GeneSeekrRequest
from selenium.webdriver.firefox.options import Options

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
# TODO: This has made me realize that sometimes I have buttons as links that look like buttons, and
 # sometimes I have them as actual buttons. Should standardize to one or the other.


class GeneSeekrIntegrationTest(LiveServerTestCase):
    def setUp(self):
        options = Options()
        options.headless = True
        self.driver = webdriver.Firefox(options=options,executable_path='/data/web/geckodriver')
        user = User.objects.create(username='testuser', password='password', email='test@test.com')
        user.set_password('password')
        user.save()        

    def login(self):
        self.driver.get('%s%s' % (self.live_server_url, reverse_lazy('geneseekr:geneseekr_home')))
        self.driver.find_element_by_id('id_login').send_keys('test@test.com')
        self.driver.find_element_by_id('id_password').send_keys('password')
        self.driver.find_element_by_xpath('//button[text()="Sign In"]').click() 

    def test_create_query(self):
        # Login.
        query_sequence = '>sequence\nACTGATCGTACGTACTGGTCTGATACTGGTCTAGCATGCTGA'
        self.login()
        # This takes us to home page - navigate to geneseekr page
        # Dropdown menu
        self.driver.find_element_by_xpath('//button[text()="Analyze"]').click()
        self.driver.find_element_by_link_text('Find Genes').click()
        # Now move to create query button.
        WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.LINK_TEXT, 'Create A GeneSeekr Query'))
            )
        self.driver.find_element_by_link_text('Create A GeneSeekr Query').click()
        print(self.driver.current_url)
        # # Now actually submit a query.
        self.driver.find_element_by_id('id_name').send_keys('NewQuery')
        self.driver.find_element_by_id('id_query_sequence').send_keys(query_sequence)
        self.driver.find_element_by_xpath('//button[text()="Run Query"]').click()

        # TODO: Need to actually have some sequences in the database for this to work...

    def tearDown(self):
        self.driver.close()
