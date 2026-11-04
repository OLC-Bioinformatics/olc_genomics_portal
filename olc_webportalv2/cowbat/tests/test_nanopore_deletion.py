from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.cowbat.models import (
    NanoporeManifest,
    NanoporeProcessingBatch,
    SequencingRun,
)
from olc_webportalv2.users.models import User


class NanoporeDeletionTests(TestCase):
    def test_staff_can_delete_run_with_nanopore_processing_batches(self):
        User.objects.create_user(
            username="nanopore-cleanup-admin",
            password="password",
            is_staff=True,
        )
        run = SequencingRun.objects.create(run_name="203001_NANOPORE_DELETE")
        manifest = NanoporeManifest.objects.create(
            sequencing_run=run,
            files=[],
            sha256="manifest-sha",
        )
        batch = NanoporeProcessingBatch.objects.create(
            sequencing_run=run,
            manifest=manifest,
        )
        self.client.login(username="nanopore-cleanup-admin", password="password")

        response = self.client.post(
            reverse(
                "cowbat:delete_assembly_run",
                kwargs={"sequencing_run_pk": run.pk},
            )
        )

        self.assertRedirects(response, reverse("cowbat:assembly_home"))
        self.assertFalse(SequencingRun.objects.filter(pk=run.pk).exists())
        self.assertFalse(NanoporeManifest.objects.filter(pk=manifest.pk).exists())
        self.assertFalse(NanoporeProcessingBatch.objects.filter(pk=batch.pk).exists())
