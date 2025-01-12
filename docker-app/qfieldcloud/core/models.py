import os
import secrets
import string
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import List

import django_cryptography.fields
import qfieldcloud.core.utils2.storage
from auditlog.registry import auditlog
from deprecated import deprecated
from django.contrib.auth.models import AbstractUser, UserManager
from django.contrib.gis.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db.models import Case, Exists, F, OuterRef, Q
from django.db.models import Value as V
from django.db.models import When
from django.db.models.aggregates import Count, Sum
from django.db.models.fields.json import JSONField
from django.urls import reverse_lazy
from django.utils.functional import cached_property
from django.utils.translation import gettext as _
from model_utils.managers import InheritanceManager
from qfieldcloud.core import geodb_utils, utils, validators
from qfieldcloud.subscription.models import AccountType
from timezone_field import TimeZoneField

# http://springmeblog.com/2018/how-to-implement-multiple-user-types-with-django/


class UserQueryset(models.QuerySet):
    """Adds for_project(user) method to the user's querysets, allowing to filter only users part of a project.

    Users are annotated with the user's project role (`project_role`) and the origin of this role (`project_role_origin`).
    If the project is public, it will return only the directly collaborated users.

    Args:
        project:               project to find users for

    Usage:
    ```
    # List all users that are involved in OpenKebabMap.
    Users.object.for_project(OpenKebabMap)
    ```

    Note:
    This query is very similar to `ProjectQueryset.for_user`, don't forget to update it too.
    """

    def for_project(self, project: "Project"):

        # This is a list of tuples defining project memberships
        # List[(Condition, Role, RoleOrigin)]

        permissions_config = [
            # Project owner
            (
                Q(pk=project.owner.pk, user_type=User.TYPE_USER),
                V(ProjectCollaborator.Roles.ADMIN),
                V(ProjectQueryset.RoleOrigins.PROJECTOWNER),
            ),
            # Organization memberships - owner
            (
                Q(
                    pk__in=Organization.objects.filter(pk=project.owner).values(
                        "organization_owner"
                    )
                ),
                V(ProjectCollaborator.Roles.ADMIN),
                V(ProjectQueryset.RoleOrigins.ORGANIZATIONOWNER),
            ),
            # Organization memberships - admin
            (
                Q(
                    pk__in=OrganizationMember.objects.filter(
                        organization=project.owner,
                        role=OrganizationMember.Roles.ADMIN,
                    ).values("member")
                ),
                V(ProjectCollaborator.Roles.ADMIN),
                V(ProjectQueryset.RoleOrigins.ORGANIZATIONADMIN),
            ),
            # Role through ProjectCollaborator
            (
                Exists(
                    ProjectCollaborator.objects.validated()
                    .filter(
                        project=project,
                        collaborator=OuterRef("pk"),
                    )
                    .exclude(
                        collaborator__user_type=User.TYPE_TEAM,
                    )
                ),
                ProjectCollaborator.objects.filter(
                    project=project,
                    collaborator=OuterRef("pk"),
                )
                .exclude(
                    collaborator__user_type=User.TYPE_TEAM,
                )
                .values_list("role"),
                V(ProjectQueryset.RoleOrigins.COLLABORATOR),
            ),
            # Role through Team membership
            (
                Exists(
                    ProjectCollaborator.objects.filter(
                        project=project,
                        collaborator__team__members__member=OuterRef("pk"),
                    )
                ),
                ProjectCollaborator.objects.filter(
                    project=project,
                    collaborator__team__members__member=OuterRef("pk"),
                ).values_list("role"),
                V(ProjectQueryset.RoleOrigins.TEAMMEMBER),
            ),
        ]

        qs = User.objects.annotate(
            project_role=Case(
                *[When(perm[0], perm[1]) for perm in permissions_config],
                default=None,
                output_field=models.CharField(),
            ),
            project_role_origin=Case(
                *[When(perm[0], perm[2]) for perm in permissions_config],
                default=None,
            ),
        )
        # Exclude those without role (invisible)
        qs = qs.exclude(project_role__isnull=True)

        return qs

    def for_organization(self, organization: "Organization"):
        permissions_config = [
            # Direct ownership
            (
                Exists(
                    Organization.objects.filter(
                        pk=organization.pk,
                        organization_owner=OuterRef("pk"),
                    )
                ),
                V(OrganizationMember.Roles.ADMIN),
                V(OrganizationQueryset.RoleOrigins.ORGANIZATIONOWNER),
                V(True),
            ),
            # Organization membership
            (
                Exists(
                    OrganizationMember.objects.filter(
                        organization=organization,
                        member=OuterRef("pk"),
                    )
                ),
                OrganizationMember.objects.filter(
                    organization=organization,
                    member=OuterRef("pk"),
                ).values_list("role"),
                V(OrganizationQueryset.RoleOrigins.ORGANIZATIONMEMBER),
                OrganizationMember.objects.filter(
                    organization=organization,
                    member=OuterRef("pk"),
                ).values_list("is_public"),
            ),
        ]

        qs = self.annotate(
            membership_role=Case(
                *[When(perm[0], perm[1]) for perm in permissions_config],
                default=None,
                output_field=models.CharField(),
            ),
            membership_role_origin=Case(
                *[When(perm[0], perm[2]) for perm in permissions_config],
                default=None,
            ),
            membership_is_public=Case(
                *[When(perm[0], perm[3]) for perm in permissions_config],
                default=None,
            ),
        )

        qs = qs.filter(user_type=User.TYPE_USER)
        qs = qs.exclude(membership_role__isnull=True)

        return qs

    def for_team(self, team: "Team"):
        permissions_config = [
            # Direct ownership of the organization
            (
                Exists(
                    Team.objects.filter(
                        pk=team.pk,
                        team_organization__organization_owner=OuterRef("pk"),
                    )
                ),
                V(TeamMember.Roles.ADMIN),
            ),
            # Team membership
            (
                Exists(
                    TeamMember.objects.filter(
                        team=team,
                        member=OuterRef("pk"),
                    )
                ),
                V(TeamMember.Roles.MEMBER),
            ),
        ]

        qs = self.annotate(
            membership_role=Case(
                *[When(perm[0], perm[1]) for perm in permissions_config],
                default=None,
                output_field=models.CharField(),
            ),
        )
        qs = qs.exclude(membership_role__isnull=True)

        return qs

    def for_entity(self, entity: "User"):
        """Returns all users grouped in given entity (any type)

        Internally calls for_team or for_organization depending on the entity."""

        if entity.user_type == User.TYPE_USER:
            return self.filter(pk=entity.pk)

        if entity.user_type == User.TYPE_TEAM:
            return self.for_team(entity)

        if entity.user_type == User.TYPE_ORGANIZATION:
            return self.for_organization(entity)

        raise RuntimeError(f"Unsupported entity : {entity}")


class QFieldCloudUserManager(UserManager):
    def get_queryset(self):
        return UserQueryset(self.model, using=self._db)

    def for_project(self, project):
        return self.get_queryset().for_project(project)

    def for_organization(self, organization):
        return self.get_queryset().for_organization(organization)

    def for_team(self, entity):
        return self.get_queryset().for_team(entity)

    def for_entity(self, entity):
        return self.get_queryset().for_entity(entity)


# TODO change types to Enum
class User(AbstractUser):
    """User model. Used as base for organizations and teams too.

    Args:
        AbstractUser (AbstractUser): the django's abstract user base

    Returns:
        User: the user instance

    Note:
        If you add validators in the constructor, note they will be added multiple times for each class that extends User.
    """

    objects = QFieldCloudUserManager()

    TYPE_USER = 1
    TYPE_ORGANIZATION = 2
    TYPE_TEAM = 3

    TYPE_CHOICES = (
        (TYPE_USER, "user"),
        (TYPE_ORGANIZATION, "organization"),
        (TYPE_TEAM, "team"),
    )

    """Define username here, so we can avoid multiple validators from the constructor. Check the class notes."""
    username = models.CharField(
        _("username"),
        max_length=150,
        unique=True,
        help_text=_(
            "Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only."
        ),
        validators=[
            RegexValidator(
                r"^[-a-zA-Z0-9_]+$",
                "Only letters, numbers, underscores or hyphens are allowed.",
            ),
            RegexValidator(r"^[a-zA-Z].*$", _("The name must begin with a letter.")),
            RegexValidator(
                r"^.{3,}$", _("The name must be at least 3 characters long.")
            ),
            validators.reserved_words_validator,
        ],
        error_messages={
            "unique": _("A user with that username already exists."),
        },
    )

    user_type = models.PositiveSmallIntegerField(
        choices=TYPE_CHOICES, default=TYPE_USER, editable=False
    )

    remaining_invitations = models.PositiveIntegerField(
        default=1000000,
        help_text=_("Remaining invitations that can be sent by the user himself."),
    )

    has_newsletter_subscription = models.BooleanField(default=False)
    has_accepted_tos = models.BooleanField(default=False)

    def __str__(self):
        return self.username

    def get_absolute_url(self):
        if self.user_type == User.TYPE_TEAM:
            team = Team.objects.get(pk=self.pk)
            return reverse_lazy(
                "settings_teams_edit",
                kwargs={
                    "username": team.team_organization.username,
                    "teamname": team.teamname,
                },
            )
        else:
            return reverse_lazy("profile_overview", kwargs={"username": self.username})

    @property
    def is_user(self):
        return self.user_type == User.TYPE_USER

    @property
    def is_organization(self):
        return self.user_type == User.TYPE_ORGANIZATION

    @property
    def is_team(self):
        return self.user_type == User.TYPE_TEAM

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def username_with_full_name(self) -> str:
        full_name = self.full_name.strip()

        if full_name:
            return f"{self.username} ({full_name})"
        else:
            return self.username

    @property
    def has_geodb(self) -> bool:
        return hasattr(self, "geodb")

    def save(self, *args, **kwargs):
        created = self._state.adding
        super().save(*args, **kwargs)
        if created:
            UserAccount.objects.create(user=self)

    def delete(self, *args, **kwargs):
        if self.user_type != User.TYPE_TEAM:
            qfieldcloud.core.utils2.storage.remove_user_avatar(self)
        super().delete(*args, **kwargs)


class UserAccount(models.Model):

    NOTIFS_IMMEDIATELY = timedelta(minutes=0)
    NOTIFS_HOURLY = timedelta(hours=1)
    NOTIFS_DAILY = timedelta(days=1)
    NOTIFS_WEEKLY = timedelta(weeks=1)
    NOTIFS_DISABLED = None
    NOTIFS_CHOICES = (
        (NOTIFS_IMMEDIATELY, _("Immediately")),
        (NOTIFS_HOURLY, _("Hourly")),
        (NOTIFS_DAILY, _("Daily")),
        (NOTIFS_WEEKLY, _("Weekly")),
        (NOTIFS_DISABLED, _("Disabled")),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)

    account_type = models.ForeignKey(
        "subscription.AccountType",
        on_delete=models.PROTECT,
        default=AccountType.get_or_create_default,
    )

    # These will be moved one day to extrapackage. We don't touch for now (they are only used
    # in some tests)
    db_limit_mb = models.PositiveIntegerField(default=1000)
    is_geodb_enabled = models.BooleanField(
        default=False,
        help_text=_("Whether the account has the option to create a GeoDB."),
    )

    bio = models.CharField(max_length=255, default="", blank=True)
    company = models.CharField(max_length=255, default="", blank=True)
    location = models.CharField(max_length=255, default="", blank=True)
    twitter = models.CharField(max_length=255, default="", blank=True)
    is_email_public = models.BooleanField(default=False)
    avatar_uri = models.CharField(_("Profile Picture URI"), max_length=255, blank=True)
    timezone = TimeZoneField(default="Europe/Zurich", choices_display="WITH_GMT_OFFSET")

    notifs_frequency = models.DurationField(
        verbose_name=_("Email frequency for notifications"),
        choices=NOTIFS_CHOICES,
        default=NOTIFS_DISABLED,
        null=True,
        blank=True,
    )

    @property
    def avatar_url(self):
        if self.avatar_uri:
            return reverse_lazy(
                "public_files",
                kwargs={"filename": self.avatar_uri},
            )
        else:
            return None

    @property
    def storage_quota_left_mb(self) -> float:
        """Returns the storage quota left in MB (quota from account and extrapackages minus storage of all owned projects)"""

        base_quota = self.account_type.storage_mb

        extra_quota = (
            self.extra_packages.filter(
                Q(start_date__lte=datetime.now())
                & (Q(end_date__isnull=True) | Q(end_date__gte=datetime.now()))
            ).aggregate(sum_mb=Sum("type__extrapackagetypestorage__megabytes"))[
                "sum_mb"
            ]
            or 0
        )

        used_quota = (
            self.user.projects.aggregate(sum_mb=Sum("storage_size_mb"))["sum_mb"] or 0
        )

        return base_quota + extra_quota - used_quota

    def __str__(self):
        return f"Account {self.account_type}"


class Geodb(models.Model):
    def random_string():
        """Generate random sting starting with a lowercase letter and then
        lowercase letters and digits"""

        first_letter = secrets.choice(string.ascii_lowercase)
        letters_and_digits = string.ascii_lowercase + string.digits
        secure_str = first_letter + "".join(
            (secrets.choice(letters_and_digits) for i in range(15))
        )
        return secure_str

    def random_password():
        """Generate secure random password composed of
        letters, digits and special characters"""

        password_characters = (
            string.ascii_letters + string.digits + "!#$%&()*+,-.:;<=>?@[]_{}~"
        )
        secure_str = "".join((secrets.choice(password_characters) for i in range(16)))
        return secure_str

    def default_hostname():
        return os.environ.get("GEODB_HOST")

    def default_port():
        return os.environ.get("GEODB_PORT")

    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)

    username = models.CharField(blank=False, max_length=255, default=random_string)
    dbname = models.CharField(blank=False, max_length=255, default=random_string)
    hostname = models.CharField(blank=False, max_length=255, default=default_hostname)
    port = models.PositiveIntegerField(default=default_port)
    created_at = models.DateTimeField(auto_now_add=True)

    # The password is generated but not stored into the db
    password = ""
    last_geodb_error = None

    def __init__(self, *args, password="", **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.password = password

        if not self.password:
            self.password = Geodb.random_password()

    def size(self):
        try:
            return geodb_utils.get_db_size(self)
        except Exception as err:
            self.last_geodb_error = str(err)
            return None

    def __str__(self):
        return "{}'s db account, dbname: {}, username: {}".format(
            self.user.username, self.dbname, self.username
        )

    def save(self, *args, **kwargs):
        created = self._state.adding
        super().save(*args, **kwargs)
        # Automatically create a role and database when a Geodb object is created.
        if created:
            geodb_utils.create_role_and_db(self)

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        # Automatically delete role and database when a Geodb object is deleted.
        geodb_utils.delete_db_and_role(self.dbname, self.username)


class OrganizationQueryset(models.QuerySet):
    """Adds of_user(user) method to the organization's querysets, allowing to filter only organization related to that user.

    Organizations are annotated with the user's role (`membership_role`), the origin of this role (`membership_role_origin`)
    and whether it is public (`membership_is_public`).

    Args:
        user:               user to check membership for
    """

    class RoleOrigins(models.TextChoices):
        ORGANIZATIONOWNER = "organization_owner", _("Organization owner")
        ORGANIZATIONMEMBER = "organization_admin", _("Organization admin")

    def with_roles(self, user):
        permissions_config = [
            # Direct ownership
            (
                Q(organization_owner=user),
                V(OrganizationMember.Roles.ADMIN),
                V(OrganizationQueryset.RoleOrigins.ORGANIZATIONOWNER),
                V(True),
            ),
            # Organization membership
            (
                Exists(
                    OrganizationMember.objects.filter(
                        organization=OuterRef("pk"),
                        member=user,
                    )
                ),
                OrganizationMember.objects.filter(
                    organization=OuterRef("pk"),
                    member=user,
                ).values_list("role"),
                V(OrganizationQueryset.RoleOrigins.ORGANIZATIONMEMBER),
                OrganizationMember.objects.filter(
                    organization=OuterRef("pk"),
                    member=user,
                ).values_list("is_public"),
            ),
        ]

        qs = self.annotate(
            membership_role=Case(
                *[When(perm[0], perm[1]) for perm in permissions_config],
                default=None,
                output_field=models.CharField(),
            ),
            membership_role_origin=Case(
                *[When(perm[0], perm[2]) for perm in permissions_config],
                default=None,
            ),
            membership_is_public=Case(
                *[When(perm[0], perm[3]) for perm in permissions_config],
                default=None,
            ),
        )

        return qs

    def of_user(self, user):
        # Exclude those without role (invisible)
        qs = self.with_roles(user).exclude(membership_role__isnull=True)

        return qs


class OrganizationManager(UserManager):
    def get_queryset(self):
        return OrganizationQueryset(self.model, using=self._db)

    def of_user(self, user):
        return self.get_queryset().of_user(user)

    def with_roles(self, user):
        return self.get_queryset().with_roles(user)


class Organization(User):
    objects = OrganizationManager()

    organization_owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="owner",
        limit_choices_to=models.Q(user_type=User.TYPE_USER),
    )

    class Meta:
        verbose_name = "organization"
        verbose_name_plural = "organizations"

    def save(self, *args, **kwargs):
        self.user_type = self.TYPE_ORGANIZATION
        return super().save(*args, **kwargs)


class OrganizationMember(models.Model):
    class Roles(models.TextChoices):
        ADMIN = "admin", _("Admin")
        MEMBER = "member", _("Member")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "member"],
                name="organization_organization_member_uniq",
            )
        ]

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        limit_choices_to=models.Q(user_type=User.TYPE_ORGANIZATION),
        related_name="members",
    )
    member = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to=models.Q(user_type=User.TYPE_USER),
    )
    role = models.CharField(max_length=10, choices=Roles.choices, default=Roles.MEMBER)

    is_public = models.BooleanField(default=False)

    def __str__(self):
        return self.organization.username + ": " + self.member.username

    def clean(self) -> None:
        if self.organization.organization_owner == self.member:
            raise ValidationError(_("Cannot add the organization owner as a member."))

        return super().clean()


class Team(User):

    team_organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="teams",
    )

    class Meta:
        verbose_name = "team"
        verbose_name_plural = "teams"

    def save(self, *args, **kwargs):
        self.user_type = self.TYPE_TEAM
        return super().save(*args, **kwargs)

    @property
    def teamname(self):
        return self.username.replace(f"@{self.team_organization.username}/", "")


class TeamMember(models.Model):
    class Roles(models.TextChoices):
        ADMIN = "admin", _("Admin")
        MEMBER = "member", _("Member")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "member"],
                name="team_team_member_uniq",
            )
        ]

    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        limit_choices_to=models.Q(user_type=User.TYPE_TEAM),
        related_name="members",
    )
    member = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to=models.Q(user_type=User.TYPE_USER),
    )

    def clean(self) -> None:
        if (
            self.team.team_organization.members.filter(member=self.member).count() == 0
            and self.team.team_organization.organization_owner != self.member
        ):
            raise ValidationError(
                _("Cannot add team member that is not an organization member.")
            )

        return super().clean()

    def __str__(self):
        return self.team.username + ": " + self.member.username


class ProjectQueryset(models.QuerySet):
    """Adds for_user(user) method to the project's querysets, allowing to filter only projects visible to that user.

    Projects are annotated with the user's role (`user_role`) and the origin of this role (`user_role_origin`).

    Args:
        user:               user to check permission for

    Usage:
    ```
    # List Olivier's projects that are visible to Ivan (olivier/ivan are User instances)
    olivier.projects.for_user(ivan)
    ```

    Note:
    This query is very similar to `UserQueryset.for_project`, don't forget to update it too.
    """

    class RoleOrigins(models.TextChoices):
        PROJECTOWNER = "project_owner", _("Project owner")
        ORGANIZATIONOWNER = "organization_owner", _("Organization owner")
        ORGANIZATIONADMIN = "organization_admin", _("Organization admin")
        COLLABORATOR = "collaborator", _("Collaborator")
        TEAMMEMBER = "team_member", _("Team member")
        PUBLIC = "public", _("Public")

    def for_user(self, user):

        # orderd list of 3-uples : (condition, role, role origin)
        permissions_config = [
            # Direct ownership
            (
                Q(owner=user),
                V(ProjectCollaborator.Roles.ADMIN),
                V(ProjectQueryset.RoleOrigins.PROJECTOWNER),
            ),
            # Organization memberships - admin
            (
                Q(owner__in=Organization.objects.filter(organization_owner=user)),
                V(ProjectCollaborator.Roles.ADMIN),
                V(ProjectQueryset.RoleOrigins.ORGANIZATIONOWNER),
            ),
            (
                Q(
                    owner__in=OrganizationMember.objects.filter(
                        member=user, role=OrganizationMember.Roles.ADMIN
                    ).values("organization")
                ),
                V(ProjectCollaborator.Roles.ADMIN),
                V(ProjectQueryset.RoleOrigins.ORGANIZATIONADMIN),
            ),
            # Role through ProjectCollaborator
            (
                Exists(
                    ProjectCollaborator.objects.validated().filter(
                        project=OuterRef("pk"),
                        collaborator=user,
                    )
                ),
                ProjectCollaborator.objects.filter(
                    project=OuterRef("pk"),
                    collaborator=user,
                ).values_list("role"),
                V(ProjectQueryset.RoleOrigins.COLLABORATOR),
            ),
            # Role through Team membership
            (
                Exists(
                    ProjectCollaborator.objects.filter(
                        project=OuterRef("pk"),
                        collaborator__team__members__member=user,
                    )
                ),
                ProjectCollaborator.objects.filter(
                    project=OuterRef("pk"),
                    collaborator__team__members__member=user,
                ).values_list("role"),
                V(ProjectQueryset.RoleOrigins.TEAMMEMBER),
            ),
            # Public
            (
                Q(is_public=True),
                V(ProjectCollaborator.Roles.READER),
                V(ProjectQueryset.RoleOrigins.PUBLIC),
            ),
        ]

        qs = self.annotate(
            user_role=Case(
                *[When(perm[0], perm[1]) for perm in permissions_config],
                default=None,
                output_field=models.CharField(),
            ),
            user_role_origin=Case(
                *[When(perm[0], perm[2]) for perm in permissions_config],
                default=None,
            ),
        )
        # Exclude those without role (invisible)
        qs = qs.exclude(user_role__isnull=True)

        return qs


class Project(models.Model):
    """Represent a QFieldcloud project.
    It corresponds to a directory on the file system.

    The owner of a project is an Organization.
    """

    # NOTE the status is NOT stored in the db, because it might be refactored
    class Status(models.TextChoices):
        OK = "ok", _("Ok")
        BUSY = "busy", _("Busy")
        FAILED = "failed", _("Failed")

    objects = ProjectQueryset.as_manager()

    class Meta:
        ordering = ["owner__username", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="project_owner_name_uniq"
            )
        ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=255,
        validators=[
            RegexValidator(
                r"^[a-zA-Z0-9-_\.]+$",
                _("Only letters, numbers, underscores, hyphens and dots are allowed."),
            )
        ],
        help_text=_(
            _("Only letters, numbers, underscores, hyphens and dots are allowed.")
        ),
    )

    description = models.TextField(blank=True)
    project_filename = models.TextField(blank=True, null=True)
    project_details = models.JSONField(blank=True, null=True)
    is_public = models.BooleanField(
        default=False,
        help_text=_(
            "Projects that are marked as public would be visible and editable to anyone."
        ),
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="projects",
        limit_choices_to=models.Q(
            user_type__in=[User.TYPE_USER, User.TYPE_ORGANIZATION]
        ),
        help_text=_(
            "The project owner can be either you or any of the organization you are member of."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # These cache stats of the S3 storage. These can be out of sync, and should be
    # refreshed whenever retrieving/uploading files by passing `project.save(recompute_storage=True)`
    storage_size_mb = models.FloatField(default=0)

    # NOTE we can track only the file based layers, WFS, WMS, PostGIS etc are impossible to track
    data_last_updated_at = models.DateTimeField(blank=True, null=True)
    data_last_packaged_at = models.DateTimeField(blank=True, null=True)

    last_package_job = models.ForeignKey(
        "PackageJob",
        on_delete=models.SET_NULL,
        related_name="last_job_of",
        null=True,
        blank=True,
    )

    repackaging_cache_expire = models.DurationField(
        default=timedelta(minutes=60),
        validators=[MinValueValidator(timedelta(minutes=1))],
    )

    overwrite_conflicts = models.BooleanField(
        default=True,
        help_text=_(
            "If enabled, QFieldCloud will automatically overwrite conflicts in this project. Disabling this will force the project manager to manually resolve all the conflicts."
        ),
    )
    thumbnail_uri = models.CharField(
        _("Thumbnail Picture URI"), max_length=255, blank=True
    )

    @property
    def thumbnail_url(self):
        if self.thumbnail_uri:
            return reverse_lazy(
                "project_metafiles",
                kwargs={"projectid": self.id, "filename": self.thumbnail_uri[51:]},
            )
        else:
            return None

    def get_absolute_url(self):
        return reverse_lazy(
            "project_overview",
            kwargs={"username": self.owner.username, "project": self.name},
        )

    def __str__(self):
        return self.name + " (" + str(self.id) + ")" + " owner: " + self.owner.username

    @property
    def staticfile_dirs(self) -> List[str]:
        """Returns a list of configured staticfile dirs for the project.

        Staticfile dir is a special directory in the QField infrastructure that holds static files
        such as images, pdf etc. By default "DCIM" is considered a staticfile directory.

        TODO this function expects whether `staticfile_dirs` key in project_details. However,
        neither the extraction from the projectfile, nor the configuration in QFieldSync are implemented.

        Returns:
            List[str]: A list configured staticfile dirs for the project.
        """
        staticfile_dirs = []

        if self.project_details and self.project_details.get("staticfile_dirs"):
            staticfile_dirs = self.project_details.get("staticfile_dirs", [])

        if not staticfile_dirs:
            staticfile_dirs = ["DCIM"]

        return staticfile_dirs

    @property
    def private(self) -> bool:
        # still used in the project serializer
        return not self.is_public

    @cached_property
    def files(self) -> List[utils.S3ObjectWithVersions]:
        """Gets all the files from S3 storage. This is potentially slow. Results are cached on the instance."""
        return list(utils.get_project_files_with_versions(self.id))

    @property
    @deprecated("Use `len(project.files)` instead")
    def files_count(self):
        return len(self.files)

    @property
    def users(self):
        return User.objects.for_project(self)

    @property
    def has_online_vector_data(self) -> bool:
        # it's safer to assume there is an online vector layer
        if not self.project_details:
            return True

        layers_by_id = self.project_details.get("layers_by_id")

        # it's safer to assume there is an online vector layer
        if layers_by_id is None:
            return True

        has_online_vector_layers = False

        for layer_data in layers_by_id.values():
            if layer_data.get("type_name") == "VectorLayer" and not layer_data.get(
                "filename", ""
            ):
                has_online_vector_layers = True
                break

        return has_online_vector_layers

    @property
    def can_repackage(self) -> bool:
        return True

    @property
    def needs_repackaging(self) -> bool:
        if (
            not self.has_online_vector_data
            and self.data_last_updated_at
            and self.data_last_packaged_at
        ):
            # if all vector layers are file based and have been packaged after the last update, it is safe to say there are no modifications
            return self.data_last_packaged_at < self.data_last_updated_at
        else:
            # if the project has online vector layers (PostGIS/WFS/etc) we cannot be sure if there are modification or not, so better say there are
            return True

    @property
    def status(self) -> Status:
        # NOTE the status is NOT stored in the db, because it might be outdated
        if (
            Job.objects.filter(
                project=self, status__in=[Job.Status.QUEUED, Job.Status.STARTED]
            ).count()
            > 0
        ):
            return Project.Status.BUSY
        elif not self.project_filename:
            return Project.Status.FAILED
        else:
            return Project.Status.OK

    def delete(self, *args, **kwargs):
        if self.thumbnail_uri:
            qfieldcloud.core.utils2.storage.remove_project_thumbail(self)
        super().delete(*args, **kwargs)

    def save(self, recompute_storage=False, *args, **kwargs):
        if recompute_storage:
            self.storage_size_mb = utils.get_s3_project_size(self.id)
        super().save(*args, **kwargs)


class ProjectCollaboratorQueryset(models.QuerySet):
    def validated(self, keep_invalid=False):
        """Annotates the queryset with `is_valid` and by default filters out all invalid memberships.

        A membership to a private project not owned by an organization, or owned by a organization
        that the member is not part of is invalid.

        Args:
            keep_invalid:   if true, invalid rows are kept"""

        # Build the conditions with Q objects
        public = Q(project__is_public=True)
        owned_by_org = Q(project__owner__user_type=User.TYPE_ORGANIZATION)
        user_also_member_of_org = Q(
            project__owner__organization__members__member=F("collaborator")
        )

        # Assemble the condition
        condition = public | (owned_by_org & user_also_member_of_org)

        # Annotate the queryset
        qs = self.annotate(is_valid=Case(When(condition, then=True), default=False))

        # Filter out invalid
        if not keep_invalid:
            qs = qs.exclude(is_valid=False)

        return qs


class ProjectCollaborator(models.Model):
    class Roles(models.TextChoices):
        ADMIN = "admin", _("Admin")
        MANAGER = "manager", _("Manager")
        EDITOR = "editor", _("Editor")
        REPORTER = "reporter", _("Reporter")
        READER = "reader", _("Reader")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "collaborator"],
                name="projectcollaborator_project_collaborator_uniq",
            )
        ]

    objects = ProjectCollaboratorQueryset.as_manager()

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="collaborators",
    )
    collaborator = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to=models.Q(user_type=User.TYPE_USER),
    )
    role = models.CharField(max_length=10, choices=Roles.choices, default=Roles.READER)

    def __str__(self):
        return self.project.name + ": " + self.collaborator.username

    def clean(self) -> None:
        if self.project.owner == self.collaborator:
            raise ValidationError(_("Cannot add the project owner as a collaborator."))

        if self.project.owner.is_organization:
            organization = Organization.objects.get(pk=self.project.owner.pk)

            if organization.organization_owner == self.collaborator:
                raise ValidationError(
                    _(
                        "Cannot add the owner of the owning organization of the project as a collaborator."
                    )
                )
            elif OrganizationMember.objects.filter(
                organization=organization,
                member=self.collaborator,
                role=OrganizationMember.Roles.ADMIN,
            ).exists():
                raise ValidationError(
                    _(
                        "Cannot add an admin of the owning organization of the project as a collaborator."
                    )
                )

        return super().clean()


class Delta(models.Model):
    class Method(str, Enum):
        Create = "create"
        Delete = "delete"
        Patch = "patch"

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        STARTED = "started", _("Started")
        APPLIED = "applied", _("Applied")
        CONFLICT = "conflict", _("Conflict")
        NOT_APPLIED = "not_applied", _("Not_applied")
        ERROR = "error", _("Error")
        IGNORED = "ignored", _("Ignored")
        UNPERMITTED = "unpermitted", _("Unpermitted")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deltafile_id = models.UUIDField()
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="deltas",
    )
    content = JSONField()
    last_status = models.CharField(
        choices=Status.choices,
        default=Status.PENDING,
        max_length=32,
    )
    last_feedback = JSONField(null=True)
    last_modified_pk = models.TextField(null=True)
    last_apply_attempt_at = models.DateTimeField(null=True)
    last_apply_attempt_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="uploaded_deltas",
    )
    old_geom = models.GeometryField(null=True, srid=4326, dim=4)
    new_geom = models.GeometryField(null=True, srid=4326, dim=4)

    jobs_to_apply = models.ManyToManyField(
        to="ApplyJob",
        through="ApplyJobDelta",
    )

    def __str__(self):
        return str(self.id) + ", project: " + str(self.project.id)

    @staticmethod
    def get_status_summary(filters={}):
        rows = (
            Delta.objects.filter(**filters)
            .values("last_status")
            .annotate(count=Count("last_status"))
            .order_by()
        )

        rows_as_dict = {}
        for r in rows:
            rows_as_dict[r["last_status"]] = r["count"]

        counts = {}
        for status, _name in Delta.Status.choices:
            counts[status] = rows_as_dict.get(status, 0)

        return counts

    @property
    def short_id(self):
        return str(self.id)[0:8]

    @property
    def method(self):
        return self.content.get("method")


class Job(models.Model):

    objects = InheritanceManager()

    class Type(models.TextChoices):
        PACKAGE = "package", _("Package")
        DELTA_APPLY = "delta_apply", _("Delta Apply")
        PROCESS_PROJECTFILE = "process_projectfile", _("Process QGIS Project File")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        QUEUED = "queued", _("Queued")
        STARTED = "started", _("Started")
        FINISHED = "finished", _("Finished")
        STOPPED = "stopped", _("Stopped")
        FAILED = "failed", _("Failed")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="jobs",
    )
    type = models.CharField(max_length=32, choices=Type.choices)
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.PENDING
    )
    output = models.TextField(null=True)
    feedback = JSONField(null=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(blank=True, null=True, editable=False)
    finished_at = models.DateTimeField(blank=True, null=True, editable=False)

    @property
    def short_id(self) -> str:
        return str(self.id)[0:8]

    @property
    def fallback_output(self) -> str:
        # show whatever is the output if it is present
        if self.output:
            return ""

        if self.status == Job.Status.PENDING:
            return _(
                "The job is in pending status, it will be started as soon as there are available server resources."
            )
        elif self.status == Job.Status.QUEUED:
            return _(
                "The job is in queued status. Server resources are allocated and it will be started soon."
            )
        elif self.status == Job.Status.STARTED:
            return _("The job is in started status. Waiting for it to finish...")
        elif self.status == Job.Status.FINISHED:
            return _(
                "The job is in finished status. It finished successfully without any output."
            )
        elif self.status == Job.Status.STOPPED:
            return _("The job is in stopped status. Waiting to be continued...")
        elif self.status == Job.Status.FAILED:
            return _(
                "The job is in failed status. The execution failed due to server error. Please verify the project is configured properly and try again."
            )
        else:
            return _(
                "The job ended in unknown state. Please verify the project is configured properly, try again and contact QFieldCloud support for more information."
            )


class PackageJob(Job):
    def save(self, *args, **kwargs):
        self.type = self.Type.PACKAGE
        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Job: package"
        verbose_name_plural = "Jobs: package"


class ProcessProjectfileJob(Job):
    def save(self, *args, **kwargs):
        self.type = self.Type.PROCESS_PROJECTFILE
        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Job: process QGIS project file"
        verbose_name_plural = "Jobs: process QGIS project file"


class ApplyJob(Job):

    deltas_to_apply = models.ManyToManyField(
        to=Delta,
        through="ApplyJobDelta",
    )

    overwrite_conflicts = models.BooleanField(
        help_text=_(
            "If enabled, QFieldCloud will automatically overwrite conflicts while applying deltas."
        ),
    )

    def save(self, *args, **kwargs):
        self.type = self.Type.DELTA_APPLY
        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Job: apply"
        verbose_name_plural = "Jobs: apply"


class ApplyJobDelta(models.Model):
    apply_job = models.ForeignKey(ApplyJob, on_delete=models.CASCADE)
    delta = models.ForeignKey(Delta, on_delete=models.CASCADE)
    status = models.CharField(
        choices=Delta.Status.choices, default=Delta.Status.PENDING, max_length=32
    )
    feedback = JSONField(null=True)
    modified_pk = models.TextField(null=True)

    def __str__(self):
        return f"{self.apply_job_id}:{self.delta_id}"


class Secret(models.Model):
    class Type(models.TextChoices):
        PGSERVICE = "pgservice", _("pg_service")
        ENVVAR = "envvar", _("Environment Variable")

    name = models.TextField(
        max_length=255,
        validators=[
            RegexValidator(
                r"^[A-Z]+[A-Z0-9_]+$",
                _(
                    "Must start with a capital letter and followed by capital letters, numbers or underscores."
                ),
            )
        ],
        help_text=_(
            _(
                "Must start with a capital letter and followed by capital letters, numbers or underscores."
            ),
        ),
    )
    type = models.CharField(max_length=32, choices=Type.choices)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="secrets"
    )
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="project_secrets"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    value = django_cryptography.fields.encrypt(models.TextField())

    class Meta:
        ordering = ["project", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"], name="secret_project_name_uniq"
            )
        ]


auditlog.register(User, exclude_fields=["last_login", "updated_at"])
auditlog.register(UserAccount)
auditlog.register(Organization)
auditlog.register(OrganizationMember)
auditlog.register(Team)
auditlog.register(TeamMember)
auditlog.register(
    Project,
    include_fields=[
        "id",
        "name",
        "description",
        "owner",
        "is_public",
        "owner",
        "created_at",
    ],
)
auditlog.register(ProjectCollaborator)
auditlog.register(
    Delta,
    include_fields=[
        "id",
        "deltafile_id",
        "project",
        "content",
        "last_status",
        "created_by",
    ],
)
auditlog.register(Secret, exclude_fields=["value"])
