from io import StringIO
from concurrent.futures import ThreadPoolExecutor
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.test import TransactionTestCase
from researchloop.governance.models import InstallationRecord
from researchloop.identity.models import Membership, Workspace
from researchloop.interaction.errors import Problem

class OwnerBoundaryScenarios(TransactionTestCase):
    def test_given_two_concurrent_bootstraps_then_only_one_owner(self):
        def attempt(name):
            close_old_connections()
            try:
                call_command('bootstrap_owner', username=name, password_stdin=True, stdin=StringIO('test-only-race-strong-527431!'), stdout=StringIO())
                return 'created'
            except CommandError:
                return 'closed'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(attempt, ['first-owner', 'second-owner']))
        self.assertCountEqual(outcomes, ['created', 'closed'])
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(Membership.objects.count(), 1)
        self.assertEqual(Workspace.objects.count(), 1)

    def test_given_unknown_provenance_then_bootstrap_refuses_without_changes(self):
        InstallationRecord.objects.create(pk=1, mode='unknown', synthetic_only=False)
        with self.assertRaises(CommandError):
            call_command('bootstrap_owner', username='new-owner', password_stdin=True, stdin=StringIO('test-only-strong-random-5824!'), stdout=StringIO())
        self.assertFalse(get_user_model().objects.exists())
        self.assertEqual(InstallationRecord.objects.get().mode, 'unknown')

    def test_given_portable_runtime_then_erasure_authority_unavailable(self):
        from researchloop.governance.authority import read
        with self.assertRaises(Problem) as raised:
            read(1, initialize=True)
        self.assertEqual(raised.exception.code, 'GOVERNANCE_UNAVAILABLE')
