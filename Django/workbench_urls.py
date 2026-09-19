from django.urls import include, path
from wharttest_django.urls import urlpatterns as upstream

urlpatterns = [path('api/projects/<int:project_pk>/workbench/', include('guicase_workbench.urls')), *upstream]
