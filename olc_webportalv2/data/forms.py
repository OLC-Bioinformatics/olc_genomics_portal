from django import forms
import re


class DataRequestForm(forms.Form):
    seqids = forms.CharField(max_length=2048, widget=forms.Textarea, label='')

    def clean_seqids(self):
        seqid_input = self.cleaned_data['seqids']
        seqid_list = seqid_input.split()
        bad_seqids = list()
        for seqid in seqid_list:
            if not re.match('\d{4}-[A-Z]+-\d{4}', seqid):
                bad_seqids.append(seqid)
        if len(bad_seqids) > 0:
            raise forms.ValidationError('One or more of the SEQIDs you entered was not formatted correctly. '
                                        'Correct format is YYYY-LAB-####. Also, ensure that you have entered one '
                                        'SEQID per line.\n'
                                        'Invalid SEQIDS: {}'.format(bad_seqids))
        # Also check that SEQIDs are present in our database of SEQIDs
        # sequence_data_objects = SequenceData.objects.filter()
        # seqids_in_database = list()
        # bad_seqids = list()
        # for sequence_data in sequence_data_objects:
        #     seqids_in_database.append(sequence_data.seqid)
        # for seqid in seqid_list:
        #     if seqid not in seqids_in_database:
        #         bad_seqids.append(seqid)
        # if len(bad_seqids) > 0:
        #     raise forms.ValidationError('One or more of the SEQIDs you entered was not found in our database.\n'
        #                                 'SEQIDs not found: {}'.format(bad_seqids))
        return seqid_input
