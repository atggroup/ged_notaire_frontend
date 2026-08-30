(function () {
  "use strict";
  var cfg = window.GED_AUTH_CONFIG || {};
  var EP = cfg.endpoints || {};
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  /* =========================================================
     Utilitaires génériques (toast, session, appel API mocké)
     ========================================================= */
  var toastHost = document.createElement("div");
  toastHost.className = "toast-host";
  document.body.appendChild(toastHost);
  function toast(message, kind) {
    var el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = message;
    toastHost.appendChild(el);
    setTimeout(function () { el.remove(); }, 3800);
  }

  function saveSession(data) {
    try { localStorage.setItem(cfg.sessionKey || "ged_session", JSON.stringify(data)); } catch (e) {}
  }

  function deriveName(email) {
    var local = String(email || "").split("@")[0] || "Utilisateur";
    return local.replace(/[._-]+/g, " ").split(" ").filter(Boolean)
      .map(function (w) { return w.charAt(0).toUpperCase() + w.slice(1); }).join(" ");
  }

  function deriveMockRole(email) {
    var e = String(email || "").toLowerCase();
    if (e.indexOf("admin") !== -1 || e.indexOf("notaire") !== -1) return "admin";
    if (e.indexOf("clerc") !== -1) return "clerc";
    return "collaborateur";
  }

  var MOCK_LATENCY = 650;

  // Appel API générique. En mode démo (useMock:true), simule le réseau et
  // les réponses. Basculez cfg.useMock=false + cfg.apiBase pour brancher
  // le vrai backend : le contrat de chaque endpoint est décrit dans
  // assets/auth-config.js et le README.
  function api(path, body) {
    if (cfg.useMock) {
      return new Promise(function (resolve, reject) {
        setTimeout(function () {
          try { resolve(mockHandler(path, body || {})); }
          catch (err) { reject(err); }
        }, MOCK_LATENCY);
      });
    }
    return fetch((cfg.apiBase || "") + path, {
      method: "POST",
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (j) {
          throw new Error(j.message || "Erreur serveur (" + res.status + ").");
        });
      }
      return res.json();
    });
  }

  function mockHandler(path, body) {
    switch (path) {
      case EP.login:
        if (!body.email || !body.password) throw new Error("Identifiants invalides.");
        if (body.password.length < 6) throw new Error("Identifiants invalides.");
        var role = deriveMockRole(body.email);
        return { token: "mock-" + btoa(body.email).slice(0, 16) + "-" + Date.now(), role: role, name: deriveName(body.email), email: body.email };

      case EP.registerCheckInvite:
        if ((cfg.inviteRequired || {})[body.role] && (!body.inviteCode || body.inviteCode.trim().length < 4)) {
          throw new Error("Code d'invitation invalide ou incomplet.");
        }
        return { valid: true, organizationName: "Étude notariale — Cabinet Abidjan" };

      case EP.registerStart:
        return { ok: true, pendingEmail: body.email };

      case EP.registerSendCode:
        return { ok: true, expiresInSeconds: (cfg.otp || {}).resendCooldownSeconds || 45, devCode: "123456" };

      case EP.registerVerifyCode:
        if (body.code === "000000") throw new Error("Code invalide. Vérifiez et réessayez.");
        return { ok: true };

      case EP.registerComplete:
        return { token: "mock-" + Date.now(), role: body.role || "collaborateur", name: body.name || deriveName(body.email), email: body.email };

      case EP.forgotSendCode:
        if (!EMAIL_RE.test(body.email || "")) throw new Error("Adresse e-mail invalide.");
        return { ok: true, expiresInSeconds: (cfg.otp || {}).resendCooldownSeconds || 45 };

      case EP.forgotVerifyCode:
        if (body.code === "000000") throw new Error("Code invalide. Vérifiez et réessayez.");
        return { resetToken: "mock-reset-" + Date.now() };

      case EP.forgotResetPassword:
        return { ok: true };

      default:
        return { ok: true };
    }
  }

  function setLoading(btn, on) {
    if (!btn) return;
    btn.classList.toggle("loading", on);
    btn.disabled = on;
  }
  function setError(fieldEl, msg) {
    if (!fieldEl) return;
    fieldEl.classList.add("invalid");
    var em = fieldEl.querySelector(".err-msg");
    if (em && msg) em.textContent = msg;
  }
  function clearError(fieldEl) { if (fieldEl) fieldEl.classList.remove("invalid"); }

  /* =========================================================
     Skeleton loading
     ========================================================= */
  var skeleton = document.getElementById("authSkeleton");
  window.addEventListener("load", function () {
    setTimeout(function () {
      if (skeleton) skeleton.classList.add("hidden");
    }, 550);
  });

  /* =========================================================
     Visibilité mot de passe
     ========================================================= */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".toggle-eye");
    if (!btn) return;
    var input = document.getElementById(btn.getAttribute("data-target"));
    if (!input) return;
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.setAttribute("aria-label", show ? "Masquer le mot de passe" : "Afficher le mot de passe");
  });

  /* =========================================================
     Machine à états
     ========================================================= */
  var stage = document.getElementById("authStage");
  var illuPanel = stage ? stage.querySelector(".illu-panel") : null;
  var formPanel = stage ? stage.querySelector(".form-panel") : null;
  var illuImg = document.getElementById("illuImage");
  var illuBadge = document.getElementById("illuBadge");
  var illuTitle = document.getElementById("illuTitle");
  var illuSub = document.getElementById("illuSub");
  var liveRegion = document.getElementById("authLive");
  var root = document.documentElement;
  var switchChip = document.getElementById("switchChip");
  var switchChipText = document.getElementById("switchChipText");
  var switchChipBtn = document.getElementById("switchChipBtn");

  // Illustrations réelles du projet (assets/img) — plus de photos externes.
  var IMG = {
    login: "assets/img/ILLUSTRATION_CONNEXION.png",
    register: "assets/img/ILLUSTRATION_INSCRIPTION.png",
    forgot: "assets/img/ILLUSTRATION_MOT_DE_PASSE_OUBLIE.png"
  };
  var IMG_FALLBACK = {
    login: "assets/img/ILLUSTRATION_CONNEXION.svg",
    register: "assets/img/ILLUSTRATION_INSCRIPTION.svg",
    forgot: "assets/img/ILLUSTRATION_MOT_DE_PASSE_OUBLIE.svg"
  };

  var CHIP_LOGIN = { text: "Pas encore de compte ?", label: "S'inscrire", go: "reg-role" };
  var CHIP_REGISTER = { text: "Déjà un compte ?", label: "Se connecter", go: "login" };
  var CHIP_FORGOT = { text: "Vous vous souvenez ?", label: "Se connecter", go: "login" };

  var META = {
    "login": {
      illu: "login", badge: false, chip: CHIP_LOGIN, layout: "left",
      title: "Un espace sécurisé pour chaque rôle de votre étude.",
      sub: "Archivage, indexation, permissions et traçabilité — chaque profil accède uniquement à ce qui relève de son périmètre.",
      pageTitle: "Connexion — GED Notaire"
    },
    "reg-role": {
      illu: "register", badge: false, chip: CHIP_REGISTER, layout: "right",
      title: "Rejoignez l'espace numérique de votre étude.",
      sub: "Clerc ou Collaborateur — le compte Notaire · Administrateur existe déjà et n'est pas créé par ce formulaire.",
      progress: { group: "register", index: 1, total: 4 }, pageTitle: "Créer un compte — GED Notaire"
    },
    "reg-personal": {
      illu: "register", badge: false, chip: CHIP_REGISTER, layout: "right",
      title: "Parlons un peu de vous.",
      sub: "Ces informations permettent à votre étude de vous identifier et de vous attribuer les bons accès.",
      progress: { group: "register", index: 2, total: 4 }, pageTitle: "Vos informations — GED Notaire"
    },
    "reg-password": {
      illu: "register", badge: false, chip: CHIP_REGISTER, layout: "right",
      title: "Protégez votre compte.",
      sub: "Un mot de passe robuste est la première ligne de défense de vos dossiers.",
      progress: { group: "register", index: 3, total: 4 }, pageTitle: "Sécurité du compte — GED Notaire"
    },
    "reg-verify": {
      illu: "register", badge: false, chip: CHIP_REGISTER, layout: "right",
      title: "Plus qu'une étape.",
      sub: "Confirmez votre adresse e-mail pour activer votre compte en toute sécurité.",
      progress: { group: "register", index: 4, total: 4 }, pageTitle: "Vérification — GED Notaire"
    },
    "reg-success": {
      illu: "register", badge: true, chip: null, layout: "right",
      title: "Compte créé avec succès.",
      sub: "Votre espace est prêt. Vous pouvez désormais accéder à votre tableau de bord.",
      pageTitle: "Bienvenue — GED Notaire"
    },
    "forgot-email": {
      illu: "forgot", badge: false, chip: CHIP_FORGOT, layout: "left",
      title: "Récupérez l'accès à votre compte.",
      sub: "Indiquez votre adresse e-mail professionnelle pour recevoir un code de vérification.",
      progress: { group: "forgot", index: 1, total: 3 }, pageTitle: "Mot de passe oublié — GED Notaire"
    },
    "forgot-verify": {
      illu: "forgot", badge: false, chip: CHIP_FORGOT, layout: "left",
      title: "Vérifiez votre boîte mail.",
      sub: "Un code à 6 chiffres vient de vous être envoyé.",
      progress: { group: "forgot", index: 2, total: 3 }, pageTitle: "Vérification — GED Notaire"
    },
    "forgot-password": {
      illu: "forgot", badge: false, chip: CHIP_FORGOT, layout: "left",
      title: "Choisissez un nouveau mot de passe.",
      sub: "Optez pour un mot de passe que vous n'avez encore jamais utilisé.",
      progress: { group: "forgot", index: 3, total: 3 }, pageTitle: "Nouveau mot de passe — GED Notaire"
    },
    "forgot-success": {
      illu: "forgot", badge: true, chip: null, layout: "left",
      title: "Mot de passe modifié.",
      sub: "Vous pouvez désormais vous reconnecter avec votre nouveau mot de passe.",
      pageTitle: "Mot de passe modifié — GED Notaire"
    }
  };

  var current = null;
  var currentLayout = null;
  var selectedRole = null; // 'admin' | 'clerc' | 'collaborateur'
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- Permutation animée illustration <-> formulaire (FLIP) ----------
     Les deux panneaux échangent réellement leur position à l'écran : on
     mesure leur emplacement avant/après le changement d'ordre CSS (flex
     "order"), puis on anime la différence avec transform (translateX sur
     desktop, translateY quand ça passe en colonne sur mobile) — glissement
     fluide dans les deux sens, jamais de saut brutal. */
  function swapLayout(nextLayout) {
    if (!stage || !illuPanel || !formPanel || nextLayout === currentLayout) {
      currentLayout = nextLayout;
      return;
    }
    if (reduceMotion) {
      stage.classList.toggle("layout-swap", nextLayout === "right");
      currentLayout = nextLayout;
      return;
    }

    var illuFirst = illuPanel.getBoundingClientRect();
    var formFirst = formPanel.getBoundingClientRect();

    stage.classList.toggle("layout-swap", nextLayout === "right");

    var illuLast = illuPanel.getBoundingClientRect();
    var formLast = formPanel.getBoundingClientRect();

    var illuDX = illuFirst.left - illuLast.left, illuDY = illuFirst.top - illuLast.top;
    var formDX = formFirst.left - formLast.left, formDY = formFirst.top - formLast.top;

    [illuPanel, formPanel].forEach(function (el) { el.classList.add("is-flipping"); el.style.transition = "none"; });
    illuPanel.style.transform = "translate(" + illuDX + "px," + illuDY + "px)";
    formPanel.style.transform = "translate(" + formDX + "px," + formDY + "px)";

    // force reflow avant de relâcher vers la position finale
    illuPanel.getBoundingClientRect();

    requestAnimationFrame(function () {
      [illuPanel, formPanel].forEach(function (el) {
        el.style.transition = "transform .62s cubic-bezier(.16,1,.3,1)";
        el.style.transform = "";
      });
      var cleanup = function () {
        [illuPanel, formPanel].forEach(function (el) {
          el.style.transition = "";
          el.classList.remove("is-flipping");
        });
        illuPanel.removeEventListener("transitionend", cleanup);
      };
      illuPanel.addEventListener("transitionend", cleanup);
    });

    currentLayout = nextLayout;
  }

  /* ---------- Transition d'accès : joue la vidéo avant de rediriger ---------- */
  var introOverlay = document.getElementById("introTransition");
  var introVideo = document.getElementById("introVideo");

  function playIntroTransition(dest) {
    if (!introOverlay || !introVideo || reduceMotion) {
      window.location.href = dest;
      return;
    }
    var navigated = false;
    function go() {
      if (navigated) return;
      navigated = true;
      window.location.href = dest;
    }
    introOverlay.classList.add("show");
    introOverlay.setAttribute("aria-hidden", "false");
    try { introVideo.currentTime = 0; } catch (err) {}
    introVideo.addEventListener("ended", go, { once: true });
    // Filet de sécurité : si la lecture est bloquée par le navigateur ou
    // que la vidéo ne se termine jamais, on n'attend pas indéfiniment.
    var fallback = setTimeout(go, 7000);
    introVideo.addEventListener("ended", function () { clearTimeout(fallback); }, { once: true });
    var playPromise = introVideo.play();
    if (playPromise && typeof playPromise.catch === "function") playPromise.catch(go);
  }

  function goTo(step, opts) {
    opts = opts || {};
    var meta = META[step];
    if (!meta) return;
    var prevGroupIllu = current ? META[current].illu : null;

    swapLayout(meta.layout || "left");

    document.querySelectorAll(".step").forEach(function (s) { s.classList.remove("active"); });
    var next = document.querySelector('.step[data-step="' + step + '"]');
    if (next) next.classList.add("active");

    if (switchChip) {
      if (meta.chip) {
        switchChip.style.display = "";
        if (switchChipText) switchChipText.textContent = meta.chip.text;
        if (switchChipBtn) {
          switchChipBtn.textContent = meta.chip.label;
          switchChipBtn.setAttribute("data-go", meta.chip.go);
        }
      } else {
        switchChip.style.display = "none";
      }
    }

    // Illustration : fade uniquement si elle change réellement
    if (illuImg) {
      var src = IMG[meta.illu];
      if (meta.illu !== prevGroupIllu || !illuImg.classList.contains("show")) {
        illuImg.classList.remove("show");
        setTimeout(function () {
          illuImg.setAttribute("data-fallback", IMG_FALLBACK[meta.illu]);
          illuImg.setAttribute("src", src);
          illuImg.classList.add("show");
        }, prevGroupIllu ? 160 : 20);
      }
    }
    if (illuBadge) illuBadge.classList.toggle("show", !!meta.badge);
    if (illuTitle) illuTitle.textContent = meta.title;
    if (illuSub) illuSub.textContent = meta.sub;
    document.title = meta.pageTitle || "GED Notaire";

    updateProgress(meta.progress);

    if (!opts.keepRole && step !== "reg-role" && step.indexOf("reg-") !== 0) {
      root.removeAttribute("data-role-active");
    }

    if (liveRegion) liveRegion.textContent = meta.title;
    current = step;

    if (!reduceMotion && next) {
      var first = next.querySelector("input:not([type=hidden]), button.role-card, .otp-box");
      if (first) setTimeout(function () { first.focus({ preventScroll: true }); }, 260);
    }
  }

  function updateProgress(p) {
    document.querySelectorAll("[data-progress-for]").forEach(function (wrap) {
      var group = wrap.getAttribute("data-progress-for");
      var show = p && p.group === group;
      wrap.style.display = show ? "block" : "none";
      if (show) {
        var fill = wrap.querySelector(".progress-fill");
        var label = wrap.querySelector(".progress-count");
        var pct = Math.round((p.index / p.total) * 100);
        if (fill) fill.style.width = pct + "%";
        if (label) label.innerHTML = "Étape <b>" + p.index + "</b> sur " + p.total;
      }
    });
  }

  // Navigation déclarative : data-go="step" sur n'importe quel élément
  document.addEventListener("click", function (e) {
    var trigger = e.target.closest("[data-go]");
    if (!trigger) return;
    e.preventDefault();
    goTo(trigger.getAttribute("data-go"), { keepRole: true });
  });

  var initialParams = new URLSearchParams(window.location.search);
  var initialGo = initialParams.get("go");
  goTo(initialGo && META[initialGo] ? initialGo : "login");

  /* =========================================================
     Composant OTP réutilisable
     ========================================================= */
  function setupOtp(rootEl) {
    var boxes = Array.prototype.slice.call(rootEl.querySelectorAll(".otp-box"));
    boxes.forEach(function (box, i) {
      box.addEventListener("input", function () {
        box.value = box.value.replace(/[^0-9]/g, "").slice(0, 1);
        box.classList.toggle("filled", !!box.value);
        box.classList.remove("err");
        if (box.value && boxes[i + 1]) boxes[i + 1].focus();
        maybeComplete();
      });
      box.addEventListener("keydown", function (e) {
        if (e.key === "Backspace" && !box.value && boxes[i - 1]) {
          boxes[i - 1].focus();
        }
      });
      box.addEventListener("paste", function (e) {
        e.preventDefault();
        var text = (e.clipboardData || window.clipboardData).getData("text").replace(/[^0-9]/g, "");
        if (!text) return;
        text.split("").slice(0, boxes.length).forEach(function (ch, idx) {
          if (boxes[idx]) { boxes[idx].value = ch; boxes[idx].classList.add("filled"); }
        });
        var last = Math.min(text.length, boxes.length) - 1;
        if (boxes[last]) boxes[last].focus();
        maybeComplete();
      });
    });
    function maybeComplete() {
      var code = boxes.map(function (b) { return b.value; }).join("");
      rootEl.dispatchEvent(new CustomEvent("otp:change", { detail: { code: code, complete: code.length === boxes.length } }));
    }
    return {
      value: function () { return boxes.map(function (b) { return b.value; }).join(""); },
      reset: function () { boxes.forEach(function (b) { b.value = ""; b.classList.remove("filled", "err"); }); boxes[0].focus(); },
      markError: function () { boxes.forEach(function (b) { b.classList.add("err"); }); },
      focusFirst: function () { boxes[0].focus(); }
    };
  }

  function startResendTimer(btn, timerEl, seconds) {
    var remaining = seconds;
    btn.disabled = true;
    tick();
    var id = setInterval(function () {
      remaining -= 1;
      tick();
      if (remaining <= 0) { clearInterval(id); btn.disabled = false; timerEl.textContent = ""; }
    }, 1000);
    function tick() {
      var m = String(Math.floor(remaining / 60)).padStart(2, "0");
      var s = String(remaining % 60).padStart(2, "0");
      if (remaining > 0) timerEl.textContent = "Renvoyer le code dans " + m + ":" + s;
    }
  }

  /* =========================================================
     Force du mot de passe
     ========================================================= */
  function wirePasswordStrength(input, strengthEl, rulesEl) {
    input.addEventListener("input", function () {
      var v = input.value;
      var rules = {
        len: v.length >= 8,
        upper: /[A-Z]/.test(v),
        lower: /[a-z]/.test(v),
        digit: /[0-9]/.test(v),
        special: /[^A-Za-z0-9]/.test(v)
      };
      var score = Object.keys(rules).filter(function (k) { return rules[k]; }).length;
      var level = v.length === 0 ? 0 : score <= 2 ? 1 : score === 3 ? 2 : score === 4 ? 3 : 4;
      strengthEl.setAttribute("data-level", String(level));
      var labelEl = strengthEl.querySelector(".pw-strength-label");
      if (labelEl) labelEl.textContent = ["", "Faible", "Moyen", "Bon", "Fort"][level] || "";
      rulesEl.querySelectorAll("[data-rule]").forEach(function (li) {
        li.classList.toggle("ok", !!rules[li.getAttribute("data-rule")]);
      });
    });
  }

  /* =========================================================
     Sélection du rôle (inscription)
     ========================================================= */
  var roleCardsWrap = document.getElementById("regRoleCards");
  var inviteField = document.getElementById("f-invite");
  var inviteInput = document.getElementById("inviteCode");
  var regRoleContinue = document.getElementById("regRoleContinue");

  if (roleCardsWrap) {
    roleCardsWrap.querySelectorAll(".role-card").forEach(function (card) {
      card.addEventListener("click", function () {
        roleCardsWrap.querySelectorAll(".role-card").forEach(function (c) {
          c.classList.remove("on"); c.setAttribute("aria-checked", "false");
        });
        card.classList.add("on");
        card.setAttribute("aria-checked", "true");
        selectedRole = card.getAttribute("data-role");
        root.setAttribute("data-role-active", selectedRole);
        var inviteRequired = (cfg.inviteRequired || {})[selectedRole];
        if (inviteField) {
          inviteField.style.display = "block";
          var lbl = inviteField.querySelector("label");
          if (lbl) lbl.innerHTML = (selectedRole === "admin" ? "Code d'invitation administrateur" : "Code d'invitation de l'étude") + (inviteRequired ? '<span class="req">*</span>' : "");
        }
        if (regRoleContinue) regRoleContinue.disabled = false;
      });
      card.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); card.click(); }
      });
    });
  }

  if (regRoleContinue) {
    regRoleContinue.addEventListener("click", function () {
      if (!selectedRole) { toast("Sélectionnez le type de compte à créer.", "err"); return; }
      clearError(inviteField);
      var code = inviteInput ? inviteInput.value.trim() : "";
      if ((cfg.inviteRequired || {})[selectedRole] && code.length < 4) {
        setError(inviteField, "Renseignez un code d'invitation valide (fourni par votre étude).");
        return;
      }
      setLoading(regRoleContinue, true);
      api(EP.registerCheckInvite, { role: selectedRole, inviteCode: code }).then(function () {
        setLoading(regRoleContinue, false);
        goTo("reg-personal", { keepRole: true });
      }).catch(function (err) {
        setLoading(regRoleContinue, false);
        setError(inviteField, err.message);
      });
    });
  }

  /* =========================================================
     Étape 2 — informations personnelles
     ========================================================= */
  var personalForm = document.getElementById("regPersonalForm");
  var regState = { firstName: "", lastName: "", email: "", phone: "", jobTitle: "", password: "" };

  if (personalForm) {
    personalForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var fFirst = document.getElementById("f-firstName");
      var fLast = document.getElementById("f-lastName");
      var fEmail = document.getElementById("f-regEmail");
      var fPhone = document.getElementById("f-phone");
      [fFirst, fLast, fEmail, fPhone].forEach(clearError);

      var firstName = document.getElementById("firstName").value.trim();
      var lastName = document.getElementById("lastName").value.trim();
      var email = document.getElementById("regEmail").value.trim();
      var phone = document.getElementById("phone").value.trim();
      var jobTitle = document.getElementById("jobTitle").value.trim();
      var ok = true;
      if (firstName.length < 2) { setError(fFirst, "Prénom requis."); ok = false; }
      if (lastName.length < 2) { setError(fLast, "Nom requis."); ok = false; }
      if (!EMAIL_RE.test(email)) { setError(fEmail, "Adresse e-mail invalide."); ok = false; }
      if (phone.length < 8) { setError(fPhone, "Numéro de téléphone invalide."); ok = false; }
      if (!ok) return;

      regState.firstName = firstName; regState.lastName = lastName;
      regState.email = email; regState.phone = phone; regState.jobTitle = jobTitle;
      goTo("reg-password", { keepRole: true });
    });
  }

  /* =========================================================
     Étape 3 — mot de passe
     ========================================================= */
  var passwordForm = document.getElementById("regPasswordForm");
  var regPwInput = document.getElementById("regPassword");
  var regPw2Input = document.getElementById("regPassword2");
  if (regPwInput) wirePasswordStrength(regPwInput, document.getElementById("regPwStrength"), document.getElementById("regPwRules"));

  if (passwordForm) {
    passwordForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var fPw = document.getElementById("f-regPassword");
      var fPw2 = document.getElementById("f-regPassword2");
      clearError(fPw); clearError(fPw2);
      var v = regPwInput.value;
      var strongEnough = v.length >= 8 && /[A-Z]/.test(v) && /[a-z]/.test(v) && /[0-9]/.test(v);
      var ok = true;
      if (!strongEnough) { setError(fPw, "Respectez au minimum les règles ci-dessus."); ok = false; }
      if (regPw2Input.value !== v || !v) { setError(fPw2, "Les mots de passe ne correspondent pas."); ok = false; }
      if (!ok) return;

      regState.password = v;
      var btn = document.getElementById("regPasswordSubmit");
      setLoading(btn, true);
      api(EP.registerStart, {
        role: selectedRole, firstName: regState.firstName, lastName: regState.lastName,
        email: regState.email, phone: regState.phone, jobTitle: regState.jobTitle
      }).then(function () {
        return api(EP.registerSendCode, { email: regState.email });
      }).then(function () {
        setLoading(btn, false);
        goTo("reg-verify", { keepRole: true });
        prepRegisterOtp();
      }).catch(function (err) {
        setLoading(btn, false);
        toast(err.message || "Une erreur est survenue.", "err");
      });
    });
  }

  /* =========================================================
     Étape 4 — vérification (inscription)
     ========================================================= */
  var regOtpRoot = document.getElementById("regOtp");
  var regOtpCtrl = regOtpRoot ? setupOtp(regOtpRoot) : null;
  var regOtpSubmit = document.getElementById("regOtpSubmit");
  var regOtpResend = document.getElementById("regOtpResend");
  var regOtpTimer = document.getElementById("regOtpTimer");
  var regOtpEmailLabel = document.getElementById("regOtpEmailLabel");
  var regOtpAttempts = 0;

  function prepRegisterOtp() {
    if (regOtpEmailLabel) regOtpEmailLabel.textContent = regState.email;
    if (regOtpCtrl) regOtpCtrl.reset();
    regOtpAttempts = 0;
    startResendTimer(regOtpResend, regOtpTimer, (cfg.otp || {}).resendCooldownSeconds || 45);
  }

  if (regOtpRoot) {
    regOtpRoot.addEventListener("otp:change", function (e) {
      if (regOtpSubmit) regOtpSubmit.disabled = !e.detail.complete;
    });
  }
  if (regOtpResend) {
    regOtpResend.addEventListener("click", function () {
      setLoading(regOtpResend, false);
      api(EP.registerSendCode, { email: regState.email }).then(function () {
        toast("Un nouveau code a été envoyé.", "ok");
        startResendTimer(regOtpResend, regOtpTimer, (cfg.otp || {}).resendCooldownSeconds || 45);
      });
    });
  }
  if (regOtpSubmit) {
    regOtpSubmit.addEventListener("click", function () {
      var code = regOtpCtrl.value();
      if (code.length < 6) return;
      setLoading(regOtpSubmit, true);
      api(EP.registerVerifyCode, { email: regState.email, code: code }).then(function () {
        return api(EP.registerComplete, {
          role: selectedRole, email: regState.email,
          name: regState.firstName + " " + regState.lastName, password: regState.password
        });
      }).then(function (res) {
        saveSession({ token: res.token, role: res.role, name: res.name, email: res.email, ts: Date.now() });
        setLoading(regOtpSubmit, false);
        goTo("reg-success", { keepRole: true });
      }).catch(function (err) {
        setLoading(regOtpSubmit, false);
        regOtpAttempts += 1;
        regOtpCtrl.markError();
        toast(err.message || "Code invalide.", "err");
        if (regOtpAttempts >= ((cfg.otp || {}).maxAttempts || 5)) {
          toast("Trop de tentatives. Demandez un nouveau code.", "err");
        }
      });
    });
  }

  var regSuccessBtn = document.getElementById("regSuccessGo");
  if (regSuccessBtn) {
    regSuccessBtn.addEventListener("click", function () {
      var dest = (cfg.roleRoutes || {})[selectedRole] || (cfg.roleRoutes || {}).collaborateur || "login.html";
      playIntroTransition(dest);
    });
  }

  /* =========================================================
     Connexion
     ========================================================= */
  var loginForm = document.getElementById("loginForm");
  if (loginForm) {
    var emailField = document.getElementById("f-loginEmail");
    var passField = document.getElementById("f-loginPassword");
    var emailInput = document.getElementById("loginEmail");
    var passInput = document.getElementById("loginPassword");
    var submitBtn = document.getElementById("loginSubmit");

    loginForm.addEventListener("submit", function (e) {
      e.preventDefault();
      clearError(emailField); clearError(passField);
      var ok = true;
      if (!EMAIL_RE.test(emailInput.value.trim())) { setError(emailField, "Adresse e-mail invalide."); ok = false; }
      if (passInput.value.length < 6) { setError(passField, "Le mot de passe doit contenir au moins 6 caractères."); ok = false; }
      if (!ok) return;

      setLoading(submitBtn, true);
      api(EP.login, { email: emailInput.value.trim(), password: passInput.value }).then(function (res) {
        saveSession({ token: res.token, role: res.role, name: res.name, email: res.email, ts: Date.now() });
        var dest = (cfg.roleRoutes || {})[res.role] || (cfg.roleRoutes || {}).collaborateur;
        playIntroTransition(dest);
      }).catch(function (err) {
        setLoading(submitBtn, false);
        toast(err.message || "Échec de la connexion.", "err");
      });
    });
  }

  /* =========================================================
     Boutons sociaux (login uniquement — cf. cahier des charges §11-12)
     ========================================================= */
  document.querySelectorAll("[data-oauth]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var provider = btn.getAttribute("data-oauth");
      setLoading(btn, true);
      // En production : redirection vers cfg.endpoints.oauthGoogle / oauthApple
      // (flux OAuth/OIDC géré côté serveur — jamais de token stocké côté client ici).
      setTimeout(function () {
        setLoading(btn, false);
        var accountExists = Math.random() > 0.5;
        if (accountExists) {
          var role = "collaborateur";
          saveSession({ token: "mock-oauth-" + Date.now(), role: role, name: "Compte " + provider, email: "compte@" + provider + ".ci", ts: Date.now() });
          playIntroTransition((cfg.roleRoutes || {})[role]);
        } else {
          toast("Aucun compte " + provider + " trouvé — complétez votre inscription.", "ok");
          goTo("reg-role");
        }
      }, 900);
    });
  });

  /* =========================================================
     Mot de passe oublié
     ========================================================= */
  var forgotState = { email: "", resetToken: "" };

  var forgotEmailForm = document.getElementById("forgotEmailForm");
  if (forgotEmailForm) {
    forgotEmailForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var fEmail = document.getElementById("f-forgotEmail");
      clearError(fEmail);
      var email = document.getElementById("forgotEmail").value.trim();
      if (!EMAIL_RE.test(email)) { setError(fEmail, "Adresse e-mail invalide."); return; }

      var btn = document.getElementById("forgotEmailSubmit");
      setLoading(btn, true);
      api(EP.forgotSendCode, { email: email }).then(function () {
        forgotState.email = email;
        setLoading(btn, false);
        goTo("forgot-verify");
        prepForgotOtp();
      }).catch(function (err) {
        setLoading(btn, false);
        setError(fEmail, err.message);
      });
    });
  }

  var forgotOtpRoot = document.getElementById("forgotOtp");
  var forgotOtpCtrl = forgotOtpRoot ? setupOtp(forgotOtpRoot) : null;
  var forgotOtpSubmit = document.getElementById("forgotOtpSubmit");
  var forgotOtpResend = document.getElementById("forgotOtpResend");
  var forgotOtpTimer = document.getElementById("forgotOtpTimer");
  var forgotOtpEmailLabel = document.getElementById("forgotOtpEmailLabel");
  var forgotOtpAttempts = 0;

  function prepForgotOtp() {
    if (forgotOtpEmailLabel) forgotOtpEmailLabel.textContent = forgotState.email;
    if (forgotOtpCtrl) forgotOtpCtrl.reset();
    forgotOtpAttempts = 0;
    startResendTimer(forgotOtpResend, forgotOtpTimer, (cfg.otp || {}).resendCooldownSeconds || 45);
  }
  if (forgotOtpRoot) {
    forgotOtpRoot.addEventListener("otp:change", function (e) {
      if (forgotOtpSubmit) forgotOtpSubmit.disabled = !e.detail.complete;
    });
  }
  if (forgotOtpResend) {
    forgotOtpResend.addEventListener("click", function () {
      api(EP.forgotSendCode, { email: forgotState.email }).then(function () {
        toast("Un nouveau code a été envoyé.", "ok");
        startResendTimer(forgotOtpResend, forgotOtpTimer, (cfg.otp || {}).resendCooldownSeconds || 45);
      });
    });
  }
  if (forgotOtpSubmit) {
    forgotOtpSubmit.addEventListener("click", function () {
      var code = forgotOtpCtrl.value();
      if (code.length < 6) return;
      setLoading(forgotOtpSubmit, true);
      api(EP.forgotVerifyCode, { email: forgotState.email, code: code }).then(function (res) {
        forgotState.resetToken = res.resetToken;
        setLoading(forgotOtpSubmit, false);
        goTo("forgot-password");
      }).catch(function (err) {
        setLoading(forgotOtpSubmit, false);
        forgotOtpAttempts += 1;
        forgotOtpCtrl.markError();
        toast(err.message || "Code invalide.", "err");
        if (forgotOtpAttempts >= ((cfg.otp || {}).maxAttempts || 5)) {
          toast("Trop de tentatives. Demandez un nouveau code.", "err");
        }
      });
    });
  }

  var newPwForm = document.getElementById("forgotPasswordForm");
  var newPwInput = document.getElementById("forgotPassword");
  var newPw2Input = document.getElementById("forgotPassword2");
  if (newPwInput) wirePasswordStrength(newPwInput, document.getElementById("forgotPwStrength"), document.getElementById("forgotPwRules"));

  if (newPwForm) {
    newPwForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var fPw = document.getElementById("f-forgotPassword");
      var fPw2 = document.getElementById("f-forgotPassword2");
      clearError(fPw); clearError(fPw2);
      var v = newPwInput.value;
      var strongEnough = v.length >= 8 && /[A-Z]/.test(v) && /[a-z]/.test(v) && /[0-9]/.test(v);
      var ok = true;
      if (!strongEnough) { setError(fPw, "Respectez au minimum les règles ci-dessus."); ok = false; }
      if (newPw2Input.value !== v || !v) { setError(fPw2, "Les mots de passe ne correspondent pas."); ok = false; }
      if (!ok) return;

      var btn = document.getElementById("forgotPasswordSubmit");
      setLoading(btn, true);
      api(EP.forgotResetPassword, { resetToken: forgotState.resetToken, password: v }).then(function () {
        setLoading(btn, false);
        goTo("forgot-success");
      }).catch(function (err) {
        setLoading(btn, false);
        toast(err.message || "Une erreur est survenue.", "err");
      });
    });
  }
})();
