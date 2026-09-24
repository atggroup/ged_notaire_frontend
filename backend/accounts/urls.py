from django.urls import path
from .totp_views import TotpConfirmView, TotpDisableView, TotpSetupView, TotpStatusView
from .views import (ChangePasswordView, CheckInviteView, ForgotResetView, ForgotSendCodeView, ForgotVerifyCodeView, GoogleOAuthCompleteView, GoogleOAuthConsumeView, GoogleOAuthStartView, LoginView, LogoutView, MeView, MFAVerifyView, OAuthStubView, RegisterCompleteView, RegisterStartView, SendRegisterCodeView, UserCreateView, VerifyRegisterCodeView)

urlpatterns = [
    path("auth/login", LoginView.as_view()), path("auth/me", MeView.as_view()), path("me", MeView.as_view()), path("me/password", ChangePasswordView.as_view()),
    path("auth/mfa/verify", MFAVerifyView.as_view()),
    path("auth/totp", TotpStatusView.as_view()), path("auth/totp/setup", TotpSetupView.as_view()),
    path("auth/totp/confirm", TotpConfirmView.as_view()), path("auth/totp/disable", TotpDisableView.as_view()),
    path("auth/logout", LogoutView.as_view()),
    path("auth/register/start", RegisterStartView.as_view()),
    path("auth/register/check-invite", CheckInviteView.as_view()),
    path("auth/register/send-code", SendRegisterCodeView.as_view()), path("auth/register/verify-code", VerifyRegisterCodeView.as_view()), path("auth/register/complete", RegisterCompleteView.as_view()),
    path("auth/forgot-password/send-code", ForgotSendCodeView.as_view()), path("auth/forgot-password/verify-code", ForgotVerifyCodeView.as_view()), path("auth/forgot-password/reset", ForgotResetView.as_view()),
    # Google Sign-In: the callback that Google itself redirects to lives
    # outside /api/ (see ged_backend/urls.py) to match the exact redirect
    # URI registered in Google Cloud Console.
    path("auth/oauth/google/start", GoogleOAuthStartView.as_view()),
    path("auth/oauth/google/complete", GoogleOAuthCompleteView.as_view()),
    path("auth/oauth/google/consume", GoogleOAuthConsumeView.as_view()),
    path("auth/oauth/<str:provider>", OAuthStubView.as_view()),
    path("users", UserCreateView.as_view()),
]
