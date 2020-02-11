from django.conf import settings
from django.contrib.auth import get_user_model

from rest_framework import serializers

from qfieldcloud.apps.model.models import (
    Project, File, Organization)


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        fields = ('id', 'name', 'description', 'private',
                  'created_at')
        model = Project


class FileSerializer(serializers.ModelSerializer):
    class Meta:
        fields = ('project', 'stored_file', 'created_at')
        model = File


class ProjectRoleSerializer(serializers.Serializer):
    role = serializers.CharField(max_length=20)

    def validate_role(self, value):
        if value not in settings.PROJECT_ROLE:
            raise serializers.ValidationError("Role has a unknown value")
        return value


class CompleteUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        exclude = ('id', 'password')


class PublicInfoUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ('username', 'first_name', 'last_name', 'user_type')


class OrganizationSerializer(serializers.ModelSerializer):

    organization_owner = serializers.StringRelatedField()
    members = serializers.StringRelatedField(many=True)

    class Meta:
        model = Organization
        exclude = ('id', 'password', 'first_name', 'last_name')
