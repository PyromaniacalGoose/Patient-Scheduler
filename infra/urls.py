"""
URL configuration for mysite project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from django.views.generic import RedirectView

from infra import views

urlpatterns = [
    path("", RedirectView.as_view(url="calendar/", permanent=False)),
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path('admin/', admin.site.urls),
    path("calendar/", views.calendar_page, name="calendar_page"),
    path("calendar/events/", views.calendar_events, name="calendar_events"),
    path("calendar/availability/", views.calendar_availability, name="calendar_availability"),
    path("calendar/register_patient/", views.register_patient, name="register_patient"),
    path("calendar/patient/<int:patient_id>/", views.patient_detail, name="patient_detail"),
]
