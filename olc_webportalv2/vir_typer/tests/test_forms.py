#!/usr/bin/env python3
from html import unescape

from django.db.utils import IntegrityError
from django.forms import formset_factory
from django.test import TestCase
from django.utils.translation import override

from olc_webportalv2.users.models import User
from olc_webportalv2.vir_typer.forms import (
    BaseVirTyperSampleFormSet,
    EmailForm,
    VirTyperProjectForm,
    VirTyperSampleForm,
)
from olc_webportalv2.vir_typer.models import (
    VirTyperFiles,
    VirTyperProject,
    VirTyperRequest,
)

__author__ = "adamkoziol"


class VirusTyperLabelTests(TestCase):
    def test_email_label_has_compact_colon_in_french(self):
        with override("fr"):
            label = EmailForm()["email"].label_tag()

        self.assertIn("Courriel:</label>", label)
        self.assertNotIn("\xa0:", label)


class VirusTyperProject(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create(username="TestProject")
        user.set_password("password")
        user.save()
        project = VirTyperProject.objects.create(
            project_name="test_project", user=user
        )
        project.save()

    def test_project_pk_exists(self):
        vir_typer_project = VirTyperProject.objects.get(
            project_name="test_project"
        )
        self.assertEqual(vir_typer_project.project_name, "test_project")

    def test_project_pk_does_not_exist(self):
        with self.assertRaises(VirTyperProject.DoesNotExist):
            VirTyperProject.objects.get(pk=0)

    def test_project_form_create(self):
        form = VirTyperProjectForm(
            {"project_name": "test", "user": "TestUser"}
        )
        self.assertTrue(form.is_valid())

    def test_project_form_wrong_entry(self):
        form = VirTyperProjectForm({"project": "test", "user": "TestUser"})
        self.assertFalse(form.is_valid())

    def test_project_model_duplicated_project_name(self):
        with self.assertRaises(IntegrityError):
            user = VirTyperProject.objects.get(
                project_name="test_project"
            ).user
            project = VirTyperProject.objects.create(
                project_name="test_project", user=user
            )
            project.save()

    def test_project_form_empty_project_name(self):
        form = VirTyperProjectForm({"project_name": "", "user": "TestUser"})
        self.assertFalse(form.is_valid())


class VirusTyperRequest(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create(username="TestRequest")
        user.set_password("password")
        user.save()
        project = VirTyperProject.objects.create(
            project_name="test_request", user=user
        )
        project.save()
        request = VirTyperRequest.objects.create(
            project_name=project,
            lab_ID="St. Hyacinthe",
            isolate_source="huître",
            LSTS_ID="2019FPP00000475214",
            putative_classification="Norovirus genogroup 1",
            sample_name="VI482",
            subunit=1,
            date_received="2019-08-01",
            analyst_name="Rachel",
        )
        request.save()
        request = VirTyperRequest.objects.create(
            project_name=project,
            lab_ID="Burnaby",
            isolate_source="Oyster",
            LSTS_ID="2019FPP00000475215",
            putative_classification="Norovirus genogroup 2",
            sample_name="VI483",
            date_received="2019-08-09",
            analyst_name="Rachel",
        )
        request.save()

    def test_request_sample_names(self):
        vir_typer_samples = VirTyperRequest.objects.all()
        self.assertEqual(
            [sample.sample_name for sample in vir_typer_samples],
            ["VI482", "VI483"],
        )

    def test_request_form_single_form_create(self):
        sample = {
            "sample_name": "VI482",
            "date_received": "2019-08-01",
            "LSTS_ID": "2019FPP00000475214",
            "lab_ID": "STH",
            "subunit": 1,
            "putative_classification": "NOVI",
            "isolate_source": "huître",
            "analyst_name": "Rachel",
        }
        form = VirTyperSampleForm(sample)
        self.assertTrue(form.is_valid())

    def test_request_form_form_create_missing_required_field(self):
        sample = {
            "sample_name": "",
            "date_received": "2019-08-01",
            "LSTS_ID": "2019FPP00000475214",
            "lab_ID": "STH",
            "subunit": 1,
            "putative_classification": "NOVI",
            "isolate_source": "huître",
            "analyst_name": "Rachel",
        }
        form = VirTyperSampleForm(sample)
        self.assertFalse(form.is_valid())

    def test_formset_required_field_message_is_translated(self):
        sample_formset = formset_factory(
            VirTyperSampleForm,
            formset=BaseVirTyperSampleFormSet,
            extra=1,
        )
        data = {
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-sample_name": "",
            "form-0-date_received": "2019-08-01",
            "form-0-LSTS_ID": "2019FPP00000475214",
            "form-0-lab_ID": "STH",
            "form-0-subunit": "1",
            "form-0-putative_classification": "NOVI",
            "form-0-isolate_source": "huître",
            "form-0-analyst_name": "Rachel",
        }

        with override("fr"):
            formset = sample_formset(data)

            self.assertFalse(formset.is_valid())
            self.assertIn(
                "Le nom de l'échantillon est obligatoire",
                unescape(str(formset.non_form_errors())),
            )

    def test_request_form_bulk_create(self):
        sample_data = list()
        pk = VirTyperProject.objects.get(project_name="test_request").pk
        sample_data.append(
            VirTyperRequest(
                project_name_id=pk,
                sample_name="VI484",
                date_received="2019-08-01",
                LSTS_ID="2019FPP00000475216",
                lab_ID="STH",
                subunit=1,
                putative_classification="NOVI",
                isolate_source="huître",
                analyst_name="Rachel",
            )
        )
        sample_data.append(
            VirTyperRequest(
                project_name_id=pk,
                sample_name="VI485",
                date_received="2019-08-09",
                LSTS_ID="2019FPP00000475217",
                lab_ID="BUR",
                putative_classification="NOVII",
                isolate_source="Oyster",
                analyst_name="Rachel",
            )
        )
        VirTyperRequest.objects.bulk_create(sample_data)
        vir_typer_samples = VirTyperRequest.objects.all()
        self.assertEqual(
            [sample.sample_name for sample in vir_typer_samples],
            ["VI482", "VI483", "VI484", "VI485"],
        )


class VirusTyperFiles(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = User.objects.create(username="TestResults")
        user.set_password("password")
        user.save()
        project = VirTyperProject.objects.create(
            project_name="test_files", user=user
        )
        project.save()
        # Sample 1
        request = VirTyperRequest.objects.create(
            project_name=project,
            lab_ID="St. Hyacinthe",
            isolate_source="huître",
            LSTS_ID="2019FPP00000475214",
            putative_classification="Norovirus genogroup 1",
            sample_name="VI482",
            subunit=1,
            date_received="2019-08-01",
            analyst_name="Rachel",
        )
        request.save()
        files = VirTyperFiles.objects.create(
            sample_name=request,
            sequence_file="P19954_2019FPP00000475214_VI482_11_GI_B05_M13-R17_G10_068.ab1",
        )
        files.save()
        files = VirTyperFiles.objects.create(
            sample_name=request,
            sequence_file="P19954_2019FPP00000475214_VI482_11_GI_B04_M13-R17_F10_070.ab1",
        )
        files.save()
        files = VirTyperFiles.objects.create(
            sample_name=request,
            sequence_file="P19954_2019FPP00000475214_VI482_11_GI_B03_M13-R17_E10_072.ab1",
        )
        files.save()
        request = VirTyperRequest.objects.create(
            project_name=project,
            lab_ID="Burnaby",
            isolate_source="Oyster",
            LSTS_ID="2019FPP00000475215",
            putative_classification="Norovirus genogroup 2",
            sample_name="VI483",
            date_received="2019-08-09",
            analyst_name="Rachel",
        )
        request.save()
        # Sample 2
        files = VirTyperFiles.objects.create(
            sample_name=request,
            sequence_file="P19954_2019FPP00000475215_VI483_11_GI_B05_M13-R17_G10_074.ab1",
        )
        files.save()

    def test_files_names(self):
        seq_files = VirTyperFiles.objects.all()
        sample_file_counts = {}
        for sequence_file in seq_files:
            sample_name = sequence_file.sample_name.sample_name
            sample_file_counts[sample_name] = (
                sample_file_counts.get(sample_name, 0) + 1
            )
        self.assertEqual(sample_file_counts, {"VI482": 3, "VI483": 1})
        self.assertEqual(
            set([sample.sequence_file for sample in seq_files]),
            {
                "P19954_2019FPP00000475214_VI482_11_GI_B05_M13-R17_G10_068.ab1",
                "P19954_2019FPP00000475214_VI482_11_GI_B04_M13-R17_F10_070.ab1",
                "P19954_2019FPP00000475214_VI482_11_GI_B03_M13-R17_E10_072.ab1",
                "P19954_2019FPP00000475215_VI483_11_GI_B05_M13-R17_G10_074.ab1",
            },
        )


class VirusTyperResults(TestCase):
    pass
