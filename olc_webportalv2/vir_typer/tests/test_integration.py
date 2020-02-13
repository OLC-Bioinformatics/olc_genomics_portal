#!/usr/bin/env python3
from selenium import webdriver
from django.test import LiveServerTestCase
from django.urls import reverse_lazy
from olc_webportalv2.users.models import User
from olc_webportalv2.vir_typer.models import VirTyperProject,VirTyperRequest, VirTyperResults
from selenium.webdriver.support.select import Select
__author__ = 'adamkoziol'


class VirusTyper(LiveServerTestCase):
    def setUp(self):
        self.driver = webdriver.Firefox(executable_path='/data/web/geckodriver')
        user = User.objects.create(username='testuser', password='password', email='test@test.com')
        user.set_password('password')
        user.save()

    def login(self):
        self.driver.get('%s%s' % (self.live_server_url, reverse_lazy('virus_typer:virus_typer_home')))
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
    self.driver.find_element_by_link_text('Create a Virus Typer Query').click()
        # Now actually submit a query.
        self.driver.find_element_by_link_text('Add New Sample').click()

        self.driver.find_element_by_id('id_project_name').send_keys('newProject')
        self.driver.find_element_by_id('id_form-0-sample_name').send_keys('newSample')
        self.driver.find_element_by_id('id_form-0-LSTS_ID').send_keys('newLSTS ID')
        self.driver.find_element_by_id('id_form-0-isolate_source').send_keys('newIsolate')
        self.driver.find_element_by_id('id_form-0-analyst_name').send_keys('newAnalyst')
        self.driver.find_element_by_id('id_form-0-date_received').send_keys('2020-01-01')

        self.driver.find_element_by_id('id_form-1-sample_name').send_keys('newSample1')
        self.driver.find_element_by_id('id_form-1-LSTS_ID').send_keys('newLSTS ID1')
        self.driver.find_element_by_id('id_form-1-isolate_source').send_keys('newIsolate1')
        self.driver.find_element_by_id('id_form-1-analyst_name').send_keys('newAnalyst1')
        self.driver.find_element_by_id('id_form-1-date_received').send_keys('2020-01-02')

        self.driver.find_element_by_xpath('//button[text()="Create Samples"]').click()


