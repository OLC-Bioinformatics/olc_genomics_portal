# import os
# import time
# import selenium
# from selenium import webdriver
# from django.test import LiveServerTestCase
# from django.urls import reverse_lazy
# from olc_webportalv2.users.models import User


# class CowbatIntegrationTest(LiveServerTestCase):
#     def setUp(self):
#         self.driver = webdriver.Firefox(
#             executable_path='/data/web/geckodriver'
#         )
#         user = User.objects.create(
#             username='testuser',
#             password='password',
#             email='test@test.com',
#         )
#         user.set_password('password')
#         user.save()

#     def login(self):
#         self.driver.get(
#             '%s%s' % (
#                 self.live_server_url,
#                 reverse_lazy('geneseekr:geneseekr_home'),
#             )
#         )
#         self.driver.find_element_by_id('id_login').send_keys('test@test.com')
#         self.driver.find_element_by_id('id_password').send_keys('password')
#         self.driver.find_element_by_xpath(
#             '//button[text()="Sign In"]'
#         ).click()

#     def test_cowbat_run(self):
#         # Login.
#         self.login()
#         # This takes us to home page - navigate to cowbat page
#         self.driver.find_element_by_link_text('Assembly').click()
#         # Start uploading a new run
#         self.driver.find_element_by_link_text('Upload a New Run').click()
#         # This takes us to the upload sequence metadata page. Upload files,
#         # enter a run name, validate, and then upload.
#         test_file_dir = '/data/web/olc_webportalv2/test_files'
#         self.driver.find_element_by_id(
#             'id_run_name'
#         ).send_keys('123456_TEST')
#         # Dropzone may not load; wait up to a minute for the input.
#         start_time = time.time()
#         max_time = 60
#         element_found = False
#         while time.time() - start_time < max_time and element_found is False:
#             try:
#                 self.driver.find_element_by_css_selector('.dz-hidden-input')
#                 element_found = True
#             except selenium.common.exceptions.NoSuchElementException:
#                 time.sleep(1)
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(os.path.join(test_file_dir, 'SampleSheet.csv'))
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(os.path.join(test_file_dir, 'config.xml'))
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(os.path.join(test_file_dir, 'CompletedJobInfo.xml'))
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(
#             os.path.join(test_file_dir, 'GenerateFASTQRunStatistics.xml')
#         )
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(os.path.join(test_file_dir, 'RunInfo.xml'))
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(os.path.join(test_file_dir, 'runParameters.xml'))
#         # Validate!
#         self.driver.find_element_by_xpath(
#             '//button[text()="Validate Metadata Files"]'
#         ).click()
#         self.driver.find_element_by_xpath(
#             '//button[text()="Upload Metadata Files"]'
#         ).click()
#         # Now we should be on the upload interop page.
#         time.sleep(10)  # Give time for upload to finish.
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(
#             os.path.join(test_file_dir, 'InterOp/ControlMetricsOut.bin')
#         )
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(
#             os.path.join(test_file_dir, 'InterOp/CorrectedIntMetricsOut.bin')
#         )
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(
#             os.path.join(test_file_dir, 'InterOp/ErrorMetricsOut.bin')
#         )
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(
#             os.path.join(test_file_dir, 'InterOp/ExtractionMetricsOut.bin')
#         )
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(
#             os.path.join(test_file_dir, 'InterOp/IndexMetricsOut.bin')
#         )
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(os.path.join(test_file_dir, 'InterOp/QMetricsOut.bin'))
#         self.driver.find_element_by_css_selector(
#             '.dz-hidden-input'
#         ).send_keys(
#             os.path.join(test_file_dir, 'InterOp/TileMetricsOut.bin')
#         )
#         self.driver.find_element_by_xpath(
#             '//button[text()="Validate InterOp Files"]'
#         ).click()
#         self.driver.find_element_by_xpath(
#             '//button[text()="Upload InterOp Files"]'
#         ).click()
#         time.sleep(10)  # Give time for upload to finish.
#         # Now should be on sequence data upload page.
#         self.assertTrue('upload_sequence_data' in self.driver.current_url)

#     def tearDown(self):
#         self.driver.close()

import hashlib
import os
from io import BytesIO
from unittest import mock

from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings
from openpyxl import Workbook

from olc_webportalv2.cowbat.integration import (
    assembly_fasta_metrics,
    canonical_csv_sha256,
    clean_container_outputs,
    combined_metadata_sha256,
    compare_numeric_rows,
    integration_archive_container,
    load_scenario,
    normalize_summary_rows,
    workbook_values_sha256,
)
from olc_webportalv2.cowbat.management.commands.run_cowbat_integration import (
    Command,
)


class CowbatIntegrationHelperTests(SimpleTestCase):
    def test_scenario_has_stable_inputs_and_output_references(self):
        path, scenario = load_scenario("000000-aaa")

        self.assertTrue(path.is_file())
        self.assertEqual(scenario["input"]["container_name"], "000000-aaa")
        self.assertEqual(len(scenario["input"]["fastq_files"]), 2)
        self.assertEqual(len(scenario["expected"]["output_sha256"]), 22)
        self.assertEqual(
            scenario["expected"]["assembly_metrics"],
            {"contig_count": 3515, "total_length": 1346327, "n50": 401},
        )

    def test_scenario_name_cannot_escape_reference_directory(self):
        with self.assertRaises(ValueError):
            load_scenario("../000000-aaa")

    def test_container_cleanup_preserves_only_fastq_gz_objects(self):
        storage = mock.Mock()
        initial = []
        for name in (
            "reads/sample.fastq.gz",
            "reports/result.csv",
            "reports/",
            "old-output.txt",
        ):
            blob = mock.Mock()
            blob.name = name
            initial.append(blob)
        fastq = mock.Mock()
        fastq.name = "reads/sample.fastq.gz"
        storage.list_objects.side_effect = [initial, [fastq]]

        remaining = clean_container_outputs(storage, "000000-aaa")

        self.assertEqual(remaining, {"reads/sample.fastq.gz"})
        self.assertEqual(
            {
                call.args[1]
                for call in storage.delete_object.call_args_list
            },
            {"reports/result.csv", "reports/", "old-output.txt"},
        )

    def test_container_cleanup_accepts_no_existing_objects(self):
        storage = mock.Mock()
        storage.list_objects.return_value = []

        self.assertEqual(clean_container_outputs(storage, "000000-aaa"), set())
        storage.delete_object.assert_not_called()

    def test_combined_metadata_hash_ignores_only_assembly_date(self):
        headers = "SeqID,AssemblyDate,Genus\n"
        first = (headers + "SAMPLE-1,2026-11-16,Salmonella\n").encode()
        second = (headers + "SAMPLE-1,2026-11-19,Salmonella\n").encode()
        changed_result = (
            headers + "SAMPLE-1,2026-11-19,Escherichia\n"
        ).encode()

        self.assertEqual(
            combined_metadata_sha256(first),
            combined_metadata_sha256(second),
        )
        self.assertNotEqual(
            combined_metadata_sha256(first),
            combined_metadata_sha256(changed_result),
        )

    def test_combined_metadata_hash_rejects_missing_sample_rows(self):
        with self.assertRaisesRegex(ValueError, "no data rows"):
            combined_metadata_sha256(b"SeqID,AssemblyDate,Genus\n")

    def test_canonical_csv_hash_ignores_configured_fields_and_row_order(self):
        first = (
            "SeqID,RunDate,Taxon\n"
            "SAMPLE-1,2026-11-16,Salmonella\n"
            "SAMPLE-2,2026-11-16,E. coli\n"
        ).encode()
        reordered = (
            "SeqID,RunDate,Taxon\n"
            "SAMPLE-2,2026-11-19,E. coli\n"
            "SAMPLE-1,2026-11-19,Salmonella\n"
        ).encode()
        changed = (
            "SeqID,RunDate,Taxon\n"
            "SAMPLE-2,2026-11-19,E. coli\n"
            "SAMPLE-1,2026-11-19,Shigella\n"
        ).encode()

        hash_options = {"ignored_fields": ["RunDate"], "sort_columns": ["SeqID"]}
        self.assertEqual(
            canonical_csv_sha256(first, **hash_options),
            canonical_csv_sha256(reordered, **hash_options),
        )
        self.assertNotEqual(
            canonical_csv_sha256(first, **hash_options),
            canonical_csv_sha256(changed, **hash_options),
        )

    def test_numeric_rows_accept_boundary_and_reject_out_of_tolerance(self):
        expected = [{"SeqID": "SAMPLE-1", "NumContigs": "659"}]
        within_tolerance = [{"SeqID": "SAMPLE-1", "NumContigs": "660"}]
        outside_tolerance = [{"SeqID": "SAMPLE-1", "NumContigs": "661"}]
        tolerances = {"NumContigs": 1}

        self.assertEqual(
            compare_numeric_rows(
                within_tolerance,
                expected,
                key_field="SeqID",
                tolerances=tolerances,
            ),
            [],
        )
        self.assertTrue(
            compare_numeric_rows(
                outside_tolerance,
                expected,
                key_field="SeqID",
                tolerances=tolerances,
            )
        )
        self.assertIn(
            "Missing actual row",
            "; ".join(
                compare_numeric_rows(
                    [],
                    expected,
                    key_field="SeqID",
                    tolerances=tolerances,
                )
            ),
        )

    def test_assembly_fasta_metrics_handle_wrapped_sequences(self):
        metrics = assembly_fasta_metrics(
            b">contig-1\nAAAA\nAA\n>contig-2\nCCCC\n>contig-3\nGGG\n"
        )

        self.assertEqual(
            metrics,
            {"contig_count": 3, "total_length": 13, "n50": 4},
        )

    def test_workbook_hash_tracks_cell_values(self):
        def workbook_bytes(value):
            workbook = Workbook()
            workbook.active.title = "Results"
            workbook.active.append(["Gene", "Identity"])
            workbook.active.append(["gene-a", value])
            output = BytesIO()
            workbook.save(output)
            workbook.close()
            return output.getvalue()

        self.assertEqual(
            workbook_values_sha256(workbook_bytes(99.5)),
            workbook_values_sha256(workbook_bytes(99.5)),
        )
        self.assertNotEqual(
            workbook_values_sha256(workbook_bytes(99.5)),
            workbook_values_sha256(workbook_bytes(98.5)),
        )

    def test_summary_normalization_ignores_configured_volatile_fields(self):
        rows = [
            {"SeqID": "SAMPLE-2", "AssemblyDate": "today", "Genus": "E. coli"},
            {
                "SeqID": "SAMPLE-1",
                "AssemblyDate": "today",
                "Genus": "Salmonella",
            },
        ]

        normalized = normalize_summary_rows(rows, ["AssemblyDate"])

        self.assertEqual(
            normalized,
            [
                {"SeqID": "SAMPLE-1", "Genus": "Salmonella"},
                {"SeqID": "SAMPLE-2", "Genus": "E. coli"},
            ],
        )

    def test_integration_archive_container_is_scoped_to_run(self):
        self.assertEqual(
            integration_archive_container(42),
            "cowbat-integration-42",
        )

    def test_reference_hashes_are_sha256_values(self):
        _, scenario = load_scenario("000000-aaa")

        for expected_hash in scenario["expected"]["output_sha256"].values():
            self.assertEqual(
                len(expected_hash),
                hashlib.sha256().digest_size * 2,
            )
            self.assertEqual(expected_hash, expected_hash.lower())

    @override_settings(ENVIRONMENT="PROD")
    @mock.patch.dict(os.environ, {"COWBAT_INTEGRATION_ENABLED": "true"})
    def test_batch_submission_in_prod_requires_explicit_confirmation(self):
        with self.assertRaisesRegex(CommandError, "--allow-production"):
            Command._require_batch_opt_in(allow_production=False)

        Command._require_batch_opt_in(allow_production=True)
