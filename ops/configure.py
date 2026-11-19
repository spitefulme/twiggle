"""Create an exclusive private installation configuration; never rotate on rerun."""
import os
import pathlib
import re
import secrets
import stat
import sys
from urllib.parse import urlsplit


def main():
    target, origin, port, project = sys.argv[1:]
    if not re.fullmatch(r'https?://[a-z0-9.-]+(?::[0-9]{1,5})?', origin):
        raise SystemExit('Origin must be one canonical ASCII origin without whitespace or interpolation')
    parsed = urlsplit(origin)
    if parsed.port in {0, 80 if parsed.scheme == 'http' else 443} or (parsed.port is not None and parsed.port > 65535):
        raise SystemExit('Use a valid non-default port, or omit the default port')
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or origin.endswith('/') or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,50}', project) or not port.isdigit() or not 1024 <= int(port) <= 65535):
        raise SystemExit('Invalid exact origin, port or project name')
    if parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost'}:
        raise SystemExit('Non-loopback origins require HTTPS')
    path = pathlib.Path(target)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise SystemExit('Configuration must be a private regular file')
        return
    data = f'COMPOSE_PROJECT_NAME={project}\nTWIGGLE_ORIGIN={origin}\nTWIGGLE_PORT={port}\nPOSTGRES_ADMIN_PASSWORD={secrets.token_hex(32)}\nPOSTGRES_PASSWORD={secrets.token_hex(32)}\nTWIGGLE_SECRET_KEY={secrets.token_hex(48)}\n'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())

if __name__ == '__main__':
    main()
