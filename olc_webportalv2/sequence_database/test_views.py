import datetime

from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.sequence_database.models import (
    Genus,
    LookupTable,
    MLST,
    NameTable,
    RMLST,
    SequenceData,
    Serovar,
    Species,
    UniqueGenus,
    UniqueMLST,
    UniqueMLSTCC,
    UniqueRMLST,
    UniqueSerovar,
    UniqueSpecies,
)
from olc_webportalv2.sequence_database.views import (
    category_chooser,
    generic_autocompleter,
)
from olc_webportalv2.users.models import User


class SequenceDatabaseViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sequence-db-view-test")
        self.client.force_login(self.user)
        self.sequence_id = "2024-ABC-0001"
        self.cfia_id = "CFIA-0001"
        self.name = NameTable.objects.create(
            seqid=self.sequence_id,
            cfiaid=self.cfia_id,
            curatorflag="",
        )
        self.genus = Genus.objects.create(
            seqid=self.sequence_id, genus="Salmonella"
        )
        self.species = Species.objects.create(
            seqid=self.sequence_id, species="Salmonella enterica"
        )
        self.mlst = MLST.objects.create(seqid=self.sequence_id, mlst="12")
        self.rmlst = RMLST.objects.create(seqid=self.sequence_id, rmlst="34")
        self.serovar = Serovar.objects.create(
            seqid=self.sequence_id, serovar="Enteritidis"
        )
        SequenceData.objects.create(
            seqid=self.sequence_id,
            cfiaid=self.name,
            genus=self.genus,
            species=self.species,
            mlst=self.mlst,
            rmlst=self.rmlst,
            serovar=self.serovar,
            version="v1",
            typing_date=datetime.date(2024, 1, 1),
        )
        UniqueGenus.objects.create(genus="Salmonella")
        UniqueGenus.objects.create(genus="Escherichia")
        UniqueSpecies.objects.create(species="ND")
        UniqueSpecies.objects.create(species="Salmonella enterica")
        UniqueMLST.objects.create(mlst="12")
        UniqueMLST.objects.create(mlst="2")
        UniqueMLST.objects.create(mlst="ND")
        UniqueMLSTCC.objects.create(mlst_cc="Enteritidis")
        UniqueRMLST.objects.create(rmlst="34")
        UniqueRMLST.objects.create(rmlst="ND")
        UniqueSerovar.objects.create(serovar="Enteritidis")
        LookupTable.objects.create(
            cfiaid=self.name,
            seqid=self.sequence_id,
            olnid="",
            labid="",
            biosample="",
            other_names=["2023-OLD-0001"],
        )

    def test_generic_autocompleter_applies_multiple_forwarded_filters(self):
        matching = generic_autocompleter(
            {"genus": "Salmonella", "species": "Salmonella enterica"}
        )

        self.assertEqual(list(matching.values_list("seqid", flat=True)), [
            self.sequence_id
        ])
        self.assertEqual(
            category_chooser("genus"),
            [
                "species",
                "mlst",
                "rmlst",
                "mlstcc",
                "serovar",
                "geneseekr",
                "vtyper",
            ],
        )

    def test_autocomplete_endpoints_return_sorted_unique_values(self):
        cases = (
            ("genus_autocompleter", "Salmon", ["Salmonella"]),
            (
                "species_autocompleter",
                "Salmonella",
                ["Salmonella enterica"],
            ),
            ("mlst_autocompleter", "", ["2", "12"]),
            ("rmlst_autocompleter", "", ["34"]),
            ("mlstcc_autocompleter", "Enter", ["Enteritidis"]),
            ("serovar_autocompleter", "", ["Enteritidis"]),
        )
        for route, query, expected in cases:
            with self.subTest(route=route):
                response = self.client.get(
                    reverse(f"sequence_database:{route}"), {"q": query}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [item["text"] for item in response.json()["results"]],
                    expected,
                )

    def test_autocomplete_can_filter_by_forwarded_sequence_metadata(self):
        response = self.client.get(
            reverse("sequence_database:genus_autocompleter"),
            {
                "q": "Salmon",
                "forward": '{"species":"Salmonella enterica"}',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["text"] for item in response.json()["results"]],
            ["Salmonella"],
        )

    def test_id_search_reports_aliases_and_missing_identifiers(self):
        response = self.client.post(
            reverse("sequence_database:id_search"),
            {
                "seqids": "2023-OLD-0001 2022-MISS-0001",
                "cfiaids": "CFIA-0001 CFIA-MISSING",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["seqids"], [self.sequence_id])
        self.assertEqual(
            response.context["renamed"],
            [("2023-OLD-0001", self.cfia_id)],
        )
        self.assertEqual(
            response.context["missing_ids"],
            ["2022-MISS-0001", "CFIA-MISSING"],
        )

    def test_database_filter_applies_metadata_and_date_bounds(self):
        response = self.client.post(
            reverse("sequence_database:database_filter"),
            {
                "genus": "Salmonella",
                "start_date": "2023-12-31",
                "end_date": "2024-01-02",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["seqids"], [self.sequence_id])
        self.assertEqual(response.context["cfiaids"], [self.cfia_id])

    def test_database_query_combines_advanced_filters(self):
        response = self.client.post(
            reverse("sequence_database:database_query"),
            {
                "start_date": "2023-12-31",
                "end_date": "2024-01-02",
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-database_fields": "GENUS",
                "form-0-query_operators": "AND",
                "form-0-qualifiers": "EXACT",
                "form-0-query": "Salmonella",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["count"], 1)
        self.assertEqual(response.context["seqids"], [self.sequence_id])
        self.assertEqual(response.context["cfiaids"], [self.cfia_id])

    def test_database_query_get_renders_empty_filter_formset(self):
        response = self.client.get(reverse("sequence_database:database_query"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["count"], 1)
        self.assertEqual(response.context["seqids"], [])

    def test_id_search_get_renders_legacy_request_records(self):
        response = self.client.get(reverse("sequence_database:id_search"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("form", response.context)
        self.assertIn("query_table", response.context)
