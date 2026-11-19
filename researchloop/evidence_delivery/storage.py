import fcntl
import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path

from django.db import connection

from researchloop.interaction.errors import Problem


LIMIT = 256 * 1024 * 1024


def directory():
    value = os.environ.get("U4_PRIVATE_ARTIFACTS")
    if not value:
        raise Problem("EXPORT_STORAGE_UNAVAILABLE", 503)
    root = Path(value)
    info = root.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise Problem("EXPORT_STORAGE_UNAVAILABLE", 503)
    return root


@contextmanager
def render_slot():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(17105, 1)")
        owned = cursor.fetchone()[0]
    try:
        yield owned
    finally:
        if owned:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(17105, 1)")


@contextmanager
def root_writer_lock():
    """Physical writer ownership persists through database disconnection.

    The directory contains one stable lock inode, never unlinked by sweep,
    purge, promotion or retry. Closing the held file descriptor releases it.
    """
    root = directory()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        descriptor = os.open(
            ".u4-writer.lock",
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=root_fd,
        )
    finally:
        os.close(root_fd)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
        ):
            raise Problem("EXPORT_STORAGE_UNAVAILABLE", 503)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(descriptor)


def open_stage(job_id, lease_id):
    root = directory()
    path = root / (str(job_id) + "." + str(lease_id) + ".part")
    available = LIMIT - sum(
        item.stat(follow_symlinks=False).st_size
        for item in root.iterdir()
        if item.is_file() and not item.is_symlink()
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return path, os.fdopen(fd, "wb"), available


def write_open(file, available, chunks, deadline):
    size, digest = 0, hashlib.sha256()
    for block in chunks:
        deadline()
        size += len(block)
        if size > available:
            raise Problem("EXPORT_STORAGE_LIMIT", 503)
        file.write(block)
        digest.update(block)
    file.flush()
    os.fsync(file.fileno())
    return size, digest.hexdigest()


def remove_job_paths(job):
    if job.lease_id is None:
        return
    root = directory()
    for extension in ("part", "ready"):
        (root / f"{job.pk}.{job.lease_id}.{extension}").unlink(missing_ok=True)


def finalized(path, job_id, lease_id):
    target = path.parent / (str(job_id) + "." + str(lease_id) + ".ready")
    path.rename(target)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return target


def open_artifact(job):
    path = directory() / (str(job.pk) + "." + str(job.lease_id) + ".ready")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size != job.artifact_size
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        os.close(fd)
        raise Problem("EXPORT_STORAGE_UNAVAILABLE", 503)
    return os.fdopen(fd, "rb")
