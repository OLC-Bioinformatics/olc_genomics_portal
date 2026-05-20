#!/usr/bin/env python3
# from selenium import webdriver
# from django.test import LiveServerTestCase
# from django.urls import reverse_lazy
# from olc_webportalv2.users.models import User
# __author__ = 'adamkoziol'
#
#
# class VirusTyper(LiveServerTestCase):
#     def setUp(self):
#         self.driver = webdriver.Firefox(executable_path='/data/web/geckodriver')
#         user = User.objects.create(username='testuser', password='password', email='test@test.com')
#         user.set_password('password')
#         user.save()
#
#     def login(self):
#         self.driver.get('%s%s' % (self.live_server_url, reverse_lazy('virus_typer:virus_typer_home')))
#         self.driver.find_element_by_id('id_login').send_keys('test@test.com')
#         self.driver.find_element_by_id('id_password').send_keys('password')
#         self.driver.find_element_by_xpath('//button[text()="Sign In"]').click()
#
#     def test_create_query(self):
#         # Login.
#         self.login()
#         # This takes us to home page - navigate to geneseekr page
#         # Dropdown menu
#         self.driver.find_element_by_xpath('//button[text()="Analyze"]').click()
#         self.driver.find_element_by_link_text('Virus Typer').click()
#         # Now move to create query button.
#         self.driver.find_element_by_link_text('Create A Virus Typer Query').click()
#         # Now actually submit a query.
#         # self.driver.find_element_by_id('id_name').send_keys('NewQuery')
#         # self.driver.find_element_by_id('id_query_sequence').send_keys(query_sequence)
#
#     def tearDown(self):
#         self.driver.close()
