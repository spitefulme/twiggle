from contextlib import contextmanager

from researchloop.governance.gate import delivery_gate
from researchloop.interaction.errors import Problem


@contextmanager
def scope(workspace_id, handoff=None):
    """Move the same acquired gate into the HTTP closing scope, never reacquire."""
    if handoff is None:
        with delivery_gate(workspace_id):
            yield
    else:
        handoff.enter_context(delivery_gate(workspace_id))
        yield


def protect(response, handoff):
    if len(response.content) > 2097152:
        raise Problem("EVIDENCE_UNAVAILABLE", 503)
    response._resource_closers.append(handoff.pop_all().close)
    return response
