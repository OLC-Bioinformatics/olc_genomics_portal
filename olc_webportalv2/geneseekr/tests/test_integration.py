from django.test import LiveServerTestCase
from django.urls import reverse_lazy
from selenium import webdriver
from selenium.webdriver.firefox.options import Options

from olc_webportalv2.users.models import User

# TODO: Standardize button styling; some links look like buttons while
# others are actual buttons.


class GeneSeekrIntegrationTest(LiveServerTestCase):
    def setUp(self):
        options = Options()
        options.headless = True
        self.driver = webdriver.Firefox(
            options=options, executable_path='/data/web/geckodriver'
        )
        user = User.objects.create(
            username='testuser', password='password', email='test@test.com'
        )
        user.set_password('password')
        user.save()

    def login(self):
        self.driver.get(
            '%s%s'
            % (self.live_server_url, reverse_lazy('geneseekr:geneseekr_home'))
        )
        self.driver.find_element_by_id('id_login').send_keys('test@test.com')
        self.driver.find_element_by_id('id_password').send_keys('password')
        self.driver.find_element_by_xpath('//button[text()="Sign In"]').click()

    def test_create_query(self):
        # Login.
        query_sequence = (
            '>sequence\nACTGATCGTACGTACTGGTCTGATACTGGTCTAGCATGCTGA'
        )
        self.login()
        # This takes us to home page - navigate to geneseekr page
        # Dropdown menu
        self.driver.find_element_by_xpath('//button[text()="Analyze"]').click()
        self.driver.find_element_by_link_text('Find Genes').click()
        # Now move to create query button.
        self.driver.find_element_by_link_text(
            'Create A GeneSeekr Query'
        ).click()
        # Now actually submit a query.
        self.driver.find_element_by_id('id_name').send_keys('NewQuery')
        self.driver.find_element_by_id('id_query_sequence').send_keys(
            query_sequence
        )
        self.driver.find_element_by_xpath(
            '//button[text()="Run Query"]'
        ).click()

        # TODO: Add sample sequences so the browser workflow can complete.

    def tearDown(self):
        self.driver.close()
