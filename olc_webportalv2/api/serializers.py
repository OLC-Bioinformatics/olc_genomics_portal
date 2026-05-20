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
        Validates run_name format (YYMMDD-lab or YYMMDD_M###).

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
        if not re.match(pattern, value):
            raise serializers.ValidationError(
                "Invalid run name. Format must be YYMMDD-lab or YYMMDD_LAB."
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
