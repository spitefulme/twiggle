from django.conf import settings
from researchloop.governance.models import InstallationRecord, Ledger
from researchloop.interaction.errors import Problem


def assert_fresh_local():
    metadata = InstallationRecord.objects.filter(pk=1).first()
    if getattr(settings, 'TWIGGLE_PORTABLE', False):
        if (getattr(settings, 'U1_ALLOW_SYNTHETIC', False) or metadata is None or metadata.mode != 'portable-core' or metadata.synthetic_only):
            raise Problem('RECOVERY_READINESS_UNAVAILABLE', 503)
        if Ledger.objects.filter(available=False).exists():
            raise Problem('GOVERNANCE_UNAVAILABLE', 503)
        return {'mode': 'portable-core', 'syntheticOnly': False, 'operationalRecovery': 'unsupported', 'operationalErasure': 'unavailable'}
    if (
        not getattr(settings, "U1_ALLOW_SYNTHETIC", False)
        or metadata is None
        or metadata.mode != "fresh-local"
        or not metadata.synthetic_only
    ):
        raise Problem("RECOVERY_READINESS_UNAVAILABLE", 503)
    if Ledger.objects.filter(available=False).exists():
        raise Problem("GOVERNANCE_UNAVAILABLE", 503)
    return {
        "mode": "fresh-local",
        "syntheticOnly": True,
        "operationalRecovery": "not-qualified-for-operational-recovery",
    }
