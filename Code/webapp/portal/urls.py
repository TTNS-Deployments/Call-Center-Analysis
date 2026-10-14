from django.urls import path

from . import api, views

app_name = "portal"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("healthz", views.healthz, name="healthz"),
    path("calls/", views.calls, name="calls"),
    path("calls/<str:call_id>/", views.call_detail, name="call_detail"),
    path("calls/<str:call_id>/audio", views.call_audio, name="call_audio"),
    path("calls/<str:call_id>/retry", views.call_retry, name="call_retry"),
    path("agents/", views.agents, name="agents"),
    path("agents/<str:agent_id>/", views.agent_detail, name="agent_detail"),
    path("insights/", views.insights, name="insights"),
    path("insights/powerbi-token", views.powerbi_token, name="powerbi_token"),
    path("pipeline/", views.pipeline_view, name="pipeline"),
    path("pipeline/upload", views.upload, name="upload"),
    path("settings/", views.settings_view, name="settings"),
    path("api/calls", api.calls, name="api_calls"),
    path("api/calls/<str:call_id>", api.call_detail, name="api_call_detail"),
]
