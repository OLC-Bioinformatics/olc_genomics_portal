"""Tests for Sequence Database search views."""

from datetime import date

from django.test import TestCase
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
