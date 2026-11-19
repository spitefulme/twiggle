"""Local interactive first-owner creation; no public registration endpoint."""
import getpass
import sys
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from researchloop.identity.models import Workspace, Membership
from researchloop.governance.models import InstallationRecord, Ledger

class Command(BaseCommand):
    stealth_options = ('stdin',)
    help = 'Create the first Twiggle owner locally, once. Password is never an argument.'
    def add_arguments(self, parser):
        parser.add_argument('--username')
        parser.add_argument('--password-stdin', action='store_true', help='Automation only: read a password from private stdin')

    def handle(self, *args, **options):
        if not getattr(settings, 'TWIGGLE_PORTABLE', False):
            raise CommandError('Requires portable settings')
        username = options['username'] or input('Owner username: ')
        if not username or username != username.strip() or len(username) > 150:
            raise CommandError('Invalid username')
        if options['password_stdin']:
            password = options.get('stdin', sys.stdin).read(1025).rstrip('\r\n')
        else:
            password = getpass.getpass('Owner password (14+ characters): ')
            if password != getpass.getpass('Repeat password: '):
                raise CommandError('Passwords differ')
        user = get_user_model()(username=username)
        try:
            user.full_clean(exclude=['password'])
            validate_password(password, user=user)
            if len(password) > 1024:
                raise ValidationError('Password too long')
        except ValidationError as error:
            raise CommandError('; '.join(error.messages)) from None
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(17109, 1)')
            if get_user_model().objects.exists() or Workspace.objects.exists() or InstallationRecord.objects.exists():
                raise CommandError('Installation is not empty; bootstrap is permanently closed')
            user.set_password(password)
            user.save()
            workspace = Workspace.objects.create(name='Twiggle')
            Membership.objects.create(user=user, workspace=workspace, role='workspace-admin')
            Ledger.objects.create(workspace=workspace)
            InstallationRecord.objects.create(pk=1, mode='portable-core', synthetic_only=False)
        self.stdout.write('First owner created. Sign in at your configured origin.')
