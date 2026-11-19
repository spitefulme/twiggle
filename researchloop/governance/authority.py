"""Rollback-independent synthetic authority. Caller holds the workspace gate."""

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import tempfile
from uuid import UUID

from django.conf import settings

from researchloop.interaction.errors import Problem


MAX_BYTES = 8 * 1024 * 1024


def canonical(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def seal(value):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        b"u5-authority:" + canonical(value),
        hashlib.sha256,
    ).hexdigest()


def configured():
    return _configured_path() is not None


def _configured_path():
    return getattr(settings, "U5_AUTHORITY_ROOT", None) or os.environ.get(
        "U5_AUTHORITY_ROOT"
    )


class Unavailable(Problem):
    def __init__(self):
        super().__init__("GOVERNANCE_UNAVAILABLE", 503)


def _root():
    if not getattr(settings, "U1_ALLOW_SYNTHETIC", False) or not configured():
        raise Unavailable()
    root = Path(_configured_path())
    info = root.lstat()
    if (
        not root.is_absolute()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise Unavailable()
    return root


@dataclass(frozen=True)
class Journal:
    epoch: int
    entries: tuple
    head: str
    manifest: str = ""
    restore_state: str = ""
    completion: str = ""


def read(workspace_id, *, initialize=False):
    try:
        root = _root()
        path = root / f"{int(workspace_id)}.json"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if initialize:
                return Journal(0, (), "")
            raise Unavailable() from None
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > MAX_BYTES
            ):
                raise Unavailable()
            frame = json.loads(source.read(MAX_BYTES + 1))
        value, signature = frame["value"], frame["signature"]
        if value["workspace"] != workspace_id or not hmac.compare_digest(
            seal(value), signature
        ):
            raise Unavailable()
        if type(value["epoch"]) is not int or value["epoch"] < 0:
            raise Unavailable()
        entries = value["entries"]
        if not isinstance(entries, list):
            raise Unavailable()
        for index, item in enumerate(entries, 1):
            if item["sequence"] != index or item["kind"] not in {"study", "expiry"}:
                raise Unavailable()
            if item["actor"] != "retention" and not (
                isinstance(item["actor"], str)
                and item["actor"].isascii()
                and item["actor"].isdigit()
            ):
                raise Unavailable()
            for field in ("id", "subject", "study"):
                UUID(item[field])
            if (
                not isinstance(item["sessions"], list)
                or not isinstance(item["versions"], dict)
                or sorted(item["versions"]) != item["sessions"]
            ):
                raise Unavailable()
            for subject, version in item["versions"].items():
                UUID(subject)
                UUID(version)
        manifest = value.get("manifest", "")
        restore_state = value.get("restoreState", "")
        completion = value.get("completion", "")
        if not _valid_restore_frame(
            value["epoch"], manifest, restore_state, completion
        ):
            raise Unavailable()
        return Journal(
            value["epoch"],
            tuple(entries),
            signature,
            manifest,
            restore_state,
            completion,
        )
    except (KeyError, ValueError, OSError, TypeError, AttributeError) as error:
        raise Unavailable() from error


def _valid_restore_frame(epoch, manifest, restore_state, completion):
    fields = (manifest, restore_state, completion)
    if not all(isinstance(item, str) for item in fields):
        return False
    if not epoch:
        return not any(fields)
    return len(manifest) == 64 and (
        (restore_state == "pending" and not completion)
        or (restore_state == "completed" and len(completion) == 64)
    )


def journal_value(
    workspace_id, epoch, entries, manifest="", *, restore_state="", completion=""
):
    value = {"workspace": workspace_id, "epoch": epoch, "entries": list(entries)}
    if manifest:
        value["manifest"] = manifest
    if restore_state:
        value["restoreState"] = restore_state
    if completion:
        value["completion"] = completion
    return value


def publish(
    workspace_id, epoch, entries, manifest="", *, restore_state="", completion=""
):
    if not _valid_restore_frame(epoch, manifest, restore_state, completion):
        raise Unavailable()
    value = journal_value(
        workspace_id,
        epoch,
        entries,
        manifest,
        restore_state=restore_state,
        completion=completion,
    )
    signature = seal(value)
    data = canonical({"value": value, "signature": signature})
    if len(data) > MAX_BYTES:
        raise Unavailable()
    path = None
    try:
        root = _root()
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as sink:
            path = Path(sink.name)
            sink.write(data)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(path, root / f"{int(workspace_id)}.json")
        path = None
        directory = os.open(root, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return Journal(
            epoch, tuple(entries), signature, manifest, restore_state, completion
        )
    except OSError as error:
        raise Unavailable() from error
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def assert_current(ledger):
    if not configured():
        if ledger.u5_head or ledger.u5_epoch or ledger.u5_quarantined:
            raise Unavailable()
        return
    journal = read(ledger.workspace_id)
    if (
        journal.restore_state == "pending"
        or ledger.u5_quarantined
        or (ledger.u5_head, ledger.u5_epoch)
        != (
            journal.head,
            journal.epoch,
        )
    ):
        raise Unavailable()
