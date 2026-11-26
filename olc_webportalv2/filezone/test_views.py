from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from olc_webportalv2.common.storage_client import StorageServiceError
from olc_webportalv2.filezone.models import ContainerName, Regexes
from olc_webportalv2.users.models import User


class FileZoneViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="filezone-view-test")
        self.client.force_login(self.user)
        self.container = ContainerName.objects.create(
            container_name="test-container"
        )

    def test_filezone_home_and_container_select_render(self):
        home = self.client.get(reverse("filezone:filezone"))
        select = self.client.get(reverse("filezone:container_select"))

        self.assertEqual(home.status_code, 200)
        self.assertEqual(select.status_code, 200)
        self.assertIn("container_form", select.context)

    def test_filezone_home_defers_search_results(self):
        project = Regexes.objects.create(
            file_matches={"container": [{"path": "large-result"}]},
            file_ajax={"container": ["large-result"]},
        )

        response = self.client.get(reverse("filezone:filezone"))
        projects = list(response.context["projects"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(projects[0].pk, project.pk)
        self.assertTrue(
            {"file_matches", "file_ajax"}.issubset(
                projects[0].get_deferred_fields()
            )
        )

    def test_container_autocomplete_filters_and_orders_names(self):
        ContainerName.objects.create(container_name="test-container-alt")
        ContainerName.objects.create(container_name="other-container")

        response = self.client.get(
            reverse("filezone:filezone_autocompleter"),
            {"q": "test-container"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["text"] for item in response.json()["results"]],
            ["test-container", "test-container-alt"],
        )

    @mock.patch("olc_webportalv2.filezone.views.refresh_container_names")
    def test_container_refresh_updates_names_then_redirects(self, refresh):
        response = self.client.get(reverse("filezone:refresh"))

        self.assertRedirects(
            response,
            reverse("filezone:container_select"),
            fetch_redirect_response=False,
        )
        refresh.assert_called_once()

    def test_container_select_redirects_after_valid_selection(self):
        response = self.client.post(
            reverse("filezone:container_select"),
            {"container_name": self.container.pk},
        )

        self.assertRedirects(
            response,
            reverse(
                "filezone:container_view",
                kwargs={"filezone_pk": self.container.pk},
            ),
            fetch_redirect_response=False,
        )

    @mock.patch(
        "olc_webportalv2.filezone.forms.find_containers",
        return_value=[],
    )
    @mock.patch("olc_webportalv2.filezone.views.create_container")
    def test_container_create_validates_and_registers_new_container(
        self, create_container, find_containers
    ):
        response = self.client.post(
            reverse("filezone:container_create"),
            {"container_name": "new-container"},
        )

        created = ContainerName.objects.get(container_name="new-container")
        self.assertRedirects(
            response,
            reverse(
                "filezone:container_view",
                kwargs={"filezone_pk": created.pk},
            ),
            fetch_redirect_response=False,
        )
        find_containers.assert_called_once()
        create_container.assert_called_once_with(
            container_name="new-container"
        )

    @mock.patch(
        "olc_webportalv2.filezone.forms.find_containers",
        return_value=[],
    )
    @mock.patch(
        "olc_webportalv2.filezone.views.create_container",
        side_effect=StorageServiceError(None, "storage unavailable"),
    )
    def test_container_create_keeps_form_open_when_storage_fails(
        self, create_container, find_containers
    ):
        response = self.client.post(
            reverse("filezone:container_create"),
            {"container_name": "new-container"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unable to create the requested")
        self.assertFalse(
            ContainerName.objects.filter(
                container_name="new-container"
            ).exists()
        )
        find_containers.assert_called_once()
        create_container.assert_called_once()

    @mock.patch(
        "olc_webportalv2.filezone.views.list_blobs",
        return_value=([], {}),
    )
    def test_container_view_get_renders_empty_container(self, list_blobs):
        response = self.client.get(
            reverse(
                "filezone:container_view",
                kwargs={"filezone_pk": self.container.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["container"], self.container)
        self.assertEqual(response.context["blobs"], [])
        list_blobs.assert_called_once_with(filezone_pk=self.container.pk)

    @mock.patch(
        "olc_webportalv2.filezone.views.calculate_checksum",
        return_value="md5-digest",
    )
    @mock.patch(
        "olc_webportalv2.filezone.views.list_blobs",
        return_value=([], {}),
    )
    @mock.patch("olc_webportalv2.filezone.views.get_storage_client")
    def test_container_view_uploads_file_and_archives_selected_blob(
        self, get_storage, list_blobs, calculate_checksum
    ):
        storage = get_storage.return_value
        mapping = {
            7: {"blob_full_path": "reports/result.txt"},
            8: {"blob_full_path": "reports/other.txt"},
        }
        list_blobs.side_effect = [([], mapping), ([], mapping)]
        archive = mock.Mock(return_value="archive-url")
        upload = SimpleUploadedFile(
            "input.txt", b"contents", content_type="text/plain"
        )

        with mock.patch(
            "olc_webportalv2.filezone.views.archive",
            archive,
        ):
            response = self.client.post(
                reverse(
                    "filezone:container_view",
                    kwargs={"filezone_pk": self.container.pk},
                ),
                {
                    "form-action": "download",
                    "selected_options[]": ["7", "999"],
                    "upload": upload,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sas_url"], "archive-url")
        calculate_checksum.assert_called_once()
        storage.upload_bytes.assert_called_once_with(
            "test-container",
            "input.txt",
            b"contents",
            content_type="text/plain",
            content_md5="md5-digest",
        )
        archive.assert_called_once_with(
            blob_client=storage,
            blob_list=[{"blob_full_path": "reports/result.txt"}],
            container_name="test-container",
            container_pk=self.container.pk,
        )

    @mock.patch(
        "olc_webportalv2.filezone.views.list_blobs",
        return_value=([], {1: {"blob_full_path": "result.txt"}}),
    )
    @mock.patch("olc_webportalv2.filezone.views.sanitise_rename")
    @mock.patch("olc_webportalv2.filezone.views.rename_blob")
    @mock.patch("olc_webportalv2.filezone.views.get_storage_client")
    def test_container_view_rejects_illegal_rename(
        self, get_storage, rename_blob, sanitise_rename, list_blobs
    ):
        sanitise_rename.return_value = ["/"]

        response = self.client.post(
            reverse(
                "filezone:container_view",
                kwargs={"filezone_pk": self.container.pk},
            ),
            {
                "form-action": "rename",
                "blob_name": "result.txt",
                "blob_rename": "../result.txt",
            },
        )

        self.assertEqual(response.status_code, 200)
        sanitise_rename.assert_called_once_with(
            blob_rename="../result.txt"
        )
        rename_blob.assert_not_called()
        get_storage.assert_called_once()
        self.assertEqual(list_blobs.call_count, 2)

    @mock.patch(
        "olc_webportalv2.filezone.views.copy_blobs",
    )
    @mock.patch("olc_webportalv2.filezone.views.get_storage_client")
    def test_file_view_copies_only_selected_matches(
        self, get_storage, copy_blobs
    ):
        request = Regexes.objects.create(
            file_matches={
                "source": [
                    {
                        "pk": 1,
                        "blob_full_path": "folder/selected.txt",
                        "container_name": "source",
                    },
                    {
                        "pk": 2,
                        "blob_full_path": "folder/other.txt",
                        "container_name": "source",
                    },
                ]
            }
        )

        response = self.client.post(
            reverse("filezone:file_view", kwargs={"filezone_pk": request.pk}),
            {
                "form-action": "copy",
                "selected_options": "1,999",
                "desired_container": "destination",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["json_results"],
            f"/static/ajax/filezone/{request.pk}/arrays.txt",
        )
        copy_blobs.assert_called_once_with(
            blob_metadata_list=[
                {
                    "pk": 1,
                    "blob_full_path": "folder/selected.txt",
                    "container_name": "source",
                }
            ],
            destination_container="destination",
        )
        get_storage.assert_called_once()

    @mock.patch(
        "olc_webportalv2.filezone.views.archive_multiple_containers",
        return_value="archive-url",
    )
    @mock.patch("olc_webportalv2.filezone.views.get_storage_client")
    def test_file_view_archives_selected_matches(
        self, get_storage, archive_multiple
    ):
        request = Regexes.objects.create(
            file_matches={
                "source": [
                    {
                        "pk": 1,
                        "blob_full_path": "folder/selected.txt",
                        "container_name": "source",
                    },
                    {
                        "pk": 2,
                        "blob_full_path": "folder/other.txt",
                        "container_name": "source",
                    },
                ]
            }
        )

        response = self.client.post(
            reverse("filezone:file_view", kwargs={"filezone_pk": request.pk}),
            {
                "form-action": "download",
                "selected_options": "2",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sas_url"], "archive-url")
        archive_multiple.assert_called_once_with(
            blob_client=get_storage.return_value,
            blob_list=[
                {
                    "pk": 2,
                    "blob_full_path": "folder/other.txt",
                    "container_name": "source",
                }
            ],
            filezone_pk=request.pk,
        )

    def test_file_select_get_renders_the_search_form(self):
        response = self.client.get(reverse("filezone:file_select"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("file_select_form", response.context)

    def test_file_view_get_renders_search_results_location(self):
        request = Regexes.objects.create(file_matches={})

        response = self.client.get(
            reverse("filezone:file_view", kwargs={"filezone_pk": request.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["json_results"],
            f"/static/ajax/filezone/{request.pk}/arrays.txt",
        )

    @mock.patch("olc_webportalv2.filezone.views.locate_files")
    def test_processing_view_queues_only_unprocessed_searches(self, locate):
        request = Regexes.objects.create(status="Unprocessed")

        response = self.client.get(
            reverse(
                "filezone:filezone_processing",
                kwargs={"filezone_pk": request.pk},
            )
        )

        request.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.status, "Processing")
        locate.apply_async.assert_called_once_with(
            queue="cowbat",
            args=(request.pk,),
            countdown=10,
        )

        self.client.get(
            reverse(
                "filezone:filezone_processing",
                kwargs={"filezone_pk": request.pk},
            )
        )
        locate.apply_async.assert_called_once()
