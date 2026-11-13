"""Form fields for accepting multiple file uploads."""

from django import forms


class MultipleFileInput(forms.ClearableFileInput):
    """Render a file input that permits selecting multiple files."""

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Validate one or more uploaded files using Django's file field."""

    widget = MultipleFileInput

    def clean(self, data, initial=None):
        """Clean each upload while preserving single-file behavior.

        Args:
            data: One uploaded file or a sequence of uploaded files.
            initial: Initial field value used by Django form validation.

        Returns:
            One cleaned file or a list of cleaned files.

        """
        if isinstance(data, (list, tuple)):
            clean_file = super().clean
            return [clean_file(item, initial) for item in data]
        return super().clean(data, initial)
