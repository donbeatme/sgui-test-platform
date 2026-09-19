from django.urls import path
from .views import WorkbenchView

urlpatterns = [
    path('', WorkbenchView.as_view()),
    path('<str:operation>/', WorkbenchView.as_view()),
    path('jobs/<uuid:job_id>/', WorkbenchView.as_view()),
    path('jobs/<uuid:job_id>/<str:operation>/', WorkbenchView.as_view()),
]
