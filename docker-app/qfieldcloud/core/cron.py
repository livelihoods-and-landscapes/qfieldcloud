import logging

from django.core.management import call_command
from django_cron import CronJobBase, Schedule
from invitations.utils import get_invitation_model

from .invitations_utils import send_invitation

logger = logging.getLogger(__name__)


class DeleteExpiredInvitationsJob(CronJobBase):
    schedule = Schedule(run_every_mins=60)
    code = "qfieldcloud.delete_expired_confirmations"

    def do(self):
        Invitation = get_invitation_model()
        Invitation.objects.delete_expired_confirmations()


class ResendFailedInvitationsJob(CronJobBase):
    schedule = Schedule(run_every_mins=60)
    code = "qfieldcloud.resend_failed_invitations"

    def do(self):
        Invitation = get_invitation_model()

        invitation_emails = []
        for invitation in Invitation.objects.filter(sent__isnull=True):
            try:
                send_invitation(invitation)
                invitation_emails.append(invitation.email)
            except Exception as err:
                logger.error(err)

        logger.info(
            f'Resend {len(invitation_emails)} previously failed invitation(s) to: {", ".join(invitation_emails)}'
        )


class PurgeOldFileVersions(CronJobBase):
    """Purges old version of files of all projects using the purge_old_file_versions
    management command.
    """

    schedule = Schedule(runs_every_mins=60 * 24 * 7)
    code = "qfieldcloud.purge_old_file_versions"

    def do(self):
        call_command("purge_old_file_versions", "--force")
