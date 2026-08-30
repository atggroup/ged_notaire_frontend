window.GED_AUTH_CONFIG = {
  // Passez useMock à false et renseignez apiBase pour brancher le vrai backend.
  apiBase: "/api",
  useMock: true,

  endpoints: {
    login: "/auth/login",
    me: "/auth/me",

    // Inscription multistep — le rôle envoyé n'est JAMAIS source de vérité :
    // le backend doit revalider le code d'invitation et déterminer le rôle réel.
    registerCheckInvite: "/auth/register/check-invite",   // POST { role, inviteCode } -> { valid, organizationName }
    registerStart: "/auth/register/start",                 // POST { role, inviteCode, firstName, lastName, email, phone, jobTitle }
    registerSendCode: "/auth/register/send-code",           // POST { email } -> { expiresInSeconds }
    registerVerifyCode: "/auth/register/verify-code",       // POST { email, code } -> { ok }
    registerComplete: "/auth/register/complete",            // POST { email, password } -> { token, role, name, email }

    // Mot de passe oublié
    forgotSendCode: "/auth/forgot-password/send-code",      // POST { email } -> { expiresInSeconds }
    forgotVerifyCode: "/auth/forgot-password/verify-code",  // POST { email, code } -> { resetToken }
    forgotResetPassword: "/auth/forgot-password/reset",     // POST { resetToken, password } -> { ok }

    // OAuth — le frontend ne fait que déclencher le flux, le backend gère
    // l'échange de code / vérification d'identité et la création de session.
    oauthGoogle: "/auth/oauth/google",                      // -> redirection serveur ou popup OAuth
    oauthApple: "/auth/oauth/apple"
  },

  // Correspondance rôle -> dossier applicatif
  roleRoutes: {
    admin: "1-notaire-admin/index.html",
    clerc: "2-clerc-principal/index.html",
    collaborateur: "3-collaborateur/index.html"
  },
  roleLabels: {
    admin: "Notaire · Admin",
    clerc: "Clerc principal",
    collaborateur: "Collaborateur"
  },
  sessionKey: "ged_session",

  // Politique de code d'invitation (validation stricte côté SERVEUR obligatoire —
  // ce réglage ne sert qu'à afficher/masquer le champ côté front en mode démo).
  inviteRequired: {
    admin: true,
    clerc: true,
    collaborateur: true
  },

  otp: {
    length: 6,
    resendCooldownSeconds: 45,
    maxAttempts: 5
  }
};
