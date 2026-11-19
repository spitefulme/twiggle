import os
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
from researchloop.interaction.delivery import BoundedDelivery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "researchloop.config.settings")
application = get_wsgi_application()
if os.environ.get("U1_LOCAL_SYNTHETIC") == "1":
    application = StaticFilesHandler(application)
application = BoundedDelivery(application)
