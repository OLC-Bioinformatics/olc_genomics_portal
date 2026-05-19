"""
Template tags for the FileZone app
"""

# Third party imports
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

register = template.Library()


@register.simple_tag
def render_blob_hierarchy(data, level=0, path=''):
    """
    Adjusted version of render_blob_hierarchy to work with provided JavaScript
    for toggling folder visibility, adding a checkbox for each file, and
    including a folder icon before folder names. Adds a checkbox for folders
    and a data-path attribute for hierarchical path tracking.
    """
    if not isinstance(data, dict):
        return 'Invalid data format'

    def render_items(data, level, path):
        html_output = []
        items = sorted(
            data.items(), key=lambda x: (
                x[1].get('__type__') != 'folder', x[0].lower()
            )
        )
        for key, value in items:
            current_path = "{path}/{key}".format(path=path, key=key) if path else key
            if value.get('__type__') == 'folder':
                folder_html = ('<tr class="folder" data-level="{level}" data-path="{path}">'
                               '<td><input type="checkbox" class="folder-checkbox" data-level="{level}" onchange="checkChildren(this, {level})"></td>'
                               '<td style="padding-left: {indent}px;"><i class="fa fa-folder" aria-hidden="true"></i> {name}</td>'
                               '<td></td>'  # Empty <td> for column 3
                               '<td></td>'  # Empty <td> for column 4
                               '<td></td>'  # Empty <td> for column 5
                               '<td></td>'  # Empty <td> for column 6
                               '<td></td>'  # Empty <td> for column 7
                               '</tr>').format(level=level, path=current_path, indent=level * 20, name=key)
                html_output.append(folder_html)
                html_output.extend(render_items(value['contents'], level + 1, current_path))
            else:
                file_html = (
                    '<tr class="file" data-level="{level}" data-path="{path}">'
                    '<td><input type="checkbox" class="file-checkbox" data-level="{level}" name="selected_options[]" value="{pk}"></td>'
                    '<td style="padding-left: {indent}px;">{blob_name}</td>'
                    '<td>{blob_size}</td><td>{blob_date}</td><td>{blob_md5}</td>'
                    # Include the data-filename attribute in the button
                    '<td><button type="submit" class="btn btn-warning" value="{pk}" id="{blob_name_id}" name="rename" data-filename="{blob_name_escaped}">{rename_text}</button></td>'
                    '<td><a href="{blob_download_link}" class="btn btn-primary btn-block" role="button">{download_text}</a></td></tr>'
                ).format(
                    level=level, path=current_path, indent=level * 20, blob_name=value["blob_name"],
                    blob_size=value["blob_size"], blob_date=value["blob_date"],
                    blob_md5=value["blob_md5"], pk=value["pk"], blob_name_id=value["blob_name"].replace(" ", "_"),
                    # Ensure the file name is properly escaped for HTML
                    blob_name_escaped=escape(value["blob_name"]),
                    rename_text=_("Rename"), blob_download_link=value["blob_download_link"], download_text=_("Download")
                )
                html_output.append(file_html)
        return html_output

    html_output = render_items(data, level, path)
    return mark_safe(''.join(html_output))
