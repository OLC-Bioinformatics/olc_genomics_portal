from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from olc_webportalv2.common.benchmarks import (
    benchmark_panel_storage_name,
    normalise_benchmark_name,
)
from olc_webportalv2.primer_finder import tasks
from olc_webportalv2.primer_finder.forms import PrimerVerifierForm
from olc_webportalv2.primer_finder.models import (
    PrimerValidatorAzureRequest,
    PrimerVerifierRequest,
)


class PrimerQuerySelectionTaskTests(SimpleTestCase):
    def test_long_benchmark_uses_existing_panel_column_width(self):
        stored_name = benchmark_panel_storage_name(
            benchmark_name='ncbi-reference-exclusivity'
        )

        self.assertEqual(stored_name, 'ncbi-ref-excl')
        self.assertEqual(len(stored_name), 13)
        self.assertEqual(
            normalise_benchmark_name(benchmark_name=stored_name),
            'ncbi-reference-exclusivity',
        )
        self.assertEqual(
            PrimerVerifierRequest._meta.db_table,
            'primer_finder_verifierrequest',
        )
        self.assertEqual(
            PrimerValidatorAzureRequest._meta.db_table,
            'primer_finder_validatorazurerequest',
        )
        self.assertEqual(
            PrimerVerifierRequest._meta.get_field(
                'inclusivity_panel'
            ).base_field.max_length,
            24,
        )

    def test_form_exposes_query_selection_controls(self):
        fields = PrimerVerifierForm().fields

        for field_name in (
            'inclusivity_genus',
            'exclusivity_genus',
            'inclusivity_seqids',
            'exclusivity_seqids',
            'inclusivity_benchmark',
            'exclusivity_benchmark',
            'min_amplicon_size',
            'max_amplicon_size',
            'contig_breaks',
        ):
            self.assertIn(field_name, fields)

    @mock.patch('olc_webportalv2.primer_finder.tasks.get_storage_client')
    def test_benchmark_copy_uses_highest_version(self, get_storage_client):
        client = get_storage_client.return_value
        client.list_objects.return_value = [
            SimpleNamespace(name='listeria.zip'),
            SimpleNamespace(name='listeria-1.2.zip'),
            SimpleNamespace(name='listeria-1.10.zip'),
        ]
        client.get_object.return_value = SimpleNamespace(size=10)
        client.object_exists.return_value = False

        blob_name = tasks.copy_benchmark_files(
            container_name='primer-request-1',
            genus='listeria',
            panel='inclusivity',
        )

        self.assertEqual(blob_name, 'inclusivity/listeria-1.10.zip')
        client.copy_object.assert_called_once_with(
            'benchmark-datasets',
            'listeria-1.10.zip',
            'primer-request-1',
            'inclusivity/listeria-1.10.zip',
        )

    @mock.patch('olc_webportalv2.primer_finder.tasks.get_storage_client')
    def test_selected_seqids_are_copied_once(self, get_storage_client):
        client = get_storage_client.return_value
        client.object_exists.side_effect = [True, False]
        client.get_object.return_value = SimpleNamespace(size=42)

        blob_names = tasks._copy_sequence_files(
            container_name='primer-request-1',
            panel='exclusivity',
            seqids=['2015-SEQ-0711', '2015-SEQ-0711'],
        )

        self.assertEqual(blob_names, ['exclusivity/2015-SEQ-0711.fasta'])
        client.copy_object.assert_called_once_with(
            'processed-data',
            '2015-SEQ-0711.fasta',
            'primer-request-1',
            'exclusivity/2015-SEQ-0711.fasta',
        )

    @mock.patch('olc_webportalv2.primer_finder.tasks.get_storage_client')
    def test_missing_selected_seqid_fails_explicitly(self, get_storage_client):
        get_storage_client.return_value.object_exists.return_value = False

        with self.assertRaisesRegex(FileNotFoundError, '2015-SEQ-0711'):
            tasks._copy_sequence_files(
                container_name='primer-request-1',
                panel='inclusivity',
                seqids=['2015-SEQ-0711'],
            )

    @mock.patch(
        'olc_webportalv2.primer_finder.tasks._copy_sequence_files',
        return_value=['inclusivity/2015-SEQ-0711.fasta'],
    )
    @mock.patch(
        'olc_webportalv2.primer_finder.tasks.copy_benchmark_files',
        return_value='inclusivity/listeria-1.10.zip',
    )
    def test_panel_selection_records_latest_benchmark_version(
        self, copy_benchmark_files, copy_sequence_files
    ):
        request = SimpleNamespace(
            inclusivity_panel=['listeria'],
            inclusivity_genus='',
            inclusivity_seqids=['2015-SEQ-0711'],
            benchmark_versions={},
            save=mock.Mock(),
        )

        blob_names = tasks._handle_panel_files(
            container_name='primer-request-1',
            inclusivity=True,
            request=request,
        )

        self.assertEqual(
            blob_names,
            [
                'inclusivity/listeria-1.10.zip',
                'inclusivity/2015-SEQ-0711.fasta',
            ],
        )
        self.assertEqual(
            request.benchmark_versions,
            {'listeria': '1.10'},
        )
        request.save.assert_called_once_with(
            update_fields=['benchmark_versions']
        )
        copy_benchmark_files.assert_called_once()
        copy_sequence_files.assert_called_once_with(
            container_name='primer-request-1',
            panel='inclusivity',
            seqids=['2015-SEQ-0711'],
        )

    @mock.patch(
        'olc_webportalv2.primer_finder.tasks.copy_benchmark_files',
        return_value='exclusivity/ncbi-reference-exclusivity-1.0.zip',
    )
    def test_long_benchmark_alias_is_expanded_for_storage(self, copy_files):
        request = SimpleNamespace(
            exclusivity_panel=['ncbi-ref-excl'],
            benchmark_versions={},
            save=mock.Mock(),
        )

        tasks._handle_panel_files(
            container_name='primer-request-1',
            inclusivity=False,
            request=request,
        )

        copy_files.assert_called_once_with(
            container_name='primer-request-1',
            genus='ncbi-reference-exclusivity',
            panel='exclusivity',
        )
