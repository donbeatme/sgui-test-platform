from django.urls import path
from .views import ScriptView, WorkerView

urlpatterns = [
    path('projects/<int:project_pk>/playwright-scripts/', ScriptView.as_view()),
    path('projects/<int:project_pk>/playwright-scripts/<int:script_id>/', ScriptView.as_view()),
    path('projects/<int:project_pk>/playwright-scripts/<int:script_id>/<str:operation>/', ScriptView.as_view()),
    path('playwright-worker/<str:operation>/', WorkerView.as_view()),
]
