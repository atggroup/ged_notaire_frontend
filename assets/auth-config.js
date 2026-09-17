window.GED_AUTH_CONFIG = {
  // Passez useMock à false et renseignez apiBase pour brancher le vrai backend.
  apiBase: "/api",
  useMock: false,

  endpoints: {
    login: "/auth/login",
    mfaVerify: "/auth/mfa/verify",
    me: "/auth/me",

    // Activation multistep d'une invitation nominative. Le rôle est toujours
    // contrôlé côté serveur à partir du code d'invitation.
    checkInvite: "/auth/register/check-invite",             // POST { email, role, inviteCode } -> { valid, role }
    registerStart: "/auth/register/start",                 // POST { role, inviteCode, firstName, lastName, email, phone, jobTitle }
    registerSendCode: "/auth/register/send-code",           // POST { email } -> { expiresInSeconds }
    registerVerifyCode: "/auth/register/verify-code",       // POST { email, code } -> { ok }
    registerComplete: "/auth/register/complete",            // POST { email, password } -> { token, role, name, email }

    // Mot de passe oublié
    forgotSendCode: "/auth/forgot-password/send-code",      // POST { email } -> { expiresInSeconds }
    forgotVerifyCode: "/auth/forgot-password/verify-code",  // POST { email, code } -> { resetToken }
    forgotResetPassword: "/auth/forgot-password/reset",     // POST { resetToken, password } -> { ok }

    // OAuth — le frontend ne fait que déclencher le flux (redirection pleine
    // page), le backend gère l'échange de code / vérification d'identité et
    // la création de session. Seul Google est proposé (gratuit, et c'est le
    // compte que tout le monde a déjà) : pas d'Apple (payant côté
    // développeur), pas de GitHub (inconnu des études notariales).
    oauthGoogleStart: "/auth/oauth/google/start",         // navigation (pas fetch) -> redirige vers Google
    oauthGoogleComplete: "/auth/oauth/google/complete",   // POST { googleTicket, role } -> { token, role, name, email }
    oauthGoogleConsume: "/auth/oauth/google/consume"      // POST { ticket } -> { token, role, name, email }
  },

  // Correspondance rôle -> dossier applicatif
  roleRoutes: {
    admin: "notaire-admin/index.html",
    clerc: "clerc-principal/index.html",
    collaborateur: "collaborateur/index.html"
  },
  roleLabels: {
    admin: "Notaire · Admin",
    clerc: "Clerc principal",
    collaborateur: "Collaborateur"
  },
  sessionKey: "ged_session",

  otp: {
    length: 6,
    resendCooldownSeconds: 45,
    maxAttempts: 5
  }
};
