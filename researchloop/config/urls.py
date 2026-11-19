from django.urls import path, re_path
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from researchloop.interaction import views
from researchloop.interaction import governance_views

urlpatterns = [path("", views.shell), re_path(r"^api/v1/(?P<path>.*?)/?$", views.api)]
urlpatterns += [
    path("governance/", governance_views.shell),
    path("recruit/<str:capability>", views.participant_shell),
    path("participant/<str:capability>", views.participant_shell),
]
urlpatterns += staticfiles_urlpatterns()
urlpatterns += [
    path("health/live", views.health),
    path("health/ready", views.health, {"readiness": True}),
]
