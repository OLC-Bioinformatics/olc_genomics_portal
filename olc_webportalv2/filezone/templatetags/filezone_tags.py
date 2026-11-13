"""Template tags for the FileZone app."""

from django import template
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext as _

register = template.Library()


@register.simple_tag
def render_blob_hierarchy(data, level=0, path=''):
    """Render a nested blob listing as escaped table rows.

    Args:
        data: Nested file and folder dictionaries to render.
        level: Current nesting depth.
        path: Parent path used to build each row's data-path.

    Returns:
        Safe HTML for the blob and folder rows, or an error message for
        malformed input.

    """
    if not isinstance(data, dict):
        return 'Invalid data format'

    def render_items(data, level, path):
        """Build escaped rows recursively for the current folder level.

        Args:
            data: Items to render at this level.
            level: Current nesting depth.
            path: Parent path for the current items.

        Returns:
            Escaped folder and file rows.

        """
        html_output = []
        items = sorted(
            data.items(),
            key=lambda x: (x[1].get('__type__') != 'folder', x[0].lower()),
        )
        for key, value in items:
            current_path = f'{path}/{key}' if path else key
            if value.get('__type__') == 'folder':
                html_output.append(
                    format_html(
                        '<tr class="folder" data-level="{}" data-path="{}">'
                        '<td><input type="checkbox" class="folder-checkbox" '
                        'data-level="{}" '
                        'onchange="checkChildren(this, {})"></td>'
                        '<td style="padding-left: {}px;">'
                        '<i class="fa fa-folder" aria-hidden="true"></i> '
                        '{}</td>'
                        '<td></td><td></td><td></td><td></td><td></td>'
                        '</tr>',
                        level,
                        current_path,
                        level,
                        level,
                        level * 20,
                        key,
                    )
                )
                html_output.extend(
                    render_items(value['contents'], level + 1, current_path)
                )
            else:
                blob_name = value['blob_name']
                html_output.append(
                    format_html(
                        '<tr class="file" data-level="{}" data-path="{}">'
                        '<td><input type="checkbox" class="file-checkbox" '
                        'data-level="{}" name="selected_options[]" '
                        'value="{}"></td>'
                        '<td style="padding-left: {}px;">{}</td>'
                        '<td>{}</td><td>{}</td><td>{}</td>'
                        '<td><button type="submit" class="btn btn-warning" '
                        'value="{}" id="{}" name="rename" '
                        'data-filename="{}">{}</button></td>'
                        '<td><a href="{}" class="btn btn-primary btn-block" '
                        'role="button">{}</a></td></tr>',
                        level,
                        current_path,
                        level,
                        value['pk'],
                        level * 20,
                        blob_name,
                        value['blob_size'],
                        value['blob_date'],
                        value['blob_md5'],
                        value['pk'],
                        blob_name.replace(' ', '_'),
                        blob_name,
                        _('Rename'),
                        value['blob_download_link'],
                        _('Download'),
                    )
                )
        return html_output

    html_output = render_items(data, level, path)
    return format_html_join('', '{}', ((row,) for row in html_output))
