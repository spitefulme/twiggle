"""Immutable allowlisted static files and the Gunicorn Django application."""
import hashlib
import mimetypes
import os
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'researchloop.config.portable_settings')
from django.core.wsgi import get_wsgi_application
app = get_wsgi_application()
root = Path(__file__).resolve().parents[1] / 'static'
assets = {}
for path in root.rglob('*'):
    if path.is_file() and not path.is_symlink():
        data = path.read_bytes()
        assets['/static/' + path.relative_to(root).as_posix()] = (data, mimetypes.guess_type(path.name)[0] or 'application/octet-stream', '"' + hashlib.sha256(data).hexdigest() + '"')

def application(environ, start_response):
    path = environ.get('PATH_INFO', '')
    if path.startswith('/static/'):
        asset = assets.get(path)
        if not asset or environ.get('REQUEST_METHOD') not in {'GET', 'HEAD'}:
            start_response('404 Not Found', [('Content-Length', '0')])
            return [b'']
        data, kind, etag = asset
        headers = [('Content-Type', kind), ('Content-Length', str(len(data))), ('ETag', etag), ('Cache-Control', 'no-cache'), ('X-Content-Type-Options', 'nosniff')]
        start_response('200 OK', headers)
        return [b'' if environ['REQUEST_METHOD'] == 'HEAD' else data]
    return app(environ, start_response)
