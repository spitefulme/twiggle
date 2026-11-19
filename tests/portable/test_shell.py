from django.test import SimpleTestCase
from django.conf import settings
from urllib.parse import urlsplit

class PortableShellScenarios(SimpleTestCase):
    def test_given_portable_installation_when_opened_then_no_synthetic_claim(self):
        response = self.client.get('/', HTTP_HOST=urlsplit(settings.ORIGIN).netloc)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'synthetic workspace', response.content)
        self.assertIn(b'Twiggle', response.content)

    def test_given_wrong_exact_host_then_rejected(self):
        self.assertEqual(self.client.get('/', HTTP_HOST='127.0.0.1:9999').status_code, 403)
