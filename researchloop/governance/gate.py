"""Workspace delivery admission; shared readers never bypass an exclusive waiter."""

from contextlib import contextmanager

import psycopg
from django.db import connection

from researchloop.interaction.errors import Problem


@contextmanager
def delivery_gate(workspace_id, exclusive=False, timeout_ms=3000, *, download=False):
    with _gate(17101, workspace_id, exclusive, timeout_ms, download=download):
        yield


@contextmanager
def maintenance_gate(workspace_id, *, exclusive):
    """Keep owner cleanup off the delivery lock and out of a new restore."""
    with _gate(17106, workspace_id, exclusive, 500):
        yield


@contextmanager
def _gate(namespace, workspace_id, exclusive, timeout_ms, *, download=False):
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 3000:
        raise ValueError("Only bounded equal-or-stricter delivery timeouts are allowed")
    parameters = connection.get_connection_params()
    parameters.pop("cursor_factory", None)
    parameters.pop("context", None)
    lock_connection = psycopg.connect(**parameters, autocommit=True)
    lock = "pg_advisory_lock" if exclusive else "pg_advisory_lock_shared"
    try:
        lock_connection.execute(
            "SELECT set_config('lock_timeout', %s, false)", [str(timeout_ms) + "ms"]
        )
        lock_connection.execute(
            "SELECT set_config('statement_timeout', %s, false)",
            [str(5000 if timeout_ms == 3000 else timeout_ms + 250) + "ms"],
        )
        lock_connection.execute(f"SELECT {lock}(%s,%s)", [namespace, int(workspace_id)])
        if (
            download
            and not lock_connection.execute(
                "SELECT pg_try_advisory_lock(%s, %s)", [17103, int(workspace_id)]
            ).fetchone()[0]
        ):
            raise Problem("DOWNLOAD_BUSY", 503)
        yield
    except psycopg.Error as error:
        raise Problem("GOVERNANCE_UNAVAILABLE", 503) from error
    finally:
        lock_connection.close()
