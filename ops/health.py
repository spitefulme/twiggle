import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2) as response:
    assert response.status == 200
