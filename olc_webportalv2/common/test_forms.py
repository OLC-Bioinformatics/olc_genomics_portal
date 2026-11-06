import unittest

from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile

from olc_webportalv2.common.forms import MultipleFileField


class MultipleFileFieldTests(unittest.TestCase):
    def test_accepts_and_validates_multiple_uploads(self):
        class UploadForm(forms.Form):
            uploads = MultipleFileField(required=False)

        form = UploadForm(
            data={},
            files={
                "uploads": [
                    SimpleUploadedFile("first.txt", b"first"),
                    SimpleUploadedFile("second.txt", b"second"),
                ]
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            [upload.name for upload in form.cleaned_data["uploads"]],
            ["first.txt", "second.txt"],
        )


if __name__ == "__main__":
    unittest.main()
