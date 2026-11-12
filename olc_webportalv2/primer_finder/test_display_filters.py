from django.test import SimpleTestCase

from olc_webportalv2.primer_finder.templatetags.display_filters import (
    display_name,
)


class DisplayNameTests(SimpleTestCase):
    def test_bds_stec_keeps_stec_uppercase(self):
        self.assertEqual(display_name('bds-stec'), 'BDS-STEC')
