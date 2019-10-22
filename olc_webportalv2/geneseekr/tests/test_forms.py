from django.test import TestCase
from django.http import QueryDict
from olc_webportalv2.geneseekr.forms import TreeForm, GeneSeekrForm, AMRForm, ProkkaForm, NearNeighborForm
from olc_webportalv2.metadata.models import SequenceData
from django.core.files.uploadedfile import SimpleUploadedFile


class GeneSeekrFormTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0711',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0712',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()

    def test_valid_geneseekr_form_seqid_input_fasta_text(self):
        form = GeneSeekrForm({
            'seqids': '2015-SEQ-0711 2015-SEQ-0712',
            'query_sequence': '>fasta_name\nATCGACTGACTAGTCA'
        })
        self.assertTrue(form.is_valid())
        seqid_list, query_sequence = form.cleaned_data
        self.assertEqual(seqid_list, ['2015-SEQ-0711', '2015-SEQ-0712'])
        self.assertEqual(query_sequence, '>fasta_name\nATCGACTGACTAGTCA')

    def test_valid_geneseekr_form_genus_input_fasta_text(self):
        form = GeneSeekrForm({
            'genus': 'Listeria',
            'query_sequence': '>fasta_name\nATCGACTGACTAGTCA'
        })
        self.assertTrue(form.is_valid())
        seqid_list, query_sequence = form.cleaned_data
        self.assertEqual(seqid_list, ['2015-SEQ-0711', '2015-SEQ-0712'])
        self.assertEqual(query_sequence, '>fasta_name\nATCGACTGACTAGTCA')

    def test_valid_geneseekr_form_seqid_input_fasta_file(self):
        with open('olc_webportalv2/geneseekr/tests/good_fasta.fasta', 'rb') as upload_file:
            form = GeneSeekrForm({'seqids': '2015-SEQ-0711 2015-SEQ-0712'}, {'query_file': SimpleUploadedFile(upload_file.name, upload_file.read())})
            self.assertTrue(form.is_valid())
            seqid_list, query_sequence = form.cleaned_data
            self.assertEqual(seqid_list, ['2015-SEQ-0711', '2015-SEQ-0712'])

    def test_valid_geneseekr_form_genus_input_fasta_file(self):
        with open('olc_webportalv2/geneseekr/tests/good_fasta.fasta', 'rb') as upload_file:
            form = GeneSeekrForm({'genus': 'Listeria'}, {'query_file': SimpleUploadedFile(upload_file.name, upload_file.read())})
            self.assertTrue(form.is_valid())
            seqid_list, query_sequence = form.cleaned_data
            self.assertEqual(seqid_list, ['2015-SEQ-0711', '2015-SEQ-0712'])

    def test_invalid_form_missing_seqid(self):
        form = GeneSeekrForm({
            'seqids': '2222-SEQ-0711 2015-SEQ-0712',
            'query_sequence': '>fasta_name\nATCGACTGACTAGTCA'
        })
        self.assertFalse(form.is_valid())

    def test_invalid_form_bad_fasta_file(self):
        with open('olc_webportalv2/geneseekr/tests/bad_fasta.fasta', 'rb') as upload_file:
            form = GeneSeekrForm({'seqids': '2015-SEQ-0711 2015-SEQ-0712'}, {'query_file': SimpleUploadedFile(upload_file.name, upload_file.read())})
            self.assertFalse(form.is_valid())

    def test_invalid_form_no_sequences_in_genus(self):
        form = GeneSeekrForm({
            'genus': 'TotallyFakeGenus',
            'query_sequence': '>fasta_name\nATCGACTGACTAGTCA'
        })
        self.assertFalse(form.is_valid())

    def test_invalid_form_no_sequences_in_exclude_genus(self):
        form = GeneSeekrForm({
            'genus': 'Listeria',
            'query_sequence': '>fasta_name\nATCGACTGACTAGTCA',
            'everything_but': True,
        })
        self.assertFalse(form.is_valid())

    def test_valid_form_exclude_genus(self):
        form = GeneSeekrForm({
            'genus': 'Salmonella',
            'query_sequence': '>fasta_name\nATCGACTGACTAGTCA',
            'everything_but': True,
        })
        self.assertTrue(form.is_valid())
        seqid_list, query_sequence = form.cleaned_data
        self.assertEqual(seqid_list, ['2015-SEQ-0711', '2015-SEQ-0712'])
        self.assertEqual(query_sequence, '>fasta_name\nATCGACTGACTAGTCA')

    def test_invalid_form_fasta_too_long(self):
        form = GeneSeekrForm({
            'seqids': '2015-SEQ-0711 2015-SEQ-0712',
            'query_sequence': '>fasta_name\nATCGACTGACTAGTCA' + 'A'*10000
        })
        self.assertFalse(form.is_valid())


class TreeFormTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0711',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0712',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()

    def test_valid_form(self):
        form = TreeForm({
            'seqids': '2015-SEQ-0711 2015-SEQ-0712'
        }, QueryDict())
        self.assertTrue(form.is_valid())
        seqids, name, number_diversitree_strains, other_files = form.cleaned_data
        self.assertEqual(seqids, ['2015-SEQ-0711', '2015-SEQ-0712'])

    def test_invalid_form_wrong_seqid_regex(self):
        form = TreeForm({
            'seqids': '22015-SEQ-0711 2015-SEQ-0712',
        }, QueryDict())
        self.assertFalse(form.is_valid())

    def test_invalid_form_wrong_seqid_does_not_exist(self):
        form = TreeForm({
            'seqids': '2222-SEQ-0711 2015-SEQ-0712'
        }, QueryDict())
        self.assertFalse(form.is_valid())

    def test_invalid_form_negative_number_diversitree_strains(self):
        form = TreeForm({
            'seqids': '2015-SEQ-0711 2015-SEQ-0712', 'number_diversitree_strains':'-2'
        }, QueryDict())
        self.assertFalse(form.is_valid())

    def test_invalid_form_large_number_diversitree_strains(self):
        form = TreeForm({
            'seqids': '2015-SEQ-0711 2015-SEQ-0712', 'number_diversitree_strains':'5'
        }, QueryDict())
        self.assertFalse(form.is_valid())

    def test_invalid_form_blank(self):
        form = TreeForm({
            'seqids': ''
        }, QueryDict())
        self.assertFalse(form.is_valid())

class AMRFormTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0711',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0712',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()

    def test_valid_form(self):
        form = AMRForm({
            'seqids': '2015-SEQ-0711 2015-SEQ-0712'
        }, QueryDict())
        self.assertTrue(form.is_valid())
        seqids, name, other_files = form.cleaned_data
        self.assertEqual(seqids, ['2015-SEQ-0711', '2015-SEQ-0712'])

    def test_invalid_form_wrong_seqid_regex(self):
        form = AMRForm({
            'seqids': '22015-SEQ-0711 2015-SEQ-0712'
        }, QueryDict())
        self.assertFalse(form.is_valid())

    def test_invalid_form_wrong_seqid_does_not_exist(self):
        form = AMRForm({
            'seqids': '2222-SEQ-0711 2015-SEQ-0712'
        }, QueryDict())
        self.assertFalse(form.is_valid())

    def test_invalid_form_blank(self):
        form = AMRForm({
            'seqids': ''
        }, QueryDict())
        self.assertFalse(form.is_valid())


class ProkkaFormTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0711',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0712',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()

    def test_valid_form(self):
        form = ProkkaForm(QueryDict('seqids=2015-SEQ-0711 2015-SEQ-0712'), QueryDict())
        self.assertTrue(form.is_valid())
        seqids, name, other_files = form.cleaned_data
        self.assertEqual(seqids, ['2015-SEQ-0711', '2015-SEQ-0712'])

    def test_invalid_form_wrong_seqid_regex(self):
        form = ProkkaForm({
            'seqids': '22015-SEQ-0711 2015-SEQ-0712'
        }, QueryDict())
        self.assertFalse(form.is_valid())

    def test_invalid_form_wrong_seqid_does_not_exist(self):
        form = ProkkaForm({
            'seqids': '2222-SEQ-0711 2015-SEQ-0712'
        }, QueryDict())
        self.assertFalse(form.is_valid())

    def test_invalid_form_blank(self):
        form = ProkkaForm({
            'seqids': ''
        }, QueryDict())
        self.assertFalse(form.is_valid())


class NearNeighborsFormTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0711',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()
        sequence_data = SequenceData.objects.create(seqid='2015-SEQ-0712',
                                                    quality='Pass',
                                                    genus='Listeria')
        sequence_data.save()

    def test_valid_form(self):
        form = NearNeighborForm({'seqid': '2015-SEQ-0711', 'number_neighbors': 10})
        self.assertTrue(form.is_valid())
        seqid, name, number_neighbors, uploaded_file = form.cleaned_data
        self.assertEqual(seqid, '2015-SEQ-0711')
        self.assertEqual(number_neighbors, 10)

    def test_negative_neighbors(self):
        form = NearNeighborForm({'seqid': '2015-SEQ-0711', 'number_neighbors': -10})
        self.assertFalse(form.is_valid())

    def test_bad_seqid(self):
        form = NearNeighborForm({'seqid': '2015-FAKE-0711', 'number_neighbors': 10})
        self.assertFalse(form.is_valid())

    def test_neighbor_boundary_low_valid(self):
        form = NearNeighborForm({'seqid': '2015-SEQ-0711', 'number_neighbors': 1})
        self.assertTrue(form.is_valid())
        seqid, name, number_neighbors, uploaded_file = form.cleaned_data
        self.assertEqual(seqid, '2015-SEQ-0711')
        self.assertEqual(number_neighbors, 1)

    def test_neighbor_boundary_high_valid(self):
        form = NearNeighborForm({'seqid': '2015-SEQ-0711', 'number_neighbors': 250})
        self.assertTrue(form.is_valid())
        seqid, name, number_neighbors, uploaded_file = form.cleaned_data
        self.assertEqual(seqid, '2015-SEQ-0711')
        self.assertEqual(number_neighbors, 250)

    def test_neighbor_boundary_high_invalid(self):
        form = NearNeighborForm({'seqid': '2015-SEQ-0711', 'number_neighbors': 251})
        self.assertFalse(form.is_valid())

    def test_neighbor_boundary_low_invalid(self):
        form = NearNeighborForm({'seqid': '2015-SEQ-0711', 'number_neighbors': 0})
        self.assertFalse(form.is_valid())

    def test_neighbor_uploaded_file_no_seqid(self):
        with open('olc_webportalv2/geneseekr/tests/good_fasta.fasta', 'rb') as upload_file:
            file_data = {'uploaded_file': SimpleUploadedFile(upload_file.name, upload_file.read())}
            form = NearNeighborForm({'seqid': '', 'number_neighbors': 4}, file_data)
            self.assertTrue(form.is_valid())

    def test_neighbor_uploaded_file_bad_extension(self):
        with open('olc_webportalv2/test_files/config.xml', 'rb') as upload_file:
            form = NearNeighborForm({'seqid': '',
                                     'uploaded_file': SimpleUploadedFile(upload_file.name, upload_file.read()),
                                     'number_neighbors': 4})
            self.assertFalse(form.is_valid())

    def test_form_invalid_seqid_and_file(self):
        with open('olc_webportalv2/geneseekr/tests/good_fasta.fasta', 'rb') as upload_file:
            file_data = {'uploaded_file': SimpleUploadedFile(upload_file.name, upload_file.read())}
            form = NearNeighborForm({'seqid': '2015-SEQ-0711', 'number_neighbors': 4}, file_data)
            self.assertFalse(form.is_valid())
