from io import StringIO
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from researchloop.governance.models import InstallationRecord
from researchloop.governance.readiness import assert_fresh_local
from researchloop.identity.models import Membership
from researchloop.interaction.errors import Problem

class BootstrapScenarios(TransactionTestCase):
    def test_given_fresh_db_when_owner_created_then_one_non_superuser(self):
        self.assertIn('bootstrap_owner', get_commands(), 'first-owner bootstrap is missing')
        call_command('bootstrap_owner', username='operator', password_stdin=True, stdin=StringIO('test-only-strong-random-5824!'), stdout=StringIO())
        owner = get_user_model().objects.get()
        self.assertFalse(owner.is_superuser)
        self.assertFalse(owner.is_staff)
        self.assertEqual(Membership.objects.get().role, 'workspace-admin')
        self.assertEqual(assert_fresh_local()['mode'], 'portable-core')
        with self.assertRaises(CommandError):
            call_command('bootstrap_owner', username='another', password_stdin=True, stdin=StringIO('test-only-strong-random-5824!'), stdout=StringIO())
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_given_synthetic_provenance_when_portable_readiness_then_fail_closed(self):
        InstallationRecord.objects.create(id=1, mode='fresh-local', synthetic_only=True)
        with self.assertRaises(Problem):
            assert_fresh_local()

    def test_given_weak_password_when_bootstrap_then_no_account(self):
        with self.assertRaises(CommandError):
            call_command('bootstrap_owner', username='operator', password_stdin=True, stdin=StringIO('short'), stdout=StringIO())
        self.assertFalse(get_user_model().objects.exists())
