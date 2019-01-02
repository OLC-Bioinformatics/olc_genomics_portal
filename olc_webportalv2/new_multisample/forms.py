from django import forms
from olc_webportalv2.new_multisample.models import ProjectMulti
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Div, Submit, HTML, Button, Row, Field
from crispy_forms.bootstrap import AppendedText, PrependedText, FormActions


class BootstrapModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(BootstrapModelForm, self).__init__(*args, **kwargs)
        for field in iter(self.fields):
            self.fields[field].widget.attrs.update({
                'class': 'form-control'
            })


class ProjectForm(BootstrapModelForm):
    def __init__(self, *args, **kwargs):
        super(ProjectForm, self).__init__(*args, **kwargs)
        self.fields['project_title'].widget.attrs.update({'placeholder': 'Enter a project title'})
        self.fields['description'].widget.attrs.update({'placeholder': 'Enter a brief project description (optional)'})

    class Meta:
        model = ProjectMulti
        fields = ('project_title',
                  'description',)


class JobForm(forms.Form):
    JOB_CHOICES = (
         ('genesipprv2', 'GeneSippr'),
         ('sendsketch', 'SendSketch'),
         ('confindr', 'ConFindr'),
         ('genomeqaml', 'GenomeQAML'),
         ('amrdetect', 'AMR Detection'),
    )

    jobs = forms.MultipleChoiceField(choices=JOB_CHOICES,
                                     widget=forms.CheckboxSelectMultiple)
    helper = FormHelper()
    helper.form_class = 'form-horizontal'
    helper.form_show_labels = False
    helper.layout = Layout(
        Field('jobs', style="background: #FAFAFA; padding: 10px;", css_class="checkbox-primary"),
        FormActions(
           Submit('submit', 'Submit Jobs', css_class="btn-outline-primary"),
        )
    )

# class SampleForm(forms.Form):
#     files = forms.FileField(widget=forms.ClearableFileInput(attrs={'multiple': True}))
