from rest_framework.routers import DefaultRouter
from .views import *
from django.urls import path, include

router = DefaultRouter()
router.register("datasets", DataSetViewSet)
router.register("files", FileModelViewSet)

urlpatterns = [
    path('api/datasets/v1/', include(router.urls)),
    path("user/me/", UserMeView.as_view()),
    path("user/login/", UserLoginView.as_view()),
    path("user/logout/", UserLogoutView.as_view()),
    path("user/forgot-password/", ForgotPasswordView.as_view()),
    path("reset-password/<str:token>/", ResetPasswordView.as_view()),
    path("user/update-photo/", UserUpdatePhotoView.as_view()),
    path("api/v1/upload_file/", UploadTempFile.as_view()),
    path("api/v1/model_status/", GetTempFileStatus.as_view())
]