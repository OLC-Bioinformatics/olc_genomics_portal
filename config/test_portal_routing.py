"""Regression tests for portal routing and form template configuration."""

import unittest

from django.test import Client
from django.template.loader import get_template
from django.urls import resolve


class PortalRoutingTests(unittest.TestCase):
    def test_admin_url_resolves(self):
        match = resolve("/admin/")

        self.assertEqual(match.namespace, "admin")
        self.assertEqual(match.url_name, "index")

    def test_admin_without_trailing_slash_redirects(self):
        response = Client().get("/admin", HTTP_HOST="localhost")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/admin/")

    def test_crispy_bootstrap5_template_is_available(self):
        self.assertIsNotNone(get_template("bootstrap5/uni_form.html"))

    def test_research_assembly_template_compiles(self):
        self.assertIsNotNone(
            get_template("cowbat/research_assembly_home.html")
        )

    def test_home_uses_bootstrap_5_data_attributes(self):
        response = Client().get("/", HTTP_HOST="localhost")
        html = response.content.decode(response.charset)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-bs-toggle="collapse"', html)
        self.assertIn('data-bs-toggle="dropdown"', html)
        self.assertNotIn('data-toggle="', html)
