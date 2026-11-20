"""Tests for Sequence Database search views."""

from datetime import date

from django.test import TestCase
from django.utils.translation import override
from django.urls import reverse

from olc_webportalv2.sequence_database.models import (
    NameTable,
    SequenceData,
    UniqueGenus,
)
from olc_webportalv2.users.models import User


class SequenceDatabaseSearchTests(TestCase):
    """Cover Sequence Database filters and autocomplete behavior."""

    def setUp(self):
        """Create an authenticated user and dated sequence records."""
        self.client.force_login(User.objects.create(username='TestUser'))
        self._create_sequence('1234-SEQ-0001', 'CFIA001', date(2024, 1, 1))
        self._create_sequence('1234-SEQ-0002', 'CFIA002', date(2024, 1, 2))

    def _create_sequence(self, seqid, cfiaid, typing_date):
        """Create a sequence with the given identifiers and typing date.

        Args:
            seqid: The sequence identifier.
            cfiaid: The CFIA identifier.
            typing_date: The sequence's metadata typing date.
        """
        name = NameTable.objects.create(
            seqid=seqid,
            cfiaid=cfiaid,
            curatorflag='',
        )
        return SequenceData.objects.create(
            seqid=seqid,
            cfiaid=name,
            typing_date=typing_date,
        )

    def test_empty_id_search_returns_no_sequences(self):
        """An empty ID submission must not return every database record."""
        response = self.client.post(
            reverse('sequence_database:id_search'),
            {'seqids': '', 'cfiaids': ''},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['seqids'], [])
        self.assertEqual(list(response.context['query_table']), [])

    def test_database_query_toggle_labels_are_translated(self):
        """Render the dynamic ID-list toggle labels in French."""
        self.user = User.objects.get(username='TestUser')
        self.user.language = 'fr'
        self.user.save(update_fields=['language'])
        self.client.force_login(self.user)

        with override('fr'):
            response = self.client.post(
                reverse('sequence_database:database_query').replace(
                    '/en-ca/', '/fr/'
                ),
                data={
                    'form-TOTAL_FORMS': '0',
                    'form-INITIAL_FORMS': '0',
                    'form-MIN_NUM_FORMS': '0',
                    'form-MAX_NUM_FORMS': '1000',
                },
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Afficher la liste des SeqID"')
        self.assertContains(response, 'data-hide-label="Masquer la liste des SeqID"')

    def test_database_filter_headers_are_translated(self):
        """Translate the labels for columns in filtered sequence results."""
        self.user = User.objects.get(username='TestUser')
        self.user.language = 'fr'
        self.user.save(update_fields=['language'])
        self.client.force_login(self.user)

        with override('fr'):
            response = self.client.post(
                reverse('sequence_database:database_filter').replace(
                    '/en-ca/', '/fr/'
                ),
                data={},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Profil MLST')
        self.assertContains(response, 'Date de typage')

    def test_missing_oln_id_error_is_translated(self):
        """A missing OLC network ID returns a localized API error."""
        user = User.objects.get(username='TestUser')
        user.language = 'fr'
        user.save(update_fields=['language'])
        self.client.force_login(user)

        with override('fr'):
            response = self.client.get(
                reverse(
                    'sequence_database:olndata_detail',
                    kwargs={'oln_id': 'missing'},
                )
            )

        self.assertEqual(
            response.json(),
            {'ERROR': 'L’identifiant de réseau OLC demandé est introuvable.'},
        )

    def test_id_search_rejects_identifiers_with_invalid_characters(self):
        """Do not turn malformed identifiers into valid partial matches."""
        for field, identifier in (
            ('seqids', '1234-SEQ-0001!'),
            ('cfiaids', 'CFIA001!'),
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    reverse('sequence_database:id_search'),
                    {'seqids': '', 'cfiaids': '', field: identifier},
                )

                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context['form'].is_valid())
                self.assertTrue(
                    response.context['form'].non_field_errors()
                )

    def test_id_search_accepts_comma_and_newline_separated_ids(self):
        """Search valid sequence and CFIA IDs across common delimiters."""
        response = self.client.post(
            reverse('sequence_database:id_search'),
            {
                'seqids': '1234-SEQ-0001,\n1234-SEQ-0002',
                'cfiaids': 'CFIA001',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(
            response.context['seqids'],
            ['1234-SEQ-0001', '1234-SEQ-0002'],
        )

    def test_database_filter_applies_start_date_without_end_date(self):
        """A start-only date filter excludes earlier sequence records."""
        response = self.client.post(
            reverse('sequence_database:database_filter'),
            {'start_date': '2024-01-02'},
        )

        self.assertEqual(response.status_code, 200)
        results = response.context['query_set'].object_list
        self.assertEqual(
            [sequence.seqid for sequence in results],
            ['1234-SEQ-0002'],
        )

    def test_database_filter_applies_end_date_without_start_date(self):
        """An end-only date filter excludes later sequence records."""
        response = self.client.post(
            reverse('sequence_database:database_filter'),
            {'end_date': '2024-01-01'},
        )

        self.assertEqual(response.status_code, 200)
        results = response.context['query_set'].object_list
        self.assertEqual(
            [sequence.seqid for sequence in results],
            ['1234-SEQ-0001'],
        )

    def test_genus_autocomplete_filters_the_search_term(self):
        """Autocomplete results must honor the supplied search term."""
        UniqueGenus.objects.create(genus='Escherichia')
        UniqueGenus.objects.create(genus='Listeria')

        response = self.client.get(
            reverse('sequence_database:genus_autocompleter'),
            {'q': 'Escher'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escherichia')
        self.assertNotContains(response, 'Listeria')
