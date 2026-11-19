from django.db import connection

from researchloop.evidence_delivery.models import Receipt
from researchloop.evidence_delivery.selection import fingerprint
from researchloop.interaction.errors import Problem


def check(actor_id, scope, key, value):
    intent = fingerprint(value)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"evidence-delivery:{actor_id}:{scope}:{key}"],
        )
    receipt = Receipt.objects.filter(actor_id=actor_id, scope=scope, key=key).first()
    if receipt and receipt.fingerprint != intent:
        raise Problem("IDEMPOTENCY_CONFLICT", 409)
    return receipt, intent


def remember(actor_id, scope, key, intent, obj, acknowledgement):
    Receipt.objects.create(
        actor_id=actor_id,
        scope=scope,
        key=key,
        fingerprint=intent,
        object_id=obj.pk,
        acknowledgement=acknowledgement,
    )
