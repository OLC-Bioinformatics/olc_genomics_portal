"""HTTP smoke tests for static assets served by Nginx."""

import os
import unittest
from urllib.request import urlopen

from django.test import SimpleTestCase

STATIC_ASSET_BASE_URL = os.environ.get("FOODPORT_STATIC_ASSET_BASE_URL")


@unittest.skipUnless(
    STATIC_ASSET_BASE_URL,
    "Set FOODPORT_STATIC_ASSET_BASE_URL to test Nginx static serving.",
)
class NginxStaticAssetHttpTests(SimpleTestCase):
    """Verify static assets are available through the configured Nginx."""

    def test_base_assets_are_served_with_expected_content_types(self):
        """Check status, content type, and response body for base assets."""
        assets = (
            ("css/bootstrap.min.css", "text/css"),
            ("css/bootstrap-datepicker.min.css", "text/css"),
            ("css/project.css", "text/css"),
            ("js/jquery-3.7.1.min.js", "application/javascript"),
            ("js/bootstrap.min.js", "application/javascript"),
            ("js/project.js", "application/javascript"),
        )

        for asset_path, expected_content_type in assets:
            with self.subTest(asset=asset_path):
                url = (
                    f"{STATIC_ASSET_BASE_URL.rstrip('/')}"
                    f"/static/{asset_path}"
                )
                with urlopen(url, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(
                        response.headers.get_content_type(),
                        expected_content_type,
                    )
                    self.assertTrue(response.read())
