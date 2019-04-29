from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from django.test import LiveServerTestCase
from django.urls import reverse_lazy
from olc_webportalv2.users.models import User
from olc_webportalv2.metadata.models import SequenceData, Genus

# TODO: This has made me realize that sometimes I have buttons as links that look like buttons, and
 # sometimes I have them as actual buttons. Should standardize to one or the other.

"""
class MetadataIntegrationTest(LiveServerTestCase):
    def setUp(self):
        self.driver = webdriver.Firefox(executable_path='/data/web/geckodriver')
        user = User.objects.create(username='testuser', password='password', email='test@test.com')
        user.set_password('password')
        user.save()
        Genus.objects.create(genus='Escherichia')
        SequenceData.objects.create(seqid='1234-SEQ-0001', genus='Escherichia', quality='Pass')
        SequenceData.objects.create(seqid='1234-SEQ-0002', genus='Escherichia', quality='Reference')
        SequenceData.objects.create(seqid='1234-SEQ-0003', genus='Escherichia', quality='Fail')
        SequenceData.objects.create(seqid='1234-SEQ-0004', genus='Listeria', quality='Pass')
        SequenceData.objects.create(seqid='1234-SEQ-0005', genus='Listeria', quality='Reference')
        SequenceData.objects.create(seqid='1234-SEQ-0006', genus='Listeria', quality='Fail')

    def login(self):
        # Any page redirects us to login page
        self.driver.get('%s%s' % (self.live_server_url, reverse_lazy('geneseekr:geneseekr_home')))
        self.driver.find_element_by_id('id_login').send_keys('test@test.com')
        self.driver.find_element_by_id('id_password').send_keys('password')
        self.driver.find_element_by_xpath('//button[text()="Sign In"]').click()

    def test_create_query(self):
        # Login.
        self.login()
        # This takes us to home page - navigate to metadata page
        self.driver.find_element_by_link_text('Explore').click()
        # Autocomplete forms make life REALLY DIFFICULT.
        # Seems to be necessary to: select the little arrow on field of interest by xpath (likely to be very brittle),
        # then create an giant action chain to actually get something into it.
        little_clicky_arrow = self.driver.find_element_by_xpath('/html/body/div[2]/form/p[1]/span/span[1]/span/span[2]')
        ActionChains(self.driver).move_to_element(little_clicky_arrow).click().send_keys('Escherichia').send_keys(Keys.ENTER).send_keys(Keys.ENTER).perform()

        # Get Escherichia sequences - default is to do pass and reference quality.
        # self.driver.find_element_by_id('id_genus').send_keys('Escherichia')
        # self.driver.find_element_by_id('id_genus').send_keys(Keys.ENTER)
        self.driver.find_element_by_xpath('//button[text()="Get SeqIDs"]').click()

        # Make sure we got two results (3 including table header)
        table = self.driver.find_element_by_id('seqid-table')
        table_rows = table.find_elements_by_tag_name('tr')
        self.assertEqual(3, len(table_rows))
        self.assertEqual(table_rows[1].text, '1234-SEQ-0001 N/A')
        self.assertEqual(table_rows[2].text, '1234-SEQ-0002 N/A')

    def test_query_low_quality_sequences(self):
        # Login.
        self.login()
        # This takes us to home page - navigate to metadata page
        self.driver.find_element_by_link_text('Explore').click()
        # Get Escherichia sequences - default is to do pass and reference quality.
        self.driver.find_element_by_id('id_genus').send_keys('Escherichia')
        self.driver.find_element_by_id('id_genus').send_keys(Keys.ENTER)
        # Select low quality sequences option via xpath. option[1] gets Pass, 2 gets reference, 3 gets fail
        self.driver.find_element_by_xpath('//*[@id="id_quality"]/option[3]').click()
        # Make sure we got three results (4 including table header)
        self.driver.find_element_by_xpath('//button[text()="Get SeqIDs"]').click()
        table = self.driver.find_element_by_id('seqid-table')
        table_rows = table.find_elements_by_tag_name('tr')
        self.assertEqual(4, len(table_rows))
        self.assertEqual(table_rows[1].text, '1234-SEQ-0001 N/A')
        self.assertEqual(table_rows[2].text, '1234-SEQ-0002 N/A')
        self.assertEqual(table_rows[3].text, '1234-SEQ-0003 N/A')

    def test_query_ref_quality_sequences(self):
        # Login.
        self.login()
        # This takes us to home page - navigate to metadata page
        self.driver.find_element_by_link_text('Explore').click()
        # Get Escherichia sequences - default is to do pass and reference quality.
        self.driver.find_element_by_id('id_genus').send_keys('Escherichia')
        self.driver.find_element_by_id('id_genus').send_keys(Keys.ENTER)
        # Select ref quality sequences option via xpath. option[1] gets Pass, 2 gets reference, 3 gets fail
        self.driver.find_element_by_xpath('//*[@id="id_quality"]/option[2]').click()
        self.driver.find_element_by_xpath('//button[text()="Get SeqIDs"]').click()
        # Make sure we got one result (2 including table header)
        table = self.driver.find_element_by_id('seqid-table')
        table_rows = table.find_elements_by_tag_name('tr')
        self.assertEqual(2, len(table_rows))
        self.assertEqual(table_rows[1].text, '1234-SEQ-0002 N/A')

    def tearDown(self):
        self.driver.close()
"""
