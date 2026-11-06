from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from olc_webportalv2.geneseekr.methods import zip_files


class GeneSeekrMethodTests(TestCase):
    @mock.patch('olc_webportalv2.geneseekr.methods.shutil.rmtree')
    @mock.patch('olc_webportalv2.geneseekr.methods.os.remove')
    @mock.patch('olc_webportalv2.geneseekr.methods.shutil.make_archive')
    @mock.patch('olc_webportalv2.geneseekr.methods.get_storage_client')
    def test_zip_files_downloads_matching_blobs_and_cleans_up(
        self, blob_service_class, make_archive, remove, rmtree
    ):
        blob_client = blob_service_class.return_value
        blob_client.list_objects.return_value = [
            SimpleNamespace(name='2015-SEQ-0711_R1.fasta'),
            SimpleNamespace(name='unrelated.fasta'),
        ]

        result = zip_files(
            seqids=['2015-SEQ-0711'],
            target_folder='gene-seekr-1',
            container_name='processed-data',
        )

        self.assertEqual(result, 'gene-seekr-1.zip')
        self.assertEqual(blob_client.download_file.call_count, 1)
        self.assertEqual(blob_client.ensure_container.call_count, 1)
        self.assertEqual(blob_client.upload_file.call_count, 1)
        self.assertEqual(remove.call_count, 1)
        self.assertEqual(
            remove.call_args[0][0],
            'olc_webportalv2/media/gene-seekr-1/tmp.zip'
        )
        self.assertEqual(rmtree.call_count, 1)
        self.assertEqual(
            rmtree.call_args[0][0],
            'olc_webportalv2/media/gene-seekr-1/tmp'
        )