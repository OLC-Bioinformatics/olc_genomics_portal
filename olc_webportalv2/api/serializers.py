"""
Serializers for COWBAT
"""

# Local imports
import re

# Third party imports
from rest_framework import serializers

# Local imports
from olc_webportalv2.cowbat.models import SequencingRun
from olc_webportalv2.cowbat.utils import normalize_run_name

from olc_webportalv2.cowbat.models import NanoporeFile


class SequencingRunSerializer(serializers.ModelSerializer):
    """
    Serializer for SequencingRun objects. Validates and serializes data
    related to sequencing runs.
    """
    # Add the optional_email field
    email = serializers.EmailField(required=False, allow_blank=True)

    class Meta:
        """
        Meta class to configure serializer fields and model.

        Attributes:
            model: The model class for serialization.
            fields: Fields to include in serialized output.
        """
        # Create a SequencingRun instance
        model = SequencingRun

        # Update the fields list to include all model fields
        fields = [
            'run_name',
            'basic_assembly',
            'preprocess',
            'nextseq',
            'email'  # This is not a model field but handled separately
        ]

    def create(self, validated_data):
        """
        Creates a SequencingRun instance from validated data.

        Overrides the default create method to handle the 'email' field
        separately from the SequencingRun model instance creation.
        The 'email' field is removed from the validated_data dictionary
        and handled as per the application's requirements.

        Parameters:
            validated_data (dict): Data that has been validated by the
                serializer.

        Returns:
            SequencingRun: The newly created SequencingRun instance.
        """
        # Handle 'email' field separately
        email = validated_data.pop('email', None)

        # Now, create the SequencingRun instance without 'email'
        sequencing_run = SequencingRun.objects.create(**validated_data)

        # If email is provided, add it to the emails_array
        if email:
            sequencing_run.emails_array.append(email)
            sequencing_run.save()

        return sequencing_run

    def update(self, instance, validated_data):
        """
        Updates a SequencingRun instance with validated data.

        Parameters:
            instance (SequencingRun): The SequencingRun instance to update.
            validated_data (dict): Data that has been validated by the
            serializer.

        Returns:
            SequencingRun: The updated SequencingRun instance.
        """
        # Remove 'email' from validated_data, if present
        email = validated_data.pop('email', None)

        # Update instance with the rest of validated_data
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        # If email is provided, add it to the emails_array
        if email:
            instance.emails_array.append(email)

        # Save the updated instance
        instance.save()

        return instance

    def validate_run_name(self, value):
        """
        Validates run_name format (5-6 digits, separator, lowercase suffix).

        Parameters:
        - value (str): The run_name value to validate.

        Returns:
        - str: Validated and formatted run_name.

        Raises:
        - serializers.ValidationError: If format is incorrect.
        """
        # Create a pattern for the naming requirements for run names
        pattern = r'\d{5,6}[-_][a-z]+'

        # Use re to determine whether the consolidated pattern matches
        if not re.fullmatch(pattern, value):
            raise serializers.ValidationError(
                "Invalid run name. Use 5-6 digits followed by '-' or '_' "
                "and lowercase letters."
            )
        return normalize_run_name(value)

    def validate(self, data):
        """
        Validates if basic_assembly and preprocess are mutually exclusive.

        Parameters:
        - data (dict): Incoming data to validate.

        Returns:
        - dict: Validated data.

        Raises:
        - serializers.ValidationError: If both options are selected.
        """
        # Ensure that both basic_assembly and preprocess are not both selected
        if data.get('basic_assembly') and data.get('preprocess'):
            raise serializers.ValidationError(
                "Basic assembly and Preprocess options are mutually exclusive."
                " Please only select one."
            )
        return data


class NanoporeRunSerializer(SequencingRunSerializer):
    BARCODE_KITS = ('SQK-RBK114-24',)
    run_name = serializers.CharField(max_length=64)
    instrument_name = serializers.CharField(max_length=128, required=False,
                                             allow_blank=True, write_only=True)
    instrument_type = serializers.CharField(max_length=128, required=False,
                                             allow_blank=True, write_only=True)
    source_directory = serializers.CharField(max_length=512, required=False,
                                              allow_blank=True, write_only=True)
    dorado_model = serializers.CharField(max_length=128, required=False,
                                         allow_blank=True, write_only=True)
    reference_panel = serializers.CharField(max_length=128, required=False,
                                             allow_blank=True, write_only=True)
    barcode_kit = serializers.CharField(max_length=64)
    barcode_values = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=24),
        allow_empty=False,
    )
    sample_metadata = serializers.JSONField()
    class Meta(SequencingRunSerializer.Meta):
        fields = [
            'run_name', 'basic_assembly', 'preprocess', 'nextseq', 'email',
            'instrument_name', 'instrument_type', 'source_directory',
            'dorado_model', 'reference_panel', 'barcode_kit',
            'barcode_values', 'sample_metadata'
        ]

    def create(self, validated_data):
        email = validated_data.pop('email', None)
        metadata = {
            field: validated_data.pop(field, '')
            for field in (
                'instrument_name', 'instrument_type', 'source_directory',
                'dorado_model', 'reference_panel'
            )
        }
        metadata = dict(metadata,
                        barcode_kit=validated_data.pop('barcode_kit'),
                        barcode_values=validated_data.pop('barcode_values'),
                        sample_metadata=validated_data.pop('sample_metadata'))
        sequencing_run = SequencingRun.objects.create(
            user=self.context['request'].user,
            workflow_state='created',
            **validated_data
        )
        if email:
            sequencing_run.emails_array.append(email)
            sequencing_run.save(update_fields=['emails_array'])
        from olc_webportalv2.cowbat.models import NanoporeRun
        NanoporeRun.objects.create(
            sequencing_run=sequencing_run,
            created_by=self.context['request'].user,
            **metadata
        )
        return sequencing_run

    def validate_barcode_kit(self, value):
        if value not in self.BARCODE_KITS:
            raise serializers.ValidationError('Unsupported barcode kit.')
        return value

    def validate(self, data):
        data = super(NanoporeRunSerializer, self).validate(data)
        barcode_values = data.get('barcode_values', [])
        if len(barcode_values) != len(set(barcode_values)):
            raise serializers.ValidationError(
                {'barcode_values': 'Barcode values must be unique.'}
            )
        sample_metadata = data.get('sample_metadata', {})
        samples = sample_metadata.get('samples') if isinstance(
            sample_metadata, dict
        ) else None
        if not isinstance(samples, list) or not samples:
            raise serializers.ValidationError(
                {'sample_metadata': 'samples must be a non-empty list.'}
            )
        if len(samples) != len(barcode_values):
            raise serializers.ValidationError(
                {'sample_metadata': 'Every barcode must have one sample.'}
            )
        seen_barcodes = []
        seen_seqids = []
        for sample in samples:
            if not isinstance(sample, dict):
                raise serializers.ValidationError(
                    {'sample_metadata': 'Each sample must be an object.'}
                )
            barcode = sample.get('barcode')
            seqid = sample.get('seqid')
            if barcode not in barcode_values:
                raise serializers.ValidationError(
                    {'sample_metadata': 'Sample barcode is not configured.'}
                )
            if barcode in seen_barcodes:
                raise serializers.ValidationError(
                    {'sample_metadata': 'Sample barcodes must be unique.'}
                )
            if not isinstance(seqid, str) or not re.match(
                    r'^\d{4}-MIN-\d{4}$', seqid):
                raise serializers.ValidationError(
                    {'sample_metadata': 'SEQIDs must match YYYY-MIN-NNNN.'}
                )
            if seqid in seen_seqids:
                raise serializers.ValidationError(
                    {'sample_metadata': 'SEQIDs must be unique.'}
                )
            olnid = sample.get('olnid')
            if not isinstance(olnid, str) or not olnid.strip():
                raise serializers.ValidationError(
                    {'sample_metadata': 'Every sample requires an OLNID.'}
                )
            seen_barcodes.append(barcode)
            seen_seqids.append(seqid)
        if set(seen_barcodes) != set(barcode_values):
            raise serializers.ValidationError(
                {'sample_metadata': 'Every configured barcode must be present.'}
            )
        return data


class NanoporeFileSerializer(serializers.ModelSerializer):
    batch_id = serializers.IntegerField(
        source='processing_batch_id', read_only=True
    )

    class Meta:
        model = NanoporeFile
        fields = [
            'id', 'relative_path', 'size_bytes', 'sha256', 'status',
            'batch_id'
        ]
        read_only_fields = ['id', 'status']
