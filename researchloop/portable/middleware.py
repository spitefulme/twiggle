from django.conf import settings
from django.http import HttpResponseForbidden

class ExactOrigin:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path not in {'/health/live', '/health/ready'}:
            from urllib.parse import urlsplit
            if request.get_host() != urlsplit(settings.ORIGIN).netloc:
                return HttpResponseForbidden('Host does not match configured origin')
            if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.headers.get('Origin') not in {None, settings.ORIGIN}:
                return HttpResponseForbidden('Origin does not match configured origin')
        return self.get_response(request)
