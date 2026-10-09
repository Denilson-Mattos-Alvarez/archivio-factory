from django.contrib.auth import views as auth_views
from django.urls import include, path
from .auth_views import AccessoArchivio

urlpatterns = [
    path('accesso/', AccessoArchivio.as_view(), name='login'),
    path('esci/', auth_views.LogoutView.as_view(), name='logout'),
    path('api/v1/', include('archivio.urls')),
    path('', include('archivio.web_urls')),
]
