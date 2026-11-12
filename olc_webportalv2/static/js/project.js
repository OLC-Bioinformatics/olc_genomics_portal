/* Project specific Javascript goes here. */

/*
Formatting hack to get around crispy-forms unfortunate hardcoding
in helpers.FormHelper:

    if template_pack == 'bootstrap4':
        grid_colum_matcher = re.compile('\w*col-(xs|sm|md|lg|xl)-\d+\w*')
        using_grid_layout = (grid_colum_matcher.match(self.label_class) or
                             grid_colum_matcher.match(self.field_class))
        if using_grid_layout:
            items['using_grid_layout'] = True

Issues with the above approach:

1. Fragile: Assumes Bootstrap 4's API doesn't change (it does)
2. Unforgiving: Doesn't allow for any variation in template design
3. Really Unforgiving: No way to override this behavior
4. Undocumented: No mention in the documentation, or it's too hard for me to find
*/
document.querySelectorAll('.form-group').forEach((formGroup) => {
    formGroup.classList.remove('row');
});

if (window.jQuery) {
    window.jQuery.noConflict();
}

document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
        return;
    }

    const toggle = target.closest('[data-primer-detail-toggle]');
    if (!toggle) {
        return;
    }

    const shouldShow = toggle.getAttribute('aria-expanded') !== 'true';
    document.querySelectorAll('[data-primer-detail-row]').forEach((row) => {
        if (
            row.dataset.primer === toggle.dataset.primer &&
            row.dataset.panel === toggle.dataset.panel
        ) {
            row.hidden = !shouldShow;
        }
    });

    toggle.setAttribute('aria-expanded', String(shouldShow));
    toggle.value = `${shouldShow ? 'Hide' : 'Show'} ${toggle.dataset.panel} details`;
});
