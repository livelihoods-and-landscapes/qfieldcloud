from django.contrib.auth import get_user_model

from rest_framework import serializers

from qfieldcloud.apps.model.models import (
    Project, File, Organization, ProjectCollaborator)


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        fields = ('id', 'name', 'description', 'private',
                  'created_at')
        model = Project


class FileSerializer(serializers.ModelSerializer):
    class Meta:
        fields = ('project', 'stored_file', 'created_at')
        model = File


class CompleteUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        exclude = ('id', 'password')


class PublicInfoUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ('username', 'user_type')


class OrganizationSerializer(serializers.ModelSerializer):

    organization_owner = serializers.StringRelatedField()
    members = serializers.StringRelatedField(many=True)

    class Meta:
        model = Organization
        exclude = ('id', 'password', 'first_name', 'last_name')


class ProjectCollaboratorSerializer(serializers.ModelSerializer):
    collaborator = serializers.StringRelatedField()

    class Meta:
        model = ProjectCollaborator
        fields = ('collaborator', 'role')
