from django import forms
from Bio import SeqIO
from io import StringIO
import re
from olc_webportalv2.metadata.models import SequenceData
from olc_webportalv2.geneseekr.models import GeneSeekrRequest

from django.forms.widgets import EmailInput


class NearNeighborForm(forms.Form):
    name = forms.CharField(max_length=56, label='Name: ', required=False)
    seqid = forms.CharField(max_length=24, label='SeqID: ', required=False)
    uploaded_file = forms.FileField(label='', required=False)
    number_neighbors = forms.IntegerField(label='Number neighbors: ', initial=2, required=True)

    def clean(self):
        MIN_NUM_NEIGHBORS = 1
        MAX_NUM_NEIGHBORS = 250
        super().clean()
        seqid = self.cleaned_data.get('seqid')
        name = self.cleaned_data.get('name')
        number_neighbors = self.cleaned_data.get('number_neighbors')
        uploaded_file = self.cleaned_data.get('uploaded_file')
        sequence_data_objects = SequenceData.objects.filter()
        seqids_in_database = list()
        for sequence_data in sequence_data_objects:
            seqids_in_database.append(sequence_data.seqid)
        if uploaded_file is None and seqid == '':
            raise forms.ValidationError('Must enter at least one of SeqID or uploaded file.')
        elif seqid != '' and uploaded_file is not None:
            raise forms.ValidationError('Only enter one of SeqID and uploaded file.')
        elif uploaded_file is not None:
            if not uploaded_file.name.endswith('.fasta'):
                raise forms.ValidationError('Uploaded file must be in FASTA format and filename must end with .fasta')
        elif seqid not in seqids_in_database:
            raise forms.ValidationError('Requested SEQID is not in the database. Correct format for SeqID is '
                                        'YYYY-LAB-####. Please check your SEQID and try again.')
        if not MIN_NUM_NEIGHBORS <= number_neighbors <= MAX_NUM_NEIGHBORS:
            raise forms.ValidationError('Invalid number of nearest neighbors requested. Valid values are from 1 to 250.')
        return seqid, name, number_neighbors, uploaded_file


class GeneSeekrForm(forms.Form):
    seqids = forms.CharField(max_length=100000, widget=forms.Textarea(attrs={'placeholder': 'YYYY-LAB-####'}), label='', required=False)
    query_sequence = forms.CharField(max_length=10000, widget=forms.Textarea(attrs={'placeholder': '>Gene\nACGTACGT'}), label='', required=False)
    query_file = forms.FileField(label='', required=False)
    genus = forms.CharField(max_length=48, label='', required=False)  # TODO: Genus should be an autocomplete field
    everything_but = forms.BooleanField(required=False)

    def clean(self):
        super().clean()
        seqid_input = self.cleaned_data.get('seqids')
        query_sequence = self.cleaned_data.get('query_sequence')
        query_file = self.cleaned_data.get('query_file')
        genus = self.cleaned_data.get('genus')
        exclude = self.cleaned_data.get('everything_but')

        # Check that SEQIDs specified are in valid SEQID format.
        seqid_list = seqid_input.split()
        bad_seqids = list()
        for seqid in seqid_list:
            if not re.match('\d{4}-[A-Z]+-\d{4}', seqid):
                bad_seqids.append(seqid)
        if len(bad_seqids) > 0:
            raise forms.ValidationError('One or more of the SEQIDs you entered was not formatted correctly. '
                                        'Correct format is YYYY-LAB-####. Also, ensure that you have entered one '
                                        'SEQID per line.\n'
                                        'Invalid SEQIDs: {}'.format(bad_seqids))
        # Also check that SEQIDs are present in our database of SEQIDs
        sequence_data_objects = SequenceData.objects.filter()
        seqids_in_database = list()
        bad_seqids = list()
        for sequence_data in sequence_data_objects:
            seqids_in_database.append(sequence_data.seqid)
        for seqid in seqid_list:
            if seqid not in seqids_in_database:
                bad_seqids.append(seqid)
        if len(bad_seqids) > 0:
            raise forms.ValidationError('One or more of the SEQIDs you entered was not found in our database.\n'
                                        'SEQIDs not found: {}'.format(bad_seqids))

        # If user didn't input SeqIDs into the SeqID text box, do filtering based on Genus.
        if len(seqid_list) == 0:
            sequence_data_objects = SequenceData.objects.filter()
            for sequence_data in sequence_data_objects:
                if exclude is True:
                    if sequence_data.genus.upper() != genus.upper():
                        seqid_list.append(sequence_data.seqid)
                elif exclude is False:
                    if sequence_data.genus.upper() == genus.upper() or genus == '':
                        seqid_list.append(sequence_data.seqid)

        # Now check that we actually have some SeqIDs, or things will break.
        if len(seqid_list) == 0:
            if exclude is False:
                raise forms.ValidationError('Your query did not correspond to any sequences in our database: query was for '
                                            'sequences from genus {}'.format(genus))
            else:
                raise forms.ValidationError('Your query did not correspond to any sequences in our database: query was for '
                                            'sequences NOT from genus {}'.format(genus))


        # Ensure that query sequence or query file was submitted
        if query_sequence == '' and query_file is None:
            raise forms.ValidationError('No input found! You must submit a FASTA sequence by pasting it into the text '
                                        'box or uploading a FASTA file.')
        elif query_sequence != '' and query_file is not None:
            raise forms.ValidationError('Multiple inputs found! You must submit a FASTA sequence by pasting it into '
                                        'the text box or uploading a FASTA file, but not both.')

        # Check proper FASTA format. Must have at least one sequence, and that must have only A,C,T,G or N
        # Check query sequence, if specified.
        if query_sequence != '':
            num_sequences = 0
            sequences = SeqIO.parse(StringIO(query_sequence), 'fasta')
            valid_bases = set('ACTGN')
            for sequence in sequences:
                num_sequences += 1
                if not set(str(sequence.seq).upper()).issubset(valid_bases):
                    raise forms.ValidationError('Your FASTA sequence contains invalid characters. Sequence should '
                                                'only contain valid nucleotides (A, C, T, G, N).')

            if num_sequences == 0:
                raise forms.ValidationError('Invalid FASTA sequence entered. Correct format is:\n>sequencename\nACTGATCGA')

        # Check query file, if that's what was specified.
        if query_file is not None:
            num_nucleotides = 0
            num_sequences = 0
            sequence_data = query_file.read().decode('utf-8')
            sequences = SeqIO.parse(StringIO(sequence_data), 'fasta')
            valid_bases = set('ACTGN')
            for sequence in sequences:
                num_sequences += 1
                num_nucleotides += len(sequence.seq)
                if not set(str(sequence.seq).upper()).issubset(valid_bases):
                    raise forms.ValidationError('Your FASTA sequence contains invalid characters. Sequence should '
                                                'only contain valid nucleotides (A, C, T, G, N).')

            if num_sequences == 0:
                raise forms.ValidationError('Invalid FASTA sequence entered. Correct format is:\n>sequencename\nACTGATCGA')

            if num_nucleotides > 10000:
                raise forms.ValidationError('FASTA sequence length maximum is 10000 bases. Your input sequence '
                                            'had {} bases.'.format(num_nucleotides))
        return seqid_list, query_sequence


class ParsnpForm(forms.Form):
    name = forms.CharField(label='Name: ', required=False, widget=forms.TextInput(attrs={'placeholder': 'Optional'}))
    seqids = forms.CharField(max_length=100000, widget=forms.Textarea(attrs={'placeholder': 'YYYY-LAB-####'}), label='', required=False)

    other_files = forms.FileField(widget=forms.ClearableFileInput(attrs={'multiple': True}), required=False, label='')
    tree_program = forms.ChoiceField(label='Which tree program? ', initial='mashtree',choices=[('mashtree', 'mashtree'),('parsnp', 'parsnp')], widget=forms.RadioSelect())
    number_diversitree_strains = forms.IntegerField(min_value=0,required=False)
    def clean(self):
        super().clean()
        #KeyError raises when only whitespace is submitted
        try:
            seqid_input = self.cleaned_data['seqids']
        except KeyError:
            raise forms.ValidationError('Input cannot be only whitespace')
        try:
            name = self.cleaned_data['name']
        except KeyError:
            name = None
        try:
            tree_program = self.cleaned_data['tree_program']
        except KeyError:
            tree_program = 'parsnp'
        try:
            number_diversitree_strains = self.cleaned_data['number_diversitree_strains']
        except KeyError:
            number_diversitree_strains = 0

        other_files = self.files.getlist('other_files')
        # Check that SEQIDs specified are in valid SEQID format.
        seqid_list = seqid_input.split()
        bad_seqids = list()
        for seqid in seqid_list:
            if not re.match('\d{4}-[A-Z]+-\d{4}', seqid):
                bad_seqids.append(seqid)
        if len(bad_seqids) > 0:
            raise forms.ValidationError('One or more of the SEQIDs you entered was not formatted correctly. '
                                        'Correct format is YYYY-LAB-####. Also, ensure that you have entered one '
                                        'SEQID per line.\n'
                                        'Invalid SEQIDs: {}'.format(bad_seqids))
        # Also check that SEQIDs are present in our database of SEQIDs
        sequence_data_objects = SequenceData.objects.filter()    
        seqids_in_database = list()
        bad_seqids = list()
        for sequence_data in sequence_data_objects:
            seqids_in_database.append(sequence_data.seqid)
        for seqid in seqid_list:
            if seqid not in seqids_in_database:
                bad_seqids.append(seqid)
        if len(bad_seqids) > 0:
            raise forms.ValidationError('One or more of the SEQIDs you entered was not found in our database.\n'
                                        'SEQIDs not found: {}'.format(bad_seqids))
        # Diversitree strains can't be greater than total strains
        if number_diversitree_strains is not None:
            if len(seqid_list) + len(other_files) < number_diversitree_strains:
                raise forms.ValidationError('Too many Diversitree strains selected, must be {} or less'.format(len(seqid_list)))
        for other_file in other_files:
            if not other_file.name.endswith('.fasta'):
                raise forms.ValidationError('All files uploaded must be in FASTA format with the extension .fasta')
        if len(seqid_list) + len(other_files) < 2:
            raise forms.ValidationError('At least 2 input sequences must be given.')
        return seqid_list, name, tree_program, number_diversitree_strains, other_files


class AMRForm(forms.Form):
    name = forms.CharField(label='Name: ', required=False, widget=forms.TextInput(attrs={'placeholder': 'Optional'}))
    seqids = forms.CharField(max_length=100000, widget=forms.Textarea(attrs={'placeholder': 'YYYY-LAB-####'}), label='', required=True)

    def clean(self):
        super().clean()
        #KeyError raises when only whitespace is submitted
        try:
            seqid_input = self.cleaned_data['seqids']
        except KeyError:
            raise forms.ValidationError('Input cannot be only whitespace')
        try:
            name = self.cleaned_data['name']
        except KeyError:
            name = None

        # Check that SEQIDs specified are in valid SEQID format.
        seqid_list = seqid_input.split()
        bad_seqids = list()
        for seqid in seqid_list:
            if not re.match('\d{4}-[A-Z]+-\d{4}', seqid):
                bad_seqids.append(seqid)
        if len(bad_seqids) > 0:
            raise forms.ValidationError('One or more of the SEQIDs you entered was not formatted correctly. '
                                        'Correct format is YYYY-LAB-####. Also, ensure that you have entered one '
                                        'SEQID per line.\n'
                                        'Invalid SEQIDs: {}'.format(bad_seqids))
        # Also check that SEQIDs are present in our database of SEQIDs
        sequence_data_objects = SequenceData.objects.filter()    
        seqids_in_database = list()
        bad_seqids = list()
        for sequence_data in sequence_data_objects:
            seqids_in_database.append(sequence_data.seqid)
        for seqid in seqid_list:
            if seqid not in seqids_in_database:
                bad_seqids.append(seqid)
        if len(bad_seqids) > 0:
            raise forms.ValidationError('One or more of the SEQIDs you entered was not found in our database.\n'
                                        'SEQIDs not found: {}'.format(bad_seqids))

        return seqid_list, name


class ProkkaForm(forms.Form):
    name = forms.CharField(label='Name: ', required=False, widget=forms.TextInput(attrs={'placeholder': 'Optional'}))
    seqids = forms.CharField(max_length=100000, widget=forms.Textarea(attrs={'placeholder': 'YYYY-LAB-####'}), label='', required=False)
    other_files = forms.FileField(widget=forms.ClearableFileInput(attrs={'multiple': True}), required=False, label='')

    def clean(self):
        super().clean()
        # KeyError raises when only whitespace is submitted
        try:
            seqid_input = self.cleaned_data['seqids']
        except KeyError:
            raise forms.ValidationError('Input cannot be only whitespace')
        try:
            name = self.cleaned_data['name']
        except KeyError:
            name = None

        other_files = self.files.getlist('other_files')

        # Check that SEQIDs specified are in valid SEQID format.
        seqid_list = seqid_input.split()
        bad_seqids = list()
        for seqid in seqid_list:
            if not re.match('\d{4}-[A-Z]+-\d{4}', seqid):
                bad_seqids.append(seqid)
        if len(bad_seqids) > 0:
            raise forms.ValidationError('One or more of the SEQIDs you entered was not formatted correctly. '
                                        'Correct format is YYYY-LAB-####. Also, ensure that you have entered one '
                                        'SEQID per line.\n'
                                        'Invalid SEQIDs: {}'.format(bad_seqids))
        # Also check that SEQIDs are present in our database of SEQIDs
        sequence_data_objects = SequenceData.objects.filter()    
        seqids_in_database = list()
        bad_seqids = list()
        for sequence_data in sequence_data_objects:
            seqids_in_database.append(sequence_data.seqid)
        for seqid in seqid_list:
            if seqid not in seqids_in_database:
                bad_seqids.append(seqid)
        if len(bad_seqids) > 0:
            raise forms.ValidationError('One or more of the SEQIDs you entered was not found in our database.\n'
                                        'SEQIDs not found: {}'.format(bad_seqids))
        if len(seqid_list) == 0 and len(other_files) == 0:
            raise forms.ValidationError('Must enter at least one SeqID or upload at least one file.')
        for other_file in other_files:
            if not other_file.name.endswith('.fasta'):
                raise forms.ValidationError('All files uploaded must be in FASTA format with the extension .fasta')

        return seqid_list, name, other_files


class NameForm(forms.Form):
    name = forms.CharField(label='Name ', required=False ,widget=forms.TextInput(attrs={'placeholder': 'Optional'}))


class EmailForm(forms.Form):
    email = forms.EmailField(max_length=50,label= "Email ")
