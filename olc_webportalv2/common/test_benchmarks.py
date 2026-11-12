from django.test import SimpleTestCase

from olc_webportalv2.common.benchmarks import (
    EXCLUSIVITY_BENCHMARK_CHOICES,
    INCLUSIVITY_BENCHMARK_CHOICES,
    validate_benchmark_panel,
)


class BDSSTECBenchmarkTests(SimpleTestCase):
    def test_bds_stec_is_available_for_both_panels(self):
        choice = ('bds-stec', 'BDS-STEC')
        self.assertIn(choice, INCLUSIVITY_BENCHMARK_CHOICES)
        self.assertIn(choice, EXCLUSIVITY_BENCHMARK_CHOICES)
        self.assertEqual(
            validate_benchmark_panel(
                benchmark_name='bds-stec',
                panel='inclusivity',
            ),
            'bds-stec',
        )
        self.assertEqual(
            validate_benchmark_panel(
                benchmark_name='bds-stec',
                panel='exclusivity',
            ),
            'bds-stec',
        )
