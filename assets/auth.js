(function () {
  var cfg = window.GED_AUTH_CONFIG || {};
  var page = document.body.getAttribute("data-page");
  var params = new URLSearchParams(location.search);

  /* ---------------- Toasts ---------------- */
  var toastHost = document.createElement("div");
  toastHost.className = "toast-host";
  document.body.appendChild(toastHost);
  function toast(message, kind) {
    var el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = message;
    toastHost.appendChild(el);
    setTimeout(function () { el.remove(); }, 3600);
  }

  /* ---------------- Session ---------------- */
  function saveSession(data) {
    try {
      localStorage.setItem(cfg.sessionKey || "ged_session", JSON.stringify(data));
    } catch (e) {}
  }
  function getSession() {
    try {
      return JSON.parse(localStorage.getItem(cfg.sessionKey || "ged_session"));
    } catch (e) {
      return null;
    }
  }

  /* ---------------- API ---------------- */
  function api(method, path, body) {
    if (cfg.useMock) {
      return new Promise(function (resolve, reject) {
        setTimeout(function () {
          if (path === cfg.endpoints.login) {
            if (!body.email || !body.password) return reject(new Error("Identifiants invalides."));
            resolve({
              ok: true,
              token: "mock-" + btoa(body.email).slice(0, 16) + "-" + Date.now(),
              role: body.role,
              name: deriveName(body.email),
              email: body.email
            });
          } else if (path === cfg.endpoints.register) {
            resolve({ ok: true });
          } else {
            resolve({ ok: true });
          }
        }, 700);
      });
    }
    var headers = { Accept: "application/json", "Content-Type": "application/json" };
    return fetch(cfg.apiBase + path, {
      method: method,
      credentials: "include",
      headers: headers,
      body: JSON.stringify(body || {})
    }).then(function (res) {
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (j) {
          throw new Error(j.message || (res.status === 401 ? "Identifiants invalides." : "Erreur serveur (" + res.status + ")."));
        });
      }
      return res.json();
    });
  }

  function deriveName(email) {
    var local = String(email || "").split("@")[0] || "Utilisateur";
    return local
      .replace(/[._-]+/g, " ")
      .split(" ")
      .filter(Boolean)
      .map(function (w) { return w.charAt(0).toUpperCase() + w.slice(1); })
      .join(" ");
  }

  /* ---------------- Validation helpers ---------------- */
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  function setError(fieldEl, msg) {
    fieldEl.classList.add("invalid");
    var em = fieldEl.querySelector(".err-msg");
    if (em && msg) em.textContent = msg;
  }
  function clearError(fieldEl) {
    fieldEl.classList.remove("invalid");
  }

  function setLoading(btn, on) {
    btn.classList.toggle("loading", on);
    btn.disabled = on;
  }

  /* ---------------- Password visibility ---------------- */
  document.querySelectorAll(".toggle-eye").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.getElementById(btn.getAttribute("data-target"));
      if (!input) return;
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? "Masquer le mot de passe" : "Afficher le mot de passe");
      btn.innerHTML = show
        ? '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/><line x1="3" y1="21" x2="21" y2="3"/></svg>'
        : '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>';
    });
  });

  /* ---------------- Role picker ---------------- */
  document.querySelectorAll(".role-pick").forEach(function (pick) {
    pick.addEventListener("click", function (e) {
      var opt = e.target.closest(".role-opt");
      if (!opt || opt.classList.contains("disabled")) return;
      pick.querySelectorAll(".role-opt").forEach(function (o) { o.classList.remove("on"); });
      opt.classList.add("on");
      pick.setAttribute("data-value", opt.getAttribute("data-role"));
    });
  });

  /* ================= LOGIN PAGE ================= */
  if (page === "login") {
    var form = document.getElementById("loginForm");
    var emailField = document.getElementById("f-email");
    var passField = document.getElementById("f-password");
    var emailInput = document.getElementById("email");
    var passInput = document.getElementById("password");
    var rolePick = document.getElementById("loginRole");
    var submitBtn = document.getElementById("loginSubmit");

    if (params.get("registered") === "1") {
      toast("Compte créé avec succès. Connectez-vous.", "ok");
      var pre = params.get("email");
      if (pre && emailInput) emailInput.value = pre;
    }

    var existing = getSession();
    if (existing && existing.token && existing.role && cfg.roleRoutes[existing.role]) {
      var box = document.getElementById("resumeBanner");
      if (box) {
        box.style.display = "flex";
        box.querySelector("b").textContent = existing.name || existing.email || "";
        var resumeLink = document.getElementById("resumeLink");
        if (resumeLink) resumeLink.setAttribute("href", cfg.roleRoutes[existing.role]);
      }
    }

    if (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var ok = true;
        clearError(emailField); clearError(passField);

        if (!EMAIL_RE.test(emailInput.value.trim())) {
          setError(emailField, "Adresse e-mail invalide.");
          ok = false;
        }
        if (passInput.value.length < 6) {
          setError(passField, "Le mot de passe doit contenir au moins 6 caractères.");
          ok = false;
        }
        var role = rolePick ? rolePick.getAttribute("data-value") : null;
        if (!role) {
          toast("Sélectionnez votre rôle pour continuer.", "err");
          ok = false;
        }
        if (!ok) return;

        setLoading(submitBtn, true);
        api("POST", cfg.endpoints.login, {
          email: emailInput.value.trim(),
          password: passInput.value,
          role: role
        }).then(function (res) {
          saveSession({
            token: res.token,
            role: res.role || role,
            name: res.name || deriveName(emailInput.value),
            email: res.email || emailInput.value.trim(),
            ts: Date.now()
          });
          toast("Connexion réussie. Redirection…", "ok");
          var dest = cfg.roleRoutes[res.role || role] || cfg.roleRoutes.collaborateur;
          setTimeout(function () { location.href = dest; }, 500);
        }).catch(function (err) {
          setLoading(submitBtn, false);
          toast(err.message || "Échec de la connexion.", "err");
        });
      });
    }
  }

  /* ================= REGISTER PAGE ================= */
  if (page === "register") {
    var rform = document.getElementById("registerForm");
    var nameField = document.getElementById("f-fullname");
    var remailField = document.getElementById("f-email");
    var rpassField = document.getElementById("f-password");
    var rpass2Field = document.getElementById("f-password2");
    var nameInput = document.getElementById("fullname");
    var remailInput = document.getElementById("remail");
    var cabinetInput = document.getElementById("cabinet");
    var rpassInput = document.getElementById("rpassword");
    var rpass2Input = document.getElementById("rpassword2");
    var rrolePick = document.getElementById("registerRole");
    var termsInput = document.getElementById("terms");
    var rsubmitBtn = document.getElementById("registerSubmit");

    if (rform) {
      rform.addEventListener("submit", function (e) {
        e.preventDefault();
        var ok = true;
        [nameField, remailField, rpassField, rpass2Field].forEach(clearError);

        if (!nameInput.value.trim() || nameInput.value.trim().length < 2) {
          setError(nameField, "Indiquez votre nom complet.");
          ok = false;
        }
        if (!EMAIL_RE.test(remailInput.value.trim())) {
          setError(remailField, "Adresse e-mail invalide.");
          ok = false;
        }
        if (rpassInput.value.length < 8 || !/[0-9]/.test(rpassInput.value)) {
          setError(rpassField, "8 caractères minimum, avec au moins un chiffre.");
          ok = false;
        }
        if (rpass2Input.value !== rpassInput.value || !rpass2Input.value) {
          setError(rpass2Field, "Les mots de passe ne correspondent pas.");
          ok = false;
        }
        var role = rrolePick ? rrolePick.getAttribute("data-value") : null;
        if (!role) {
          toast("Sélectionnez le rôle du compte à créer.", "err");
          ok = false;
        }
        if (!termsInput.checked) {
          toast("Veuillez accepter les conditions d'utilisation.", "err");
          ok = false;
        }
        if (!ok) return;

        setLoading(rsubmitBtn, true);
        api("POST", cfg.endpoints.register, {
          fullname: nameInput.value.trim(),
          email: remailInput.value.trim(),
          cabinet: cabinetInput ? cabinetInput.value.trim() : "",
          role: role,
          password: rpassInput.value
        }).then(function () {
          toast("Compte créé avec succès.", "ok");
          setTimeout(function () {
            location.href = "login.html?registered=1&email=" + encodeURIComponent(remailInput.value.trim());
          }, 500);
        }).catch(function (err) {
          setLoading(rsubmitBtn, false);
          toast(err.message || "Échec de la création du compte.", "err");
        });
      });
    }
  }
})();
