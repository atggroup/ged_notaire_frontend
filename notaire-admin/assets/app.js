(function () {
  var cfg = Object.assign(
    { apiBase: "/api", useMock: true, role: "collaborateur", capabilities: {}, token: "" },
    window.GED_CONFIG || {}
  );
  var page = (location.pathname.split("/").pop() || "index.html").toLowerCase();
  var params = new URLSearchParams(location.search);

  /* ---------------- Skeleton de chargement ----------------
     Avant même le premier appel API, on remplace tout contenu figé dans le
     HTML (texte « Chargement… » ou fiches d'exemple) par un skeleton animé :
     ainsi aucune donnée fictive ne s'affiche à l'écran, même un instant, en
     attendant la réponse du serveur. Les fonctions renderXxx() déjà
     existantes plus bas écrasent ce skeleton dès que les données réelles
     arrivent (ou l'état vide / l'état d'erreur, selon le cas). En mode démo
     (useMock:true) on ne touche à rien : le contenu d'exemple reste affiché
     tel quel, comme avant. */
  (function renderInitialSkeletons() {
    if (cfg.useMock) return;
    function bar(w, cls) {
      return '<span class="skel-block skel-line' + (cls ? " " + cls : "") + '" style="width:' + (w || "70%") + '"></span>';
    }
    function docCard() {
      return '<div class="doc skel-doc" aria-hidden="true">' +
        '<div class="skel-block skel-thumb" style="height:118px"></div>' +
        bar("78%", "lg") + bar("52%", "sm") +
        '<div class="row" style="display:flex;align-items:center;margin-top:12px;gap:10px">' + bar("34%") +
        '<span class="skel-block skel-avatar" style="width:32px;height:32px;margin-left:auto"></span></div></div>';
    }
    function row(cols) {
      var tds = "";
      for (var i = 0; i < cols; i++) tds += "<td>" + bar(i === cols - 1 ? "26px" : (i === 0 ? "78%" : "58%")) + "</td>";
      return '<tr class="skel-row" aria-hidden="true">' + tds + "</tr>";
    }
    function mini() {
      return '<div class="skel-mini" aria-hidden="true"><span class="skel-block skel-avatar" style="width:36px;height:36px"></span>' + bar("62%") + "</div>";
    }
    var docsHost = document.querySelector(".docs");
    if (docsHost && (page.indexOf("index") === 0 || page.indexOf("documents") === 0 || page.indexOf("recherche") === 0)) {
      docsHost.innerHTML = Array(6).fill(0).map(docCard).join("");
    }
    [["dossiersBody", 6], ["usersBody", 4], ["permissionsBody", 6], ["accessRequestsBody", 5], ["queueBody", 5], ["backupHistoryBody", 4]].forEach(function (pair) {
      var body = document.getElementById(pair[0]);
      if (body) body.innerHTML = Array(4).fill(0).map(function () { return row(pair[1]); }).join("");
    });
    ["notificationsList", "savedSearchesList", "importantAlertsList"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.innerHTML = Array(3).fill(0).map(mini).join("");
    });
    if (page.indexOf("journal-audit") === 0) {
      var auditCard = document.querySelector(".wrap .card");
      if (auditCard) auditCard.innerHTML = Array(5).fill(0).map(mini).join("");
    }
    if (page.indexOf("document-detail") === 0) {
      var title = document.querySelector(".two h3");
      if (title) title.innerHTML = bar("58%", "lg");
    }
    var access = document.getElementById("accessRequestList");
    if (access) access.innerHTML = '<span class="skel-block skel-avatar" style="width:36px;height:36px"></span>' + bar("60%");
    var alertsEl = document.getElementById("dashboardAlerts");
    if (alertsEl) alertsEl.innerHTML = '<span class="skel-block skel-avatar" style="width:36px;height:36px"></span>' + bar("60%");
  })();

  document.documentElement.setAttribute("data-theme", cfg.theme || cfg.role);
  document.body.setAttribute("data-theme", cfg.theme || cfg.role);
  document.body.setAttribute("data-role", cfg.role);

  /* ---------------- Session / Auth guard ---------------- */
  var SESSION_KEY = "ged_session";
  var ROLE_LABELS = { admin: "Notaire \u00b7 Admin", notaire: "Notaire \u00b7 Admin", clerc: "Clerc principal", collaborateur: "Collaborateur" };

  function getSession() {
    try { return JSON.parse(localStorage.getItem(SESSION_KEY)); } catch (e) { return null; }
  }
  function clearSession() {
    try { localStorage.removeItem(SESSION_KEY); } catch (e) {}
  }
  function initials(name) {
    var parts = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "GD";
    return (parts[0][0] + (parts[1] ? parts[1][0] : parts[0][1] || "")).toUpperCase();
  }
  /* Déconnexion après inactivité (30 min par défaut, cfg.inactivityMinutes).
     Un poste resté ouvert à l'accueil de l'étude donnait accès aux actes
     pendant toute la durée du jeton (8 h). L'activité est partagée entre les
     onglets : travailler dans l'un garde les autres ouverts. */
  function surveillerInactivite() {
    var limite = (Number(cfg.inactivityMinutes) || 30) * 60000;
    var CLE = "ged_derniere_activite";
    function marquer() { try { localStorage.setItem(CLE, String(Date.now())); } catch (e) {} }
    var dernier = 0;
    ["click", "keydown", "mousemove", "touchstart", "scroll"].forEach(function (evt) {
      document.addEventListener(evt, function () {
        var maintenant = Date.now();
        if (maintenant - dernier > 15000) { dernier = maintenant; marquer(); }
      }, { passive: true, capture: true });
    });
    marquer();
    setInterval(function () {
      var derniere = 0;
      try { derniere = Number(localStorage.getItem(CLE) || 0); } catch (e) {}
      if (derniere && Date.now() - derniere > limite) {
        clearSession();
        location.href = "../login.html?expire=inactivite";
      }
    }, 20000);
  }
  function requireSession() {
    var s = getSession();
    if (!s || !s.token) {
      location.href = "../login.html";
      return null;
    }
    if (s.role && cfg.role && s.role !== cfg.role) {
      toast("Cette session correspond \u00e0 un autre r\u00f4le. Reconnectez-vous.", "err");
      clearSession();
      setTimeout(function () { location.href = "../login.html"; }, 700);
      return null;
    }
    return s;
  }
  function applySessionIdentity(s) {
    if (!s) return;
    document.querySelectorAll(".who .nm").forEach(function (el) { if (s.name) el.textContent = s.name; });
    document.querySelectorAll(".who .rl").forEach(function (el) {
      var label = ROLE_LABELS[s.role] || cfg.role;
      var svg = el.querySelector("svg");
      el.textContent = label;
      if (svg) el.appendChild(svg);
    });
    document.querySelectorAll(".who .av").forEach(function (el) { if (s.name) el.textContent = initials(s.name); });
  }
  function wireLogout() {
    document.querySelectorAll("#logoutLink").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        var headers = cfg.token ? { Authorization: "Bearer " + cfg.token } : {};
        fetch(cfg.apiBase + "/auth/logout", { method: "POST", credentials: "include", headers: headers })
          .catch(function () {})
          .then(function () { clearSession(); toast("Déconnexion de toutes les sessions réussie.", "ok"); setTimeout(function () { location.href = "../login.html"; }, 350); });
      });
    });
  }

  var PAGE_CAP = {
    "scan.html": "scan",
    "dossiers.html": "dossiers",
    "utilisateurs.html": "users",
    "permissions.html": "permissions",
    "journal-audit.html": "audit",
    "sauvegarde.html": "backup",
    "configuration.html": "config"
  };

  var toastHost = document.createElement("div");
  toastHost.className = "toast-host";
  // Les toasts portent l'essentiel du retour utilisateur (succès, refus,
  // message d'erreur du serveur). Sans région « live », un lecteur d'écran
  // ne les annonce jamais : l'utilisateur non-voyant agit sans savoir si
  // l'action a abouti. `polite` n'interrompt pas la lecture en cours.
  toastHost.setAttribute("role", "status");
  toastHost.setAttribute("aria-live", "polite");
  toastHost.setAttribute("aria-atomic", "false");
  document.body.appendChild(toastHost);

  function toast(message, kind) {
    var el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = message;
    toastHost.appendChild(el);
    setTimeout(function () { el.remove(); }, 3200);
  }

  function slug(s) {
    return String(s || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_|_$/g, "") || "field";
  }

  function can(key) {
    return !!cfg.capabilities[key];
  }

  function deny(msg) {
    toast(msg || "Action non autorisée pour ce rôle.", "err");
    return false;
  }

  function api(method, path, body) {
    var payload = {
      role: cfg.role,
      path: path,
      method: method,
      body: body == null ? null : body
    };
    if (cfg.useMock) {
      return new Promise(function (resolve) {
        setTimeout(function () { resolve({ ok: true, mock: true, data: payload }); }, 180);
      });
    }
    var headers = { Accept: "application/json" };
    if (body && method !== "GET") headers["Content-Type"] = "application/json";
    if (cfg.token) headers.Authorization = "Bearer " + cfg.token;
    return fetch(cfg.apiBase + path, {
      method: method,
      credentials: "include",
      headers: headers,
      body: body && method !== "GET" ? JSON.stringify(body) : undefined
    }).then(function (res) {
      if (res.status === 401) throw new Error("Session expirée.");
      var ct = res.headers.get("content-type") || "";
      var parsed = ct.indexOf("json") >= 0 ? res.json().catch(function () { return {}; }) : res.text().then(function (t) { return { detail: t }; });
      return parsed.then(function (data) {
        // DRF renvoie { detail: "..." }, { non_field_errors: [...] } ou { champ: [...] } :
        // sans lire ces clés, le message précis calculé côté serveur (ex. "Un dossier
        // dédié existe déjà pour ce client.") n'atteignait jamais l'utilisateur.
        if (!res.ok) {
          var message = res.status === 403 ? "Droits insuffisants." : "Erreur serveur (" + res.status + ").";
          if (data && typeof data === "object") {
            if (typeof data.detail === "string") message = data.detail;
            else if (Array.isArray(data.non_field_errors) && data.non_field_errors.length) message = data.non_field_errors[0];
            else {
              for (var key in data) {
                if (Object.prototype.hasOwnProperty.call(data, key) && Array.isArray(data[key]) && data[key].length) { message = data[key][0]; break; }
              }
            }
          }
          throw new Error(message);
        }
        return data;
      });
    }).catch(function (err) {
      toast(err.message || "Échec de la requête.", "err");
      throw err;
    });
  }

  function apiMultipart(path, formData) {
    if (cfg.useMock) return Promise.resolve({ reference: "MOCK-0001", nom: "Document de démonstration" });
    var headers = { Accept: "application/json" };
    if (cfg.token) headers.Authorization = "Bearer " + cfg.token;
    return fetch(cfg.apiBase + path, { method: "POST", credentials: "include", headers: headers, body: formData }).then(function (res) {
      if (res.status === 401) throw new Error("Session expirée.");
      if (res.status === 403) throw new Error("Droits insuffisants.");
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var message = "Le document n'a pas pu être archivé.";
          if (data && typeof data === "object") {
            if (typeof data.detail === "string") message = data.detail;
            else if (Array.isArray(data.non_field_errors) && data.non_field_errors.length) message = data.non_field_errors[0];
            else {
              for (var key in data) {
                if (Object.prototype.hasOwnProperty.call(data, key) && Array.isArray(data[key]) && data[key].length) { message = data[key][0]; break; }
              }
            }
          }
          throw new Error(message);
        }
        return data;
      });
    }).catch(function (err) { toast(err.message || "Échec de l'envoi.", "err"); throw err; });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>'"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[c]; });
  }
  function docDate(value) {
    if (!value) return "";
    var d = new Date(value);
    return isNaN(d) ? "" : d.toLocaleDateString("fr-FR", { day: "2-digit", month: "long", year: "numeric" });
  }
  function docCard(doc) {
    var level = doc.niveau_de_confidentialite || "Standard";
    var cls = level === "Confidentiel" ? "n3" : (level === "Restreint" ? "n2" : "n1");
    return '<div class="doc" data-ref="' + escapeHtml(doc.reference) + '">' +
      '<div class="thumb" style="background:var(--primary-soft)"><img src="../assets/img/illustration_document.png" alt="Document" style="width:68px;height:68px;object-fit:contain;filter:drop-shadow(0 4px 10px rgba(0,0,0,.12))" /><span class="tag" style="color:var(--primary)">' + escapeHtml(doc.type) + '</span><span class="lvl ' + cls + '" style="position:absolute;bottom:10px;left:10px">' + escapeHtml(level) + '</span></div>' +
      '<h4>' + escapeHtml(doc.nom) + '</h4><div class="meta">' + escapeHtml(doc.dossierReference || "Sans dossier") + (doc.created_at ? " · " + docDate(doc.created_at) : "") + '</div>' +
      '<div class="row" style="display:flex;align-items:center;margin-top:12px"><span class="code">' + escapeHtml(doc.reference) + '</span><a class="fav" href="#" aria-label="Favori" style="margin-left:auto;display:inline-flex;align-items:center;justify-content:center">' + (doc.is_favorite ? '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2.5 15.09 8.76 22 9.77 17 14.64 18.18 21.52 12 18.27 5.82 21.52 7 14.64 2 9.77 8.91 8.76"/></svg>' : '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2.5 15.09 8.76 22 9.77 17 14.64 18.18 21.52 12 18.27 5.82 21.52 7 14.64 2 9.77 8.91 8.76"/></svg>') + '</a><a class="open" href="document-detail.html?ref=' + encodeURIComponent(doc.reference) + '" aria-label="Ouvrir le document" style="margin-left:12px;display:inline-flex;align-items:center;justify-content:center"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7"/><path d="M8 7h9v9"/></svg></a></div></div>';
  }
  function renderDocuments(items) {
    var docs = document.querySelector(".docs");
    if (!docs || !Array.isArray(items)) return;
    docs.innerHTML = items.length ? items.map(docCard).join("") : '<div class="card" style="grid-column:1/-1;text-align:center"><h3>Aucun document</h3><p class="muted">Aucun document ne correspond à votre recherche.</p></div>';
  }
  function loadDocuments(path) {
    if (cfg.useMock) return Promise.resolve();
    return api("GET", path || "/documents").then(function (items) { renderDocuments(items); return items; });
  }

  function collectFields(root) {
    var data = {};
    (root || document).querySelectorAll(".inp,.sel,.ta").forEach(function (el, i) {
      var label = el.closest(".field") && el.closest(".field").querySelector("label");
      var key = el.name || el.getAttribute("data-field") || (label && slug(label.textContent)) || "field_" + i;
      data[key] = el.value;
    });
    (root || document).querySelectorAll(".seg").forEach(function (seg) {
      var label = seg.closest(".field") && seg.closest(".field").querySelector("label");
      var step = seg.closest(".step") && seg.closest(".step").querySelector(".lt");
      var on = seg.querySelector(".opt.on");
      var key = (label && slug(label.textContent)) || (step && slug(step.textContent)) || "segment";
      if (on) data[key] = on.textContent.trim();
    });
    return data;
  }

  function openModal(opts) {
    closeModal();
    var back = document.createElement("div");
    back.className = "modal-backdrop";
    back.innerHTML =
      '<div class="modal" role="dialog" aria-modal="true">' +
      "<h3>" + opts.title + "</h3>" +
      (opts.bodyHtml || "<p>" + (opts.text || "") + "</p>") +
      '<div class="modal-actions">' +
      (opts.cancel === false ? "" : '<button type="button" class="btn" data-modal-cancel>Annuler</button>') +
      '<button type="button" class="btn pri" data-modal-ok>' + (opts.ok || "Confirmer") + "</button>" +
      "</div></div>";
    document.body.appendChild(back);
    back.addEventListener("click", function (e) { if (e.target === back) closeModal(); });
    var annuler = back.querySelector("[data-modal-cancel]");
    if (annuler) annuler.addEventListener("click", closeModal);
    back.querySelector("[data-modal-ok]").addEventListener("click", function () {
      var payload = collectFields(back);
      Promise.resolve(opts.onOk && opts.onOk(payload)).then(closeModal).catch(function () {});
    });
  }

  function closeModal() {
    document.querySelectorAll(".modal-backdrop").forEach(function (n) { n.remove(); });
  }

  function requestAccess(ref) {
    if (!can("requestAccess")) return deny("Les demandes d'accès sont réservées au clerc et au collaborateur.");
    openModal({
      title: "Demande d'accès",
      ok: "Envoyer",
      bodyHtml:
        "<p>La demande est transmise au notaire et journalisée.</p>" +
        '<div class="field"><label>Référence</label><input class="inp" name="ref" value="' + String(ref || "").replace(/"/g, "") + '" readonly></div>' +
        '<div class="field"><label>Motif</label><textarea class="ta" name="motif" placeholder="Précisez le besoin métier…"></textarea></div>',
      onOk: function (payload) {
        if (!payload.motif) { toast("Le motif est obligatoire.", "err"); throw new Error("motif"); }
        return api("POST", "/access-requests", payload).then(function () {
          toast("Demande envoyée au notaire.", "ok");
        });
      }
    });
  }

  function guardPage() {
    var needed = PAGE_CAP[page];
    if (needed && !can(needed)) {
      toast("Cette page n'appartient pas à votre périmètre.", "err");
      setTimeout(function () { location.href = "index.html"; }, 500);
      return false;
    }
    return true;
  }

  function setupNav() {
    var app = document.querySelector(".app");
    var top = document.querySelector(".top");
    if (!app || !top) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "iconbtn menu-btn";
    btn.setAttribute("aria-label", "Ouvrir le menu");
    btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>';
    top.insertBefore(btn, top.firstChild);
    var back = document.createElement("div");
    back.className = "rail-backdrop";
    app.appendChild(back);
    function close() { app.classList.remove("nav-open"); }
    btn.addEventListener("click", function () { app.classList.toggle("nav-open"); });
    back.addEventListener("click", close);
    document.querySelectorAll(".rail a").forEach(function (a) { a.addEventListener("click", close); });
  }

  /* ----------------------------------------------------------------
     Info-bulles du menu latéral : en position:fixed (et non plus
     absolute) pour ne jamais être rognées par le défilement du menu
     (.rail-nav défile désormais verticalement quand il contient plus
     d'icônes que la hauteur d'écran ne peut en afficher). La position
     est donc calculée en JS au survol, par rapport à la fenêtre.
     ---------------------------------------------------------------- */
  /* Bouton "Retour" générique : sur les pages de détail (document,
     dossier), ramène à la page précédente dans l'historique du
     navigateur. Repli sur le tableau de bord si la page a été ouverte
     directement (pas d'historique à remonter, ex. lien partagé). */
  function setupBackButtons() {
    document.querySelectorAll("[data-go-back]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (window.history.length > 1) window.history.back();
        else location.href = "index.html";
      });
    });
  }

  function setupNavTooltips() {
    document.querySelectorAll(".nav-i").forEach(function (item) {
      var tip = item.querySelector(".tip");
      if (!tip) return;
      item.addEventListener("mouseenter", function () {
        var r = item.getBoundingClientRect();
        tip.style.top = (r.top + r.height / 2) + "px";
        tip.style.left = (r.right + 10) + "px";
        tip.style.transform = "translateY(-50%)";
      });
    });
  }

  /* ---------------- Barre du haut + rail toujours fixes au scroll ---------------- */
  function setupFixedHeader() {
    var top = document.querySelector(".top");
    var body = document.querySelector(".body");
    if (!top || !body) return;
    var spacer = document.createElement("div");
    spacer.className = "top-spacer";
    top.insertAdjacentElement("afterend", spacer);
    function sync() {
      var h = top.offsetHeight;
      document.documentElement.style.setProperty("--topbar-h", h + "px");
    }
    sync();
    window.addEventListener("resize", sync);
    window.addEventListener("orientationchange", sync);
    if (window.ResizeObserver) new ResizeObserver(sync).observe(top);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(sync);
  }

  function setupSearch() {
    document.querySelectorAll(".search input").forEach(function (input) {
      var q = params.get("q");
      if (q && page.indexOf("recherche") === 0) input.value = q;
      input.addEventListener("keydown", function (e) {
        if (e.key !== "Enter") return;
        e.preventDefault();
        var value = input.value.trim();
        if (page.indexOf("recherche") === 0) {
          api("GET", "/search?q=" + encodeURIComponent(value)).then(renderDocuments);
        } else {
          location.href = "recherche.html?q=" + encodeURIComponent(value);
        }
      });
    });
  }

  function setupSegs() {
    document.querySelectorAll(".seg").forEach(function (seg) {
      seg.addEventListener("click", function (e) {
        var opt = e.target.closest(".opt");
        if (!opt) return;
        if (/confidentiel/i.test(opt.textContent) && !can("validateActs") && page.indexOf("scan") === 0) {
          deny("Seul le notaire classe un acte en confidentiel.");
          return;
        }
        seg.querySelectorAll(".opt").forEach(function (o) { o.classList.remove("on"); });
        opt.classList.add("on");
      });
    });
  }

  function filterDocs(query, type) {
    var q = (query || "").toLowerCase();
    var t = (type || "tous").toLowerCase();
    if (t.indexOf("tous") === 0) t = "tous";
    var n = 0;
    document.querySelectorAll(".docs .doc").forEach(function (doc) {
      var text = doc.textContent.toLowerCase();
      var tag = (doc.querySelector(".tag") && doc.querySelector(".tag").textContent.trim().toLowerCase()) || "";
      var okType = t === "tous" || tag.indexOf(t) >= 0;
      var okQ = !q || text.indexOf(q) >= 0;
      var show = okType && okQ;
      doc.style.display = show ? "" : "none";
      if (show) n++;
    });
    var count = document.querySelector(".sec-h .muted");
    if (count && /résultat/i.test(count.textContent)) count.textContent = n + " résultat" + (n > 1 ? "s" : "");
    return n;
  }

  function setupDocs() {
    document.querySelectorAll(".doc .open").forEach(function (a) {
      var code = a.closest(".doc") && a.closest(".doc").querySelector(".code");
      if (code) a.href = "document-detail.html?ref=" + encodeURIComponent(code.textContent.trim());
    });

    document.querySelectorAll(".cat").forEach(function (cat) {
      cat.addEventListener("click", function () {
        cat.parentElement.querySelectorAll(".cat").forEach(function (c) { c.classList.remove("on"); });
        cat.classList.add("on");
        var type = (cat.querySelector(".ct") && cat.querySelector(".ct").textContent.trim()) || "Tous";
        loadDocuments("/documents?type=" + encodeURIComponent(type));
      });
    });

    var pills = document.querySelector(".pill-tabs");
    var docs = document.querySelector(".docs");
    if (pills && docs) {
      pills.addEventListener("click", function (e) {
        var pt = e.target.closest(".pt");
        if (!pt) return;
        pills.querySelectorAll(".pt").forEach(function (p) { p.classList.remove("on"); });
        pt.classList.add("on");
        docs.classList.toggle("list", /liste/i.test(pt.textContent));
      });
    }

    var docsHost = document.querySelector(".docs");
    if (docsHost) docsHost.addEventListener("click", function (e) {
      var doc = e.target.closest(".doc");
      if (!doc) return;
      var ref = doc.getAttribute("data-ref") || (doc.querySelector(".code") || {}).textContent || "";
      if (e.target.closest(".ask")) { e.preventDefault(); requestAccess(ref); }
      if (e.target.closest(".fav")) { e.preventDefault(); api("POST", "/documents/favorite", { ref: ref }).then(function () { loadDocuments(page.indexOf("recherche") === 0 ? "/search?q=" + encodeURIComponent(params.get("q") || "") : "/documents"); }); }
    });
    if (page.indexOf("documents") === 0 && !cfg.useMock) loadDocuments("/documents");
  }

  function setupTables() {
    document.querySelectorAll(".card > table").forEach(function (table) {
      var wrap = document.createElement("div");
      wrap.className = "table-wrap";
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
  }

  function setupRecherche() {
    if (page.indexOf("recherche") !== 0) return;
    function runSearch() {
      var data = collectFields();
      var q = data.mot_cle_reference || data.q || "";
      return api("GET", "/search?q=" + encodeURIComponent(q) + "&type=" + encodeURIComponent(data.type || data.type_d_acte || "") + "&dossier=" + encodeURIComponent(data.dossier || "")).then(function (items) {
        renderDocuments(items);
        return items;
      });
    }
    var btn = document.querySelector(".btn.pri");
    if (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        runSearch().then(function (items) {
          toast(items.length + " résultat" + (items.length > 1 ? "s" : "") + " dans votre périmètre.", "ok");
        });
      });
    }
    var queryInput = document.querySelector('[name="q"]');
    if (params.get("q") && queryInput) queryInput.value = params.get("q");
    runSearch();
    function loadSavedSearches() {
      return api("GET", "/searches/saved").then(function (items) {
        var host = document.getElementById("savedSearchesList");
        if (!host) return;
        if (!Array.isArray(items) || !items.length) { host.innerHTML = '<div class="hint">Aucune recherche sauvegardée.</div>'; return; }
        host.innerHTML = items.map(function (s) {
          return '<div class="kv"><span>' + escapeHtml(s.name) + '</span><b><button class="btn" data-run-search="' + s.id + '">Lancer</button> <button class="btn" data-delete-search="' + s.id + '">Supprimer</button></b></div>';
        }).join("");
        var byId = {}; items.forEach(function (s) { byId[s.id] = s; });
        host.querySelectorAll("[data-run-search]").forEach(function (b) {
          b.addEventListener("click", function () {
            var s = byId[b.getAttribute("data-run-search")]; if (!s) return;
            var filters = s.filters || {};
            if (queryInput) queryInput.value = filters.q || "";
            var typeSel = document.querySelector('[name="type"]'); if (typeSel) typeSel.value = filters.type || "";
            var dossierInput = document.querySelector('[name="dossier"]'); if (dossierInput) dossierInput.value = filters.dossier || "";
            runSearch().then(function (results) { toast(results.length + " résultat" + (results.length > 1 ? "s" : "") + " dans votre périmètre.", "ok"); });
          });
        });
        host.querySelectorAll("[data-delete-search]").forEach(function (b) {
          b.addEventListener("click", function () {
            if (!confirm("Supprimer cette recherche sauvegardée ?")) return;
            api("DELETE", "/searches/saved", { id: b.getAttribute("data-delete-search") }).then(function () { toast("Recherche supprimée.", "ok"); loadSavedSearches(); });
          });
        });
      }).catch(function () {});
    }
    var saveSearchBtn = document.getElementById("saveSearchBtn");
    if (saveSearchBtn) saveSearchBtn.addEventListener("click", function () {
      openModal({
        title: "Sauvegarder cette recherche", ok: "Enregistrer",
        bodyHtml: '<div class="field"><label>Nom de la recherche</label><input class="inp" name="searchName" placeholder="Ex. Ventes en cours"></div>',
        onOk: function (payload) {
          if (!payload.searchName) { toast("Le nom est obligatoire.", "err"); throw new Error("name"); }
          var data = collectFields();
          var filters = { q: data.q || data.mot_cle_reference || "", type: data.type || data.type_d_acte || "", dossier: data.dossier || "" };
          return api("POST", "/searches/saved", { name: payload.searchName, filters: filters }).then(function () { toast("Recherche sauvegardée.", "ok"); loadSavedSearches(); });
        }
      });
    });
    loadSavedSearches();
  }

  /* Second facteur par application d'authentification (TOTP).
     Remplace l'interrupteur « Activée / Désactivée » qui ne commandait rien :
     l'écran affiche l'état réel et guide l'activation pas à pas. */
  function setupSecuriteConnexion() {
    var zone = document.getElementById("totpZone");
    if (!zone) return;
    function afficher(etat) {
      if (etat.enabled) {
        zone.innerHTML = '<span class="b green">Activée</span> depuis le ' + escapeHtml(new Date(etat.enabledAt).toLocaleDateString("fr-FR")) +
          ' · ' + etat.recoveryCodesRemaining + ' code(s) de secours restant(s).<br>' +
          '<button type="button" class="btn" id="totpDesactiver" style="margin-top:10px">Désactiver</button>';
        document.getElementById("totpDesactiver").addEventListener("click", desactiver);
      } else {
        zone.innerHTML = '<span class="b warn">Non activée</span> Votre second facteur repose sur l\'e-mail : si votre messagerie ' +
          'était compromise, votre compte le serait aussi.<br>' +
          '<button type="button" class="btn pri" id="totpActiver" style="margin-top:10px">Activer l\'application d\'authentification</button>';
        document.getElementById("totpActiver").addEventListener("click", activer);
      }
    }
    function charger() { return api("GET", "/auth/totp").then(afficher).catch(function () { zone.textContent = "État indisponible."; }); }
    function echec(err, defaut) { toast((err && err.message) || defaut, "err"); throw err; }
    function activer() {
      openModal({
        title: "Activer l'application d'authentification", ok: "Continuer",
        bodyHtml: '<p>Par sécurité, confirmez d\'abord votre mot de passe.</p>' +
          '<div class="field"><label>Mot de passe</label><input class="inp" type="password" name="password" autocomplete="current-password"></div>',
        onOk: function (donnees) {
          return api("POST", "/auth/totp/setup", { password: donnees.password || "" }).then(function (cle) {
            setTimeout(function () { etapeCle(cle); }, 0);
          }).catch(function (err) { echec(err, "Mot de passe incorrect."); });
        }
      });
    }
    function etapeCle(cle) {
      openModal({
        title: "Ajoutez la GED dans votre application", ok: "Vérifier le code",
        bodyHtml: '<ol style="font-size:13px;line-height:1.6;padding-left:18px;margin:0 0 12px">' +
          '<li>Installez <b>Google Authenticator</b> ou <b>Microsoft Authenticator</b> sur votre téléphone.</li>' +
          '<li>Dans l\'application : <b>Ajouter un compte</b> → <b>Saisir une clé de configuration</b> (compte « GED notariale »).</li>' +
          '<li>Saisissez cette clé : <code style="display:block;margin:6px 0;padding:8px;border-radius:8px;background:var(--cream);font-size:14px;letter-spacing:1px;word-break:break-all">' + escapeHtml(cle.secret) + '</code>' +
          'Sur le téléphone lui-même : <a href="' + escapeHtml(cle.otpauthUri) + '">ouvrir directement dans l\'application</a>.</li>' +
          '<li>Saisissez le code à 6 chiffres affiché par l\'application :</li></ol>' +
          '<div class="field"><label>Code à 6 chiffres</label><input class="inp" name="code" inputmode="numeric" maxlength="6" autocomplete="one-time-code"></div>',
        onOk: function (donnees) {
          return api("POST", "/auth/totp/confirm", { code: (donnees.code || "").replace(/\s/g, "") }).then(function (res) {
            setTimeout(function () { etapeSecours(res.recoveryCodes || []); }, 0);
          }).catch(function (err) { echec(err, "Code incorrect : vérifiez l'heure de votre téléphone."); });
        }
      });
    }
    function etapeSecours(codes) {
      openModal({
        title: "Vos codes de secours", ok: "J'ai conservé mes codes",
        bodyHtml: '<p>Chaque code permet <b>une</b> connexion si vous perdez votre téléphone. Ils ne seront <b>plus jamais affichés</b> : ' +
          'imprimez-les ou notez-les et rangez-les en lieu sûr (coffre de l\'étude).</p>' +
          '<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:6px;font-family:monospace;font-size:14px;padding:10px;border-radius:10px;background:var(--cream)">' +
          codes.map(function (c) { return "<span>" + escapeHtml(c) + "</span>"; }).join("") + '</div>',
        onOk: function () { toast("Application d'authentification activée.", "ok"); return charger(); }
      });
    }
    function desactiver() {
      openModal({
        title: "Désactiver l'application d'authentification", ok: "Désactiver",
        bodyHtml: '<p>Votre connexion reposera de nouveau sur un code envoyé par e-mail. Les autres notaires seront prévenus.</p>' +
          '<div class="field"><label>Mot de passe</label><input class="inp" type="password" name="password" autocomplete="current-password"></div>' +
          '<div class="field"><label>Code de l\'application</label><input class="inp" name="code" inputmode="numeric" maxlength="6" autocomplete="one-time-code"></div>',
        onOk: function (donnees) {
          return api("POST", "/auth/totp/disable", { password: donnees.password || "", code: (donnees.code || "").replace(/\s/g, "") })
            .then(function () { toast("Application d'authentification désactivée.", "ok"); return charger(); })
            .catch(function (err) { echec(err, "Mot de passe ou code incorrect."); });
        }
      });
    }
    var boutonMdp = document.getElementById("changerMotDePasse");
    if (boutonMdp) boutonMdp.addEventListener("click", function () {
      openModal({
        title: "Changer le mot de passe", ok: "Enregistrer",
        bodyHtml: '<div class="field"><label>Mot de passe actuel</label><input class="inp" type="password" name="currentPassword" autocomplete="current-password"></div>' +
          '<div class="field"><label>Nouveau mot de passe</label><input class="inp" type="password" name="newPassword" autocomplete="new-password">' +
          '<div class="muted" style="font-size:12px;margin-top:4px">12 caractères minimum, avec majuscule, minuscule et chiffre ; pas de mot de passe courant.</div></div>' +
          '<div class="field"><label>Confirmer le nouveau mot de passe</label><input class="inp" type="password" name="confirmation" autocomplete="new-password"></div>',
        onOk: function (d) {
          if ((d.newPassword || "") !== (d.confirmation || "")) { toast("Les deux saisies du nouveau mot de passe diffèrent.", "err"); return Promise.reject(); }
          return api("POST", "/me/password", { currentPassword: d.currentPassword || "", newPassword: d.newPassword || "" }).then(function (res) {
            // Les autres sessions sont fermées ; celle-ci reçoit un jeton neuf.
            try {
              var session = JSON.parse(localStorage.getItem(SESSION_KEY) || "{}");
              session.token = res.token;
              localStorage.setItem(SESSION_KEY, JSON.stringify(session));
            } catch (e) {}
            cfg.token = res.token;
            toast("Mot de passe modifié. Vos autres sessions ont été fermées.", "ok");
          }).catch(function (err) { toast((err && err.message) || "Modification impossible.", "err"); throw err; });
        }
      });
    });
    charger();
  }

  function setupProfil() {
    if (page.indexOf("profil") !== 0) return;
    var nameInput = document.querySelector('[name="name"]');
    var emailInput = document.querySelector('[name="email"]');
    api("GET", "/me").then(function (profile) {
      if (nameInput) nameInput.value = profile.name || "";
      if (emailInput) emailInput.value = profile.email || "";
      document.querySelectorAll("[data-profile-name]").forEach(function (el) { el.textContent = profile.name || ""; });
      document.querySelectorAll("[data-profile-avatar]").forEach(function (el) { el.textContent = initials(profile.name); });
    });
    var saveTimer;
    document.querySelectorAll('[name="name"]').forEach(function (inp) {
      inp.addEventListener("change", function () {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
          api("PATCH", "/me", { name: inp.value.trim() }).then(function () { toast("Profil mis à jour.", "ok"); });
        }, 200);
      });
    });
  }

  /* Le libellé lisible vient désormais du serveur (statutLabel) : la table de
     correspondance locale mappait « en_attente_validation », valeur que le
     backend n'a jamais produite, et laissait donc passer le slug brut. */
  function queueRow(doc) {
    var label = doc.statutLabel || doc.statut || "À indexer";
    return '<tr data-ref="' + escapeHtml(doc.reference) + '"><td class="tname"><span class="fic" style="background:var(--warn-soft);color:#A9740A"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h6"/></svg></span>' + escapeHtml(doc.nom) + '</td><td>Import</td><td>' + escapeHtml(doc.uploadedByName || "—") + '</td><td><span class="b warn">' + escapeHtml(label) + '</span></td><td><a class="iconbtn" style="width:34px;height:34px" href="document-detail.html?ref=' + encodeURIComponent(doc.reference) + '" aria-label="Ouvrir le document"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7"/><circle cx="12" cy="12" r="3"/></svg></a></td></tr>';
  }
  function renderQueue(items) {
    var body = document.getElementById("queueBody");
    if (!body) return;
    if (!Array.isArray(items) || !items.length) {
      body.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:24px">Aucun document en attente d\'indexation.</td></tr>';
      return;
    }
    body.innerHTML = items.map(queueRow).join("");
  }
  function loadQueue() {
    var body = document.getElementById("queueBody");
    return api("GET", "/documents/queue").then(function (items) { renderQueue(items); return items; }).catch(function () {
      if (body) body.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:24px">Impossible de charger la file d\'attente.</td></tr>';
    });
  }
  function populateReferentielSelects() {
    var typeSel = document.getElementById("scanTypeCode");
    var domSel = document.getElementById("scanDomaine");
    if (!typeSel && !domSel) return;
    api("GET", "/referentiels").then(function (ref) {
      if (typeSel) {
        typeSel.innerHTML = '<option value="">Choisir un type…</option>' + (ref.typesDocuments || []).map(function (grp) {
          return '<optgroup label="' + escapeHtml(grp.categorie) + '">' + grp.options.map(function (o) { return '<option value="' + o.code + '">' + o.code + ' — ' + escapeHtml(o.label) + '</option>'; }).join("") + '</optgroup>';
        }).join("");
      }
      if (domSel) {
        domSel.innerHTML = '<option value="">Aucun (dossier déjà existant)</option>' + (ref.domaines || []).map(function (d) { return '<option value="' + d.code + '">' + d.code + ' — ' + escapeHtml(d.label) + '</option>'; }).join("");
      }
    });
  }
  function initDossierCombo() {
    var wrap = document.getElementById("scanDossierCombo");
    if (!wrap) return;
    var input = document.getElementById("scanDossierInput");
    var hidden = document.getElementById("scanDossierValue");
    var panel = document.getElementById("scanDossierPanel");
    var items = [];
    var activeIndex = -1;
    function label(item) { return item.reference + (item.client ? " — " + item.client : ""); }
    function statusClass(item) {
      return item.statut === "cloture" || item.statut === "clôturé" ? "grey" : (item.statut === "en_attente" ? "warn" : "green");
    }
    function openPanel() { panel.hidden = false; wrap.setAttribute("data-open", "true"); input.setAttribute("aria-expanded", "true"); }
    function closePanel() { panel.hidden = true; wrap.removeAttribute("data-open"); input.setAttribute("aria-expanded", "false"); activeIndex = -1; }
    function renderPanel(list) {
      if (!items.length) { panel.innerHTML = '<div class="combo-empty">Chargement des dossiers…</div>'; return; }
      if (!list.length) { panel.innerHTML = '<div class="combo-empty">Aucun dossier ne correspond à cette recherche.</div>'; return; }
      panel.innerHTML = list.map(function (item, i) {
        return '<div class="combo-opt" role="option" id="scanDossierOpt' + i + '" data-ref="' + escapeHtml(item.reference) + '" aria-selected="' + (hidden.value === item.reference ? "true" : "false") + '">' +
          '<div class="combo-opt-main"><span class="combo-opt-ref">' + escapeHtml(item.reference) + '</span><span class="combo-opt-client">' + escapeHtml(item.client || "Sans client") + '</span></div>' +
          '<div class="combo-opt-meta"><span>' + escapeHtml(item.objet || "—") + '</span><span class="b ' + statusClass(item) + '">' + escapeHtml(item.statutLabel || item.statut || "—") + '</span></div>' +
          '</div>';
      }).join("");
    }
    function filterItems(q) {
      q = (q || "").trim().toLowerCase();
      if (!q) return items;
      return items.filter(function (item) {
        return (item.reference || "").toLowerCase().indexOf(q) !== -1 ||
          (item.client || "").toLowerCase().indexOf(q) !== -1 ||
          (item.objet || "").toLowerCase().indexOf(q) !== -1;
      });
    }
    function updateList() { renderPanel(filterItems(input.value)); activeIndex = -1; }
    function selectItem(ref) {
      var found = items.find(function (item) { return item.reference === ref; });
      hidden.value = ref;
      input.value = found ? label(found) : ref;
      closePanel();
    }
    function highlight(opts) {
      opts.forEach(function (o, i) { o.classList.toggle("active", i === activeIndex); });
      if (opts[activeIndex]) opts[activeIndex].scrollIntoView({ block: "nearest" });
    }
    panel.innerHTML = '<div class="combo-empty">Chargement des dossiers…</div>';
    // Le texte visible et la valeur envoyée pouvaient diverger : Chrome
    // restaure le texte d'un champ au rechargement mais pas l'état interne,
    // et le lien « ajouter un document » d'une fiche dossier passe
    // ?dossier=REF sans que la page ne le lise. La page affichait alors un
    // dossier tout en refusant l'envoi (« Sélectionnez le dossier… »).
    function trouver(texte) {
      texte = (texte || "").trim().toLowerCase();
      if (!texte) return null;
      return items.find(function (item) {
        var ref = (item.reference || "").toLowerCase();
        return ref === texte || label(item).toLowerCase() === texte || texte.indexOf(ref + " \u2014 ") === 0;
      }) || null;
    }
    function resoudre() {
      if (hidden.value) return;
      var found = trouver(input.value);
      if (found) selectItem(found.reference);
    }
    var pret = api("GET", "/dossiers").then(function (list) { items = Array.isArray(list) ? list : []; }).catch(function () { items = []; }).then(function () {
      var demande = params.get("dossier");
      if (demande && !input.value) {
        var found = trouver(demande);
        if (found) selectItem(found.reference);
        else toast("Le dossier " + demande + " n'est pas accessible ou n'existe pas.", "err");
      }
      resoudre();
    });
    wrap.__resoudre = function () { return pret.then(function () { resoudre(); return hidden.value; }); };
    input.addEventListener("blur", function () { setTimeout(resoudre, 150); });
    input.addEventListener("focus", function () { updateList(); openPanel(); });
    input.addEventListener("click", function () { updateList(); openPanel(); });
    input.addEventListener("input", function () { hidden.value = ""; updateList(); openPanel(); });
    input.addEventListener("keydown", function (e) {
      var opts = panel.querySelectorAll(".combo-opt");
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (panel.hidden) { updateList(); openPanel(); opts = panel.querySelectorAll(".combo-opt"); }
        activeIndex = Math.min(activeIndex + 1, opts.length - 1);
        highlight(opts);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        highlight(opts);
      } else if (e.key === "Enter") {
        if (!panel.hidden && activeIndex >= 0 && opts[activeIndex]) { e.preventDefault(); selectItem(opts[activeIndex].getAttribute("data-ref")); }
      } else if (e.key === "Escape") {
        closePanel();
      }
    });
    panel.addEventListener("click", function (e) {
      var opt = e.target.closest(".combo-opt");
      if (opt) selectItem(opt.getAttribute("data-ref"));
    });
    document.addEventListener("click", function (e) { if (!wrap.contains(e.target)) closePanel(); });
  }
  function setupScan() {
    if (page.indexOf("scan") !== 0) return;
    if (!can("scan")) return deny("La numérisation n'est pas dans votre socle d'accès.");
    loadQueue();
    populateReferentielSelects();
    initDossierCombo();
    // Source : chaque choix change réellement les formats acceptés, le texte
    // d'aide et, pour l'import en lot, autorise plusieurs fichiers à la fois.
    var FORMATS_TOUS = "application/pdf,image/jpeg,image/png,image/tiff,.docx,.xlsx";
    var SOURCES = {
      scanner: { label: "Fichier issu du scanner : PDF ou image", accept: "application/pdf,image/jpeg,image/png,image/tiff", multiple: false,
        hint: "Numérisez avec le logiciel du scanner, enregistrez au format PDF, puis sélectionnez le fichier ici · 25 Mo maximum" },
      fichier: { label: "Fichier existant : PDF, image, Word (.docx) ou Excel (.xlsx)", accept: FORMATS_TOUS, multiple: false,
        hint: "Document déjà présent sur l'ordinateur (reçu par e-mail, clé USB, dossier partagé…) · 25 Mo maximum" },
      lot: { label: "Plusieurs fichiers : PDF, image, Word (.docx) ou Excel (.xlsx)", accept: FORMATS_TOUS, multiple: true,
        hint: "Sélectionnez plusieurs fichiers d'un coup (Ctrl + clic ou Maj + clic). Ils reçoivent tous le même type, le même dossier et le même niveau · 25 Mo par fichier, " + 50 + " fichiers au plus" }
    };
    var LOT_MAX = 50;
    var source = "scanner";
    var fichierInput = document.querySelector("[data-upload-file]");
    var compteur = document.getElementById("documentFileCount");
    function majCompteur() {
      if (!compteur || !fichierInput) return;
      var n = fichierInput.files ? fichierInput.files.length : 0;
      compteur.hidden = !(source === "lot" && n);
      compteur.textContent = n + (n > 1 ? " fichiers sélectionnés" : " fichier sélectionné") + (n > LOT_MAX ? " \u2014 maximum " + LOT_MAX + " par envoi" : "");
      compteur.style.color = n > LOT_MAX ? "var(--danger)" : "";
    }
    function choisirSource(cle) {
      var s = SOURCES[cle];
      if (!s || !fichierInput) return;
      source = cle;
      fichierInput.accept = s.accept;
      fichierInput.multiple = s.multiple;
      fichierInput.value = "";
      var label = document.getElementById("documentFileLabel");
      var hint = document.getElementById("documentFileHint");
      if (label) label.textContent = s.label;
      if (hint) hint.textContent = s.hint;
      document.querySelectorAll("#scanSource .opt").forEach(function (o) {
        var actif = o.getAttribute("data-source") === cle;
        o.classList.toggle("on", actif);
        o.setAttribute("aria-checked", actif ? "true" : "false");
        o.tabIndex = actif ? 0 : -1;
      });
      majCompteur();
    }
    var segSource = document.getElementById("scanSource");
    if (segSource) {
      segSource.addEventListener("click", function (e) {
        var opt = e.target.closest("[data-source]");
        if (opt) choisirSource(opt.getAttribute("data-source"));
      });
      segSource.addEventListener("keydown", function (e) {
        var opt = e.target.closest("[data-source]");
        if (!opt) return;
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choisirSource(opt.getAttribute("data-source")); }
      });
    }
    if (fichierInput) fichierInput.addEventListener("change", majCompteur);
    var btn = Array.from(document.querySelectorAll("button.btn.pri")).find(function (b) { return /contrôle/i.test(b.textContent); });
    if (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var data = collectFields();
        var input = fichierInput;
        var fichiers = input && input.files ? Array.from(input.files) : [];
        var file = fichiers[0];
        if (!file) return toast(source === "lot" ? "Choisissez les fichiers à archiver." : "Choisissez le fichier à archiver.", "err");
        if (fichiers.length > LOT_MAX) return toast("Maximum " + LOT_MAX + " fichiers par envoi : répartissez l'import en plusieurs fois.", "err");
        if (!data.type_code) return toast("Choisissez un type de document dans le référentiel.", "err");
        // La référence choisie vit dans le champ caché : collectFields() ne
        // lit que les champs visibles et renvoyait le texte affiché sous une
        // autre clé, si bien que `data.dossier` restait toujours vide.
        var champDossier = document.getElementById("scanDossierValue");
        data.dossier = champDossier ? champDossier.value : "";
        var apresResolution = btn.__apresResolution;
        btn.__apresResolution = false;
        if (!data.dossier) {
          // Dernier recours, UNE seule fois : le texte affiché correspond
          // peut-être à un dossier existant sans que la valeur ait été posée.
          var combo = document.getElementById("scanDossierCombo");
          if (!apresResolution && combo && combo.__resoudre) {
            combo.__resoudre().then(function (ref) {
              if (!ref) return toast("Sélectionnez le dossier existant auquel rattacher ce document.", "err");
              btn.__apresResolution = true;
              btn.click();
            });
            return;
          }
          return toast("Sélectionnez le dossier existant auquel rattacher ce document.", "err");
        }
        if (/confidentiel/i.test(data.niveau_de_confidentialite || "") && !can("validateActs")) {
          return deny("Seul le notaire archive un acte confidentiel.");
        }
        function envoyer(f) {
          var form = new FormData();
          form.append("fichier", f);
          form.append("type_code", data.type_code);
          form.append("niveau", data.niveau_de_confidentialite || "Standard");
          if (data.dossier) form.append("dossier", data.dossier);
          // Une date de validité permet à la GED d'alerter avant expiration.
          if (data.valid_until) form.append("valid_until", data.valid_until);
          return apiMultipart("/documents/upload", form);
        }
        var libelleBouton = btn.innerHTML;
        btn.disabled = true;
        // Envoi un par un : l'antivirus et le chiffrement traitent chaque
        // fichier, et un échec n'empêche pas les suivants.
        var reussis = [], echecs = [];
        var chaine = fichiers.reduce(function (p, f, i) {
          return p.then(function () {
            if (fichiers.length > 1) btn.textContent = "Envoi " + (i + 1) + " / " + fichiers.length + "\u2026";
            return envoyer(f).then(function (doc) { reussis.push(doc); }, function () { echecs.push(f.name); });
          });
        }, Promise.resolve());
        chaine.then(function () {
          if (!reussis.length) throw new Error("aucun");
          if (fichiers.length === 1) {
            toast("Document envoyé au contrôle : " + reussis[0].reference, "ok");
            (reussis[0].warnings || []).forEach(function (w, i) { setTimeout(function () { toast(w, "ok"); }, 900 * (i + 1)); });
          } else {
            toast(reussis.length + " document(s) envoyé(s) au contrôle" + (echecs.length ? " \u00b7 " + echecs.length + " refusé(s) : " + echecs.join(", ") : "."), echecs.length ? "err" : "ok");
          }
          var validite = document.getElementById("scanValidUntil");
          if (validite) validite.value = "";
          if (input) input.value = "";
          var dossierInput = document.getElementById("scanDossierInput");
          var dossierValue = document.getElementById("scanDossierValue");
          if (dossierInput) dossierInput.value = "";
          if (dossierValue) dossierValue.value = "";
          majCompteur();
          loadQueue();
        }).catch(function () {}).finally(function () {
          btn.disabled = false;
          btn.innerHTML = libelleBouton;
        });
      });
    }
  }

  function dossierRow(item) {
    return '<tr><td class="tname"><span class="fic" style="background:var(--primary-soft);color:var(--primary-600)"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg></span><span>' + escapeHtml(item.client || item.nom || "Sans client") + '<br><span class="ls" style="font-weight:400">' + escapeHtml(item.reference || "—") + '</span></span></td>' +
      '<td>' + escapeHtml((item.domaineLabel ? item.domaineLabel + " · " : "") + (item.objet || "—")) + '</td><td>' + escapeHtml(item.notaire || "—") + '</td><td>' + escapeHtml(item.documentsCount != null ? item.documentsCount + " documents" : "—") + '</td>' +
      '<td><span class="b ' + (item.statut === "cloture" || item.statut === "clôturé" ? "grey" : (item.statut === "en_attente" ? "warn" : "green")) + '">' + escapeHtml(item.statutLabel || item.statut || "—") + '</span></td>' +
      '<td><a class="iconbtn" style="width:34px;height:34px" href="dossier-detail.html?ref=' + encodeURIComponent(item.reference) + '"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7"/><circle cx="12" cy="12" r="3"/></svg></a></td></tr>';
  }
  function renderDossiers(items) {
    var body = document.getElementById("dossiersBody");
    if (!body) return;
    if (!Array.isArray(items) || !items.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">Aucun dossier pour le moment.</td></tr>';
      return;
    }
    body.innerHTML = items.map(dossierRow).join("");
  }
  function loadDossiers() {
    var body = document.getElementById("dossiersBody");
    return api("GET", "/dossiers").then(function (items) { renderDossiers(items); return items; }).catch(function () {
      if (body) body.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">Impossible de charger les dossiers.</td></tr>';
    });
  }
  function setupDossiers() {
    if (page.indexOf("dossiers") !== 0) return;
    if (!can("dossiers")) return;
    loadDossiers();
    var add = Array.from(document.querySelectorAll(".btn.pri")).find(function (b) { return /nouveau dossier/i.test(b.textContent); });
    if (add) {
      add.addEventListener("click", function (e) {
        e.preventDefault();
        if (!can("createDossier")) return deny("La création de dossier est réservée au notaire.");
        api("GET", "/referentiels").then(function (ref) {
          var domOptions = (ref.domaines || []).map(function (d) { return '<option value="' + d.code + '">' + d.code + ' — ' + escapeHtml(d.label) + '</option>'; }).join("");
          openModal({
            title: "Nouveau dossier",
            ok: "Créer",
            bodyHtml:
              '<div class="field"><label>Client</label><input class="inp" name="client" placeholder="Nom du client"></div>' +
              '<div class="field"><label>Domaine</label><select class="sel" name="domaine"><option value="">Choisir un domaine…</option>' + domOptions + '</select><div class="hint">Sert à générer la référence du dossier (DOM-AAAA-NNNNN).</div></div>' +
              '<div class="field"><label>Objet</label><input class="inp" name="objet" placeholder="Vente, donation…"></div>',
            onOk: function (payload) {
              if (!payload.domaine) { toast("Le domaine est obligatoire.", "err"); throw new Error("domaine"); }
              return api("POST", "/dossiers", payload).then(function (dossier) { toast("Dossier créé : " + dossier.reference, "ok"); loadDossiers(); });
            }
          });
        });
      });
    }
  }

  function clientRow(item) {
    var kindLabel = item.kind === "personne_morale" ? "Personne morale" : "Personne physique";
    var meta = [kindLabel];
    if (item.email) meta.push(item.email);
    if (item.telephone) meta.push(item.telephone);
    return '<div class="lrow" style="cursor:default"><span class="li" style="background:var(--primary-soft);color:var(--primary-600)">' + escapeHtml(initials(item.nom)) + '</span>' +
      '<div><div class="lt">' + escapeHtml(item.nom) + '</div><div class="ls">' + escapeHtml(meta.join(" \u00b7 ")) + '</div></div>' +
      '<div class="lx"><span class="b grey">' + (item.dossiersCount || 0) + (item.dossiersCount === 1 ? " dossier" : " dossiers") + '</span></div></div>';
  }
  function renderClients(items) {
    var host = document.getElementById("clientsList");
    if (!host) return;
    if (!Array.isArray(items) || !items.length) {
      host.innerHTML = '<div class="empty-mini">Aucun client pour le moment.</div>';
      return;
    }
    host.innerHTML = items.map(clientRow).join("");
  }
  var __clients = [];
  function loadClients() {
    var host = document.getElementById("clientsList");
    return api("GET", "/clients").then(function (items) {
      __clients = Array.isArray(items) ? items : [];
      renderClients(__clients);
      return items;
    }).catch(function () {
      if (host) host.innerHTML = '<div class="empty-mini">Impossible de charger les clients.</div>';
    });
  }
  function setupClients() {
    if (page.indexOf("clients") !== 0) return;
    loadClients();
    var search = document.getElementById("clientSearch");
    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.trim().toLowerCase();
        if (!q) return renderClients(__clients);
        renderClients(__clients.filter(function (c) {
          return [c.nom, c.reference, c.email, c.telephone].some(function (v) { return v && String(v).toLowerCase().indexOf(q) >= 0; });
        }));
      });
    }
  }

  var TASK_STATUS_LABEL = { "ouverte": "Ouverte", "en_cours": "En cours", "termin\u00e9e": "Termin\u00e9e", "annul\u00e9e": "Annul\u00e9e" };
  function taskRow(item) {
    var badgeCls = item.status === "termin\u00e9e" ? "green" : (item.status === "annul\u00e9e" ? "grey" : ((item.priority === "urgente" || item.priority === "haute") ? "warn" : "grey"));
    var meta = [];
    if (item.dossier) meta.push(item.dossier);
    if (item.assignedTo) meta.push(item.assignedTo);
    if (item.dueAt) meta.push("\u00e9ch\u00e9ance " + docDate(item.dueAt));
    return '<div class="lrow" style="cursor:default"><span class="li" style="background:var(--primary-soft);color:var(--primary-600)"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="m8 12 2.5 2.5L16 9"/></svg></span>' +
      '<div><div class="lt">' + escapeHtml(item.title) + '</div><div class="ls">' + escapeHtml(meta.join(" \u00b7 ")) + '</div></div>' +
      '<div class="lx"><span class="b ' + badgeCls + '">' + escapeHtml(TASK_STATUS_LABEL[item.status] || item.status) + '</span></div></div>';
  }
  function renderTasks(items) {
    var host = document.getElementById("tasksList");
    if (!host) return;
    if (!Array.isArray(items) || !items.length) {
      host.innerHTML = '<div class="empty-mini">Aucune t\u00e2che pour le moment.</div>';
      return;
    }
    host.innerHTML = items.map(taskRow).join("");
  }
  var __tasks = [];
  function loadTaches() {
    var host = document.getElementById("tasksList");
    return api("GET", "/tasks").then(function (items) {
      __tasks = Array.isArray(items) ? items : [];
      renderTasks(__tasks);
      return items;
    }).catch(function () {
      if (host) host.innerHTML = '<div class="empty-mini">Impossible de charger les t\u00e2ches.</div>';
    });
  }
  function setupTaches() {
    if (page.indexOf("taches") !== 0) return;
    loadTaches();
    var search = document.getElementById("taskSearch");
    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.trim().toLowerCase();
        if (!q) return renderTasks(__tasks);
        renderTasks(__tasks.filter(function (t) {
          return [t.title, t.dossier, t.assignedTo, t.assignedBy].some(function (v) { return v && String(v).toLowerCase().indexOf(q) >= 0; });
        }));
      });
    }
  }

  function userRow(item) {
    var initialsTxt = initials(item.name);
    var scanBtn = item.role === "collaborateur"
      ? '<button type="button" class="iconbtn" style="width:34px;height:34px" data-toggle-scan data-id="' + item.id + '" data-can-scan="' + (item.canScan ? "1" : "0") + '" title="' + (item.canScan ? "Révoquer la numérisation" : "Autoriser la numérisation") + '" aria-label="' + (item.canScan ? "Révoquer la numérisation" : "Autoriser la numérisation") + '"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"' + (item.canScan ? ' style="color:var(--green)"' : '') + '><path d="M4 8V6a2 2 0 0 1 2-2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v2"/><path d="M20 16v2a2 2 0 0 1-2 2h-2"/><path d="M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M4 12h16"/></svg></button>'
      : "";
    var session = getSession();
    var isSelf = !!(session && session.email && item.email && String(session.email).toLowerCase() === String(item.email).toLowerCase());
    var roleBtn = isSelf ? "" : (
      '<button type="button" class="iconbtn" style="width:34px;height:34px" data-change-role data-id="' + item.id +
      '" data-role="' + escapeHtml(item.role) + '" data-name="' + escapeHtml(item.name) +
      '" title="Changer le rôle" aria-label="Changer le rôle de ' + escapeHtml(item.name) + '">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M8 21H3v-5"/><path d="M21 3l-7.5 7.5"/><path d="M3 21l7.5-7.5"/></svg></button>'
    );
    var activeBtn = isSelf ? "" : (
      item.isActive
        ? '<button type="button" class="iconbtn" style="width:34px;height:34px;color:var(--danger)" data-toggle-active data-id="' + item.id + '" data-is-active="1" data-name="' + escapeHtml(item.name) + '" title="Suspendre l\u2019accès" aria-label="Suspendre l\u2019accès"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="17" y1="8" x2="22" y2="13"/><line x1="22" y1="8" x2="17" y2="13"/></svg></button>'
        : '<button type="button" class="iconbtn" style="width:34px;height:34px;color:var(--green)" data-toggle-active data-id="' + item.id + '" data-is-active="0" data-name="' + escapeHtml(item.name) + '" title="Réactiver l\u2019accès" aria-label="Réactiver l\u2019accès"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><polyline points="16 11 18 13 22 9"/></svg></button>'
    );
    return '<tr><td class="tname"><span class="av" style="background:var(--primary-soft);color:var(--primary-600);border-radius:11px;width:34px;height:34px">' + escapeHtml(initialsTxt) + '</span>' + escapeHtml(item.name) + '</td>' +
      '<td>' + escapeHtml(item.roleLabel || item.role) + '</td><td><span class="b ' + (item.isActive ? "green" : "grey") + '">' + (item.isActive ? "Actif" : "Accès suspendu") + '</span></td>' +
      '<td style="display:flex;gap:6px"><a class="iconbtn" style="width:34px;height:34px" href="permissions.html"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/><path d="m9 12 2 2 4-4"/></svg></a>' + scanBtn + roleBtn + activeBtn + '</td></tr>';
  }
  function renderUsers(items) {
    var body = document.getElementById("usersBody");
    if (!body) return;
    if (!Array.isArray(items) || !items.length) {
      body.innerHTML = '<tr><td colspan="4" class="muted" style="text-align:center;padding:24px">Aucun utilisateur pour le moment.</td></tr>';
      return;
    }
    body.innerHTML = items.map(userRow).join("");
  }
  function loadUsers() {
    var body = document.getElementById("usersBody");
    return api("GET", "/users").then(function (items) { renderUsers(items); return items; }).catch(function () {
      if (body) body.innerHTML = '<tr><td colspan="4" class="muted" style="text-align:center;padding:24px">Impossible de charger les utilisateurs.</td></tr>';
    });
  }
  function setupUsers() {
    if (page.indexOf("utilisateurs") !== 0) return;
    if (!can("users")) return deny("La gestion des comptes est réservée au notaire.");
    loadUsers();
    var body = document.getElementById("usersBody");
    if (body) {
      body.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-toggle-scan]");
        if (!btn) return;
        e.preventDefault();
        var id = btn.getAttribute("data-id");
        var next = btn.getAttribute("data-can-scan") !== "1";
        api("PATCH", "/users", { id: id, canScan: next }).then(function () {
          toast(next ? "Numérisation autorisée pour ce collaborateur." : "Numérisation révoquée pour ce collaborateur.", "ok");
          loadUsers();
        });
      });
      body.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-change-role]");
        if (!btn) return;
        e.preventDefault();
        var id = btn.getAttribute("data-id");
        var nom = btn.getAttribute("data-name") || "cet utilisateur";
        var actuel = btn.getAttribute("data-role") || "";
        var choix = [
          { valeur: "collaborateur", libelle: "Collaborateur" },
          { valeur: "clerc", libelle: "Clerc principal" },
          { valeur: "admin", libelle: "Notaire · Admin" }
        ];
        var options = choix.map(function (c) {
          return '<option value="' + c.valeur + '"' + (c.valeur === actuel ? " selected" : "") + ">" + c.libelle + "</option>";
        }).join("");
        openModal({
          title: "Changer le rôle de " + nom,
          ok: "Changer le rôle",
          bodyHtml:
            "<p>Le rôle commande l’espace de travail, les droits et l’exigence de second facteur. " +
            "Les sessions en cours de " + escapeHtml(nom) + " seront immédiatement coupées : " +
            "la prochaine connexion appliquera le nouveau rôle.</p>" +
            '<div class="field"><label>Nouveau rôle</label><select class="sel" name="role">' + options + "</select></div>" +
            '<div class="field"><label>Motif (promotion, réorganisation…)</label><textarea class="ta" name="motif" placeholder="Consigné au journal d’audit"></textarea></div>',
          onOk: function (payload) {
            if (!payload.role || payload.role === actuel) {
              toast("Aucun changement de rôle demandé.", "err");
              return;
            }
            if (!String(payload.motif || "").trim()) {
              toast("Le motif est obligatoire.", "err");
              return Promise.reject(new Error("motif manquant"));
            }
            return api("PATCH", "/users", { id: id, role: payload.role, motif: payload.motif }).then(function (res) {
              toast(nom + " est désormais " + (res.roleLabel || payload.role) + ". Ses sessions ont été coupées.", "ok");
              loadUsers();
            });
          }
        });
      });
      body.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-toggle-active]");
        if (!btn) return;
        e.preventDefault();
        var id = btn.getAttribute("data-id");
        var name = btn.getAttribute("data-name") || "cet utilisateur";
        var isActive = btn.getAttribute("data-is-active") === "1";
        if (isActive) {
          openModal({
            title: "Suspendre l\u2019accès de " + name,
            ok: "Suspendre l\u2019accès",
            bodyHtml:
              "<p>Le compte est désactivé immédiatement et toutes ses sessions en cours sont révoquées. Ses documents, dossiers et actions passées restent conservés pour l\u2019audit — rien n\u2019est supprimé.</p>" +
              '<div class="field"><label>Motif (fin de collaboration, mutation…)</label><textarea class="ta" name="departureReason" placeholder="Motif communiqué en interne"></textarea></div>',
            onOk: function (payload) {
              return api("PATCH", "/users", { id: id, isActive: false, departureReason: payload.departureReason || "" }).then(function () {
                toast("Accès suspendu pour " + name + ".", "ok");
                loadUsers();
              });
            }
          });
        } else {
          openModal({
            title: "Réactiver l\u2019accès de " + name,
            ok: "Réactiver",
            bodyHtml: "<p>" + name + " pourra de nouveau se connecter avec ses identifiants existants.</p>",
            onOk: function () {
              return api("PATCH", "/users", { id: id, isActive: true }).then(function () {
                toast("Accès réactivé pour " + name + ".", "ok");
                loadUsers();
              });
            }
          });
        }
      });
    }
    var add = Array.from(document.querySelectorAll(".btn.pri")).find(function (b) { return /ajouter/i.test(b.textContent); });
    if (add) {
      add.addEventListener("click", function (e) {
        e.preventDefault();
        openModal({
          title: "Créer un compte",
          ok: "Créer",
          bodyHtml:
            '<div class="field"><label>Nom complet</label><input class="inp" name="nom"></div>' +
            '<div class="field"><label>E-mail</label><input class="inp" name="email" type="email"></div>' +
            '<div class="field"><label>Rôle</label><select class="sel" name="role"><option value="collaborateur">Collaborateur</option><option value="clerc">Clerc principal</option></select></div>',
          onOk: function (payload) {
            return api("POST", "/users", payload).then(function () {
              toast("Compte créé. Identifiants transmis à l'étude.", "ok");
              loadUsers();
            });
          }
        });
      });
    }
  }

  function permissionRow(item) {
    return '<tr data-id="' + item.id + '"><td class="tname">' + escapeHtml(item.beneficiaire) + '</td>' +
      '<td>' + escapeHtml(item.cibleReference || item.cible || "—") + '</td><td>' + escapeHtml(item.accessLevelLabel || item.accessLevel) + '</td>' +
      '<td>' + (item.createdAt ? docDate(item.createdAt) : "—") + '</td><td><span class="b green">Accordé</span></td>' +
      '<td><a class="iconbtn" style="width:34px;height:34px" href="#"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16"/><path d="M6 7v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7"/><path d="M9 7V4h6v3"/></svg></a></td></tr>';
  }
  function renderPermissions(items) {
    var body = document.getElementById("permissionsBody");
    if (!body) return;
    if (!Array.isArray(items) || !items.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">Aucun accès attribué.</td></tr>';
      return;
    }
    body.innerHTML = items.map(permissionRow).join("");
  }
  function loadPermissions() {
    var body = document.getElementById("permissionsBody");
    return api("GET", "/permissions").then(function (items) { renderPermissions(items); return items; }).catch(function () {
      if (body) body.innerHTML = '<tr><td colspan="6" class="muted" style="text-align:center;padding:24px">Impossible de charger les accès.</td></tr>';
    });
  }
  function accessRequestRow(item) {
    return '<tr data-id="' + item.id + '"><td class="tname">' + escapeHtml(item.demandeur) + '</td>' +
      '<td>' + escapeHtml(item.cibleReference || item.cible || "—") + '</td>' +
      '<td>' + escapeHtml(item.motif) + '</td><td>' + (item.createdAt ? docDate(item.createdAt) : "—") + '</td>' +
      '<td style="display:flex;gap:8px"><button class="btn pri" style="padding:8px 14px" data-decision="accept" type="button">Accepter</button><button class="btn" style="padding:8px 14px" data-decision="refuse" type="button">Refuser</button></td></tr>';
  }
  function renderAccessRequests(items) {
    var body = document.getElementById("accessRequestsBody");
    if (!body) return;
    var pending = (items || []).filter(function (item) { return item.status === "en_attente"; });
    body.innerHTML = pending.length ? pending.map(accessRequestRow).join("") : '<tr><td colspan="5" class="muted" style="text-align:center;padding:24px">Aucune demande en attente.</td></tr>';
  }
  function loadAccessRequests() {
    var body = document.getElementById("accessRequestsBody");
    return api("GET", "/access-requests").then(function (items) { renderAccessRequests(items); return items; }).catch(function () {
      if (body) body.innerHTML = '<tr><td colspan="5" class="muted" style="text-align:center;padding:24px">Impossible de charger les demandes.</td></tr>';
    });
  }
  function setupAccessRequests() {
    var body = document.getElementById("accessRequestsBody");
    if (!body) return;
    loadAccessRequests();
    body.addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-decision]");
      if (!btn) return;
      var row = btn.closest("tr");
      var id = row && row.getAttribute("data-id");
      var decision = btn.getAttribute("data-decision");
      api("PATCH", "/access-requests", { id: id, decision: decision }).then(function () {
        toast(decision === "accept" ? "Accès accordé." : "Demande refusée.", "ok");
        loadAccessRequests();
        if (decision === "accept") loadPermissions();
      });
    });
  }

  function setupPermissions() {
    if (page.indexOf("permissions") !== 0) return;
    if (!can("permissions")) return deny("L'attribution des accès est réservée au notaire.");
    loadPermissions();
    setupAccessRequests();
    // Populate real recipients and targets instead of the previous mock rows.
    api("GET", "/users").then(function (users) {
      var select = document.querySelector('[name="email"]');
      if (select) select.innerHTML = '<option value="">Sélectionnez un utilisateur</option>' + users.filter(function (u) { return u.isActive && u.role !== "admin"; }).map(function (u) { return '<option value="' + escapeHtml(u.email) + '">' + escapeHtml(u.name) + ' — ' + escapeHtml(u.roleLabel) + '</option>'; }).join("");
    });
    Promise.all([api("GET", "/dossiers"), api("GET", "/documents")]).then(function (data) {
      var list = document.getElementById("permissionTargets");
      if (!list) return;
      list.innerHTML = data[0].map(function (d) { return '<option value="' + escapeHtml(d.reference) + '">' + escapeHtml(d.client || d.nom) + ' — dossier</option>'; }).join("") + data[1].map(function (d) { return '<option value="' + escapeHtml(d.reference) + '">' + escapeHtml(d.nom) + ' — document</option>'; }).join("");
    });
    var btn = Array.from(document.querySelectorAll("button.btn.pri")).find(function (b) { return /ouvrir l'accès/i.test(b.textContent); });
    if (btn) {
      btn.addEventListener("click", function () {
        var data = collectFields();
        if (!data.email || !data.target || !data.motif) {
          toast("Bénéficiaire, cible et motif sont obligatoires.", "err");
          return;
        }
        if (!data.motif_obligatoire && !data.motif) {
          toast("Le motif est obligatoire.", "err");
          return;
        }
        var payload = { email: data.email, accessLevel: data.accessLevel, motif: data.motif };
        payload[data.scope === "document" ? "ref" : "dossier"] = data.target;
        api("POST", "/permissions", payload).then(function () { toast("Accès attribué.", "ok"); loadPermissions(); });
      });
    }
    var body = document.getElementById("permissionsBody");
    if (body) {
      body.addEventListener("click", function (e) {
        var a = e.target.closest(".iconbtn");
        if (!a) return;
        e.preventDefault();
        var row = a.closest("tr");
        var id = row && row.getAttribute("data-id");
        var who = row && row.querySelector(".tname");
        openModal({
          title: "Révoquer l'accès",
          text: "Cette révocation est journalisée.",
          ok: "Révoquer",
          onOk: function () {
            return api("DELETE", "/permissions", id ? { id: id } : { beneficiaire: who ? who.textContent.trim() : "" }).then(function () {
              toast("Accès révoqué.", "ok");
              loadPermissions();
            });
          }
        });
      });
    }
  }

  function setupConfig() {
    if (page.indexOf("configuration") !== 0) return;
    if (!can("config")) return deny("La configuration du cabinet est réservée au notaire.");
    document.querySelectorAll(".pill-tabs .pt").forEach(function (pt) {
      pt.addEventListener("click", function () {
        pt.parentElement.querySelectorAll(".pt").forEach(function (p) { p.classList.remove("on"); });
        pt.classList.add("on");
        var target = pt.getAttribute("data-tab");
        if (target) {
          document.querySelectorAll(".cfg-panel").forEach(function (panel) {
            panel.classList.toggle("on", panel.getAttribute("data-tab") === target);
          });
        }
      });
    });
    api("GET", "/settings").then(function (settings) {
      var pra = settings.praPca || {};
      var fields = { cabinet_name: settings.cabinet_name, city: settings.city, codification_policy: settings.codification_policy, storage_mode: settings.storage_mode,
        rtoMinutes: pra.rtoMinutes, rpoMinutes: pra.rpoMinutes };
      Object.keys(fields).forEach(function (key) { var el = document.querySelector('[name="' + key + '"]'); if (el && fields[key] != null) el.value = fields[key]; });
    });
    Array.from(document.querySelectorAll("button.btn.pri")).filter(function (b) { return /enregistrer/i.test(b.textContent); }).forEach(function (btn) {
      btn.addEventListener("click", function () {
        var donnees = collectFields();
        // RTO/RPO : le serveur attend un objet `praPca` ; envoyés à plat, ils
        // n'étaient jamais enregistrés.
        if (donnees.rtoMinutes || donnees.rpoMinutes) {
          donnees.praPca = { rtoMinutes: Number(donnees.rtoMinutes) || 240, rpoMinutes: Number(donnees.rpoMinutes) || 60,
            recoveryOrder: ["database", "keys", "documents", "rights", "audit"] };
        }
        api("PUT", "/settings", donnees).then(function () { toast("Configuration enregistrée.", "ok"); })
          .catch(function (err) { toast(err.message || "Enregistrement impossible.", "err"); });
      });
    });
    setupModelesChecklist();
  }

  /* Modèles de checklist par type d'affaire (API /checklist-templates). */
  function setupModelesChecklist() {
    var liste = document.getElementById("modelesListe");
    if (!liste) return;
    var selDomaine = document.getElementById("modeleDomaine");
    var selType = document.getElementById("modeleType");
    var compte = document.getElementById("modelesCompte");
    var libelles = {};
    function chargerModeles() {
      return api("GET", "/checklist-templates?domaine=" + encodeURIComponent(selDomaine.value)).then(function (modeles) {
        compte.textContent = modeles.length + " pièce(s)";
        liste.innerHTML = modeles.length ? modeles.map(function (m) {
          return '<tr data-id="' + m.id + '"><td><b>' + escapeHtml(m.label) + '</b></td>' +
            '<td>' + escapeHtml(m.typeCode ? (libelles[m.typeCode] || m.typeCode) : "—") + '</td>' +
            '<td><input type="checkbox" data-champ="required"' + (m.required ? " checked" : "") + ' aria-label="Obligatoire"></td>' +
            '<td><input type="number" min="0" max="365" data-champ="reminderDays" value="' + (m.reminderDays == null ? "" : m.reminderDays) + '" placeholder="défaut" style="width:84px" aria-label="Délai de relance en jours"></td>' +
            '<td><input type="checkbox" data-champ="active"' + (m.active ? " checked" : "") + ' aria-label="Active"></td>' +
            '<td><span class="muted" style="font-size:11.5px" data-etat></span></td></tr>';
        }).join("") : '<tr><td colspan="6" class="muted" style="text-align:center;padding:18px">Aucune pièce pour ce type d\'affaire. Ajoutez-en ci-dessous ou chargez les modèles proposés.</td></tr>';
      }).catch(function (err) { liste.innerHTML = '<tr><td colspan="6" class="muted">' + escapeHtml(err.message || "Chargement impossible.") + "</td></tr>"; });
    }
    api("GET", "/referentiels").then(function (refs) {
      selDomaine.innerHTML = (refs.domaines || []).map(function (d) { return '<option value="' + escapeHtml(d.code) + '">' + escapeHtml(d.label) + "</option>"; }).join("");
      selType.innerHTML = '<option value="">— Aucun (rattachement manuel)</option>' + (refs.typesDocuments || []).map(function (g) {
        return '<optgroup label="' + escapeHtml(g.categorie) + '">' + g.options.map(function (o) {
          libelles[o.code] = o.label;
          return '<option value="' + escapeHtml(o.code) + '">' + escapeHtml(o.label) + "</option>";
        }).join("") + "</optgroup>";
      }).join("");
      selDomaine.value = "VEN";
      chargerModeles();
    });
    selDomaine.addEventListener("change", chargerModeles);
    // Modification en ligne : chaque changement est enregistré (et journalisé).
    liste.addEventListener("change", function (e) {
      var champ = e.target.getAttribute("data-champ");
      var ligne = e.target.closest("tr[data-id]");
      if (!champ || !ligne) return;
      var corps = { id: Number(ligne.getAttribute("data-id")) };
      corps[champ] = e.target.type === "checkbox" ? e.target.checked : (e.target.value === "" ? null : Number(e.target.value));
      var etat = ligne.querySelector("[data-etat]");
      api("PATCH", "/checklist-templates", corps).then(function () { if (etat) etat.textContent = "Enregistré"; })
        .catch(function (err) { toast(err.message || "Modification impossible.", "err"); chargerModeles(); });
    });
    document.getElementById("modeleAjouter").addEventListener("click", function () {
      var libelle = document.getElementById("modeleLibelle").value.trim();
      if (!libelle) return toast("Indiquez le nom de la pièce attendue.", "err");
      api("POST", "/checklist-templates", { domaine: selDomaine.value, label: libelle, typeCode: selType.value,
        required: document.getElementById("modeleRequis").checked }).then(function () {
        document.getElementById("modeleLibelle").value = "";
        toast("Pièce ajoutée au modèle.", "ok");
        chargerModeles();
      }).catch(function (err) { toast(err.message || "Ajout impossible.", "err"); });
    });
    document.getElementById("modelesProposes").addEventListener("click", function () {
      openModal({
        title: "Charger les modèles proposés", ok: "Charger",
        text: "Ajoute une liste de pièces proposée pour chaque type d'affaire (vente, succession, donation, société…). Vos ajustements existants ne sont jamais écrasés. Ces listes sont une proposition : relisez-les et adaptez-les à la pratique de l'étude.",
        onOk: function () {
          return api("POST", "/checklist-templates/proposes", {}).then(function (res) {
            toast(res.ajoutes ? res.ajoutes + " pièce(s) ajoutée(s) aux modèles." : "Les modèles proposés étaient déjà chargés.", "ok");
            return chargerModeles();
          });
        }
      });
    });
  }

  function setupBackup() {
    if (page.indexOf("sauvegarde") !== 0) return;
    if (!can("backup")) return deny("La sauvegarde est réservée au notaire.");
    function formatBytes(value) {
      var bytes = Number(value) || 0;
      if (bytes < 1024) return bytes + " o";
      var units = ["Ko", "Mo", "Go", "To"], index = -1;
      do { bytes /= 1024; index += 1; } while (bytes >= 1024 && index < units.length - 1);
      return bytes.toLocaleString("fr-FR", { maximumFractionDigits: 2 }) + " " + units[index];
    }
    function stamp(value) { return value ? new Date(value).toLocaleString("fr-FR", { dateStyle: "medium", timeStyle: "short" }) : "—"; }
    function renderBackup(data) {
      var local = data.local || {}, cloud = data.cloud || {}, test = data.lastRestoreTest, lastBackup = data.lastBackup;
      var localUsed = document.getElementById("backupLocalUsed");
      var localNote = document.getElementById("backupLocalNote");
      var localStatus = document.getElementById("backupLocalStatus");
      if (localUsed) localUsed.textContent = formatBytes(local.usedBytes);
      if (localNote) localNote.textContent = (local.documentCount || 0) + " document" + ((local.documentCount || 0) > 1 ? "s" : "") + " chiffré" + ((local.documentCount || 0) > 1 ? "s" : "") + " sur le serveur de l'étude";
      if (localStatus) localStatus.textContent = "Stockage local disponible";
      var cloudUsed = document.getElementById("backupCloudUsed");
      var cloudNote = document.getElementById("backupCloudNote");
      var cloudStatus = document.getElementById("backupCloudStatus");
      if (cloudUsed) cloudUsed.textContent = cloud.configured ? "Configurée" : "Non configurée";
      if (cloudNote) cloudNote.textContent = cloud.configured ? "Copie hors site prête pour les sauvegardes" : "Aucun stockage cloud S3 n'est complètement configuré";
      if (cloudStatus) cloudStatus.textContent = cloud.configured ? "Stockage cloud configuré" : "Action requise : configurer le stockage cloud";
      var last = document.getElementById("backupRestoreLast");
      if (last) last.textContent = test ? stamp(test.createdAt) + " — " + (test.status === "success" ? "réussi" : "échoué") : "Aucun test exécuté";
      var drill = document.getElementById("backupDrillLast");
      if (drill) drill.textContent = data.lastDrill ? stamp(data.lastDrill.createdAt) + " — " + (data.lastDrill.status === "success" ? "réussi" : "échoué") : "Pas encore exécuté";
      var fresh = document.getElementById("backupFreshness");
      if (fresh && data.freshness) {
        fresh.textContent = data.freshness.ageHours === null ? "Aucune sauvegarde réussie"
          : (data.freshness.ok ? "À jour" : "En retard") + " — il y a " + data.freshness.ageHours + " h (tolérance " + data.freshness.limitHours + " h)";
        fresh.style.color = data.freshness.ok ? "" : "var(--danger)";
      }
      var escrow = document.getElementById("backupEscrow");
      if (escrow && data.keyEscrow) {
        var manquantes = data.keyEscrow.missingKeyIds || [];
        escrow.textContent = manquantes.length ? "NON — exportez le paquet de récupération (Configuration)" : "Oui";
        escrow.style.color = manquantes.length ? "var(--danger)" : "";
      }
      var KINDS = { backup: "Sauvegarde complète", restore_test: "Test de restauration", restore_drill: "Exercice PRA" };
      var history = document.getElementById("backupHistoryBody");
      if (history) history.innerHTML = (data.history || []).length ? data.history.map(function (run) {
        var good = run.status === "success";
        var kind = (KINDS[run.kind] || "Contrôle") + (run.prunedAt ? " (retirée par la rotation)" : "");
        return "<tr><td>" + escapeHtml(stamp(run.createdAt)) + "</td><td>" + escapeHtml(kind) + "</td><td>" + escapeHtml(run.kind === "backup" ? (run.checkedDocuments || 0) + " document(s)" : formatBytes(run.checkedBytes)) + "</td><td><span class=\"b " + (good ? "green" : "red") + "\" title=\"" + escapeHtml(run.message || "") + "\">" + (good ? "Réussie" : "Échouée") + "</span></td></tr>";
      }).join("") : '<tr><td colspan="4" class="muted" style="text-align:center;padding:24px">Aucune sauvegarde ni test n\'a encore été exécuté.</td></tr>';
    }
    function loadBackup() { return api("GET", "/backups").then(renderBackup); }
    loadBackup();

    // --- Supervision des automatisations --------------------------------
    var STATUT_JOB = { "succès": ["green", "Réussi"], "échec": ["red", "Échoué"], "en_cours": ["blue", "En cours"], "demandé": ["warn", "Demandé"] };
    function renderJobs(data) {
      var state = document.getElementById("workerState");
      var workers = data.workers || [];
      if (state) {
        var vivants = workers.filter(function (w) { return w.alive; }).length;
        state.className = "b " + (!workers.length ? "red" : (vivants === workers.length ? "green" : "red"));
        state.textContent = !workers.length ? "Aucun exécutant démarré" : vivants + "/" + workers.length + " exécutant(s) actif(s)";
      }
      var body = document.getElementById("jobsBody");
      if (!body) return;
      body.innerHTML = (data.jobs || []).map(function (job) {
        var run = job.lastRun, badge = run ? (STATUT_JOB[run.status] || ["grey", run.status]) : ["grey", "Jamais exécuté"];
        var detail = run ? escapeHtml(stamp(run.finishedAt || run.startedAt || run.requestedAt)) + '<br><span class="muted" style="font-size:11.5px">' + escapeHtml((run.message || "").slice(0, 140)) + "</span>" : '<span class="muted">—</span>';
        var action = job.requestPending ? '<span class="b warn">Demande en attente</span>'
          : '<button type="button" class="btn" style="padding:6px 12px;font-size:12px" data-run-job="' + escapeHtml(job.name) + '">Exécuter</button>';
        return "<tr><td><b>" + escapeHtml(job.label) + "</b></td><td>" + escapeHtml(job.schedule) + "</td><td>" + detail + '</td><td><span class="b ' + badge[0] + '">' + escapeHtml(badge[1]) + "</span></td><td>" + action + "</td></tr>";
      }).join("") || '<tr><td colspan="5" class="muted" style="text-align:center;padding:24px">Aucun travail enregistré.</td></tr>';
    }
    function loadJobs() { return api("GET", "/automation/jobs").then(renderJobs).catch(function () {}); }
    var GRAVITE = { moyenne: "warn", haute: "red", critique: "red" };
    function renderAlerts(data) {
      var count = document.getElementById("alertsCount");
      if (count) { count.className = "b " + (data.open ? "red" : "green"); count.textContent = data.open ? data.open + " ouverte(s)" : "Aucune alerte ouverte"; }
      var body = document.getElementById("alertsBody");
      if (!body) return;
      body.innerHTML = (data.entries || []).map(function (a) {
        var ouverte = a.status === "ouverte";
        var action = ouverte ? '<button type="button" class="btn" style="padding:6px 12px;font-size:12px" data-handle-alert="' + a.id + '">Marquer traitée</button>'
          : '<span class="muted" style="font-size:11.5px">' + escapeHtml((a.handledBy || "") + " — " + (a.handlingNote || "")) + "</span>";
        return "<tr><td>" + escapeHtml(stamp(a.createdAt)) + '</td><td><span class="b ' + (GRAVITE[a.severity] || "grey") + '">' + escapeHtml(a.severity) + "</span></td><td><b>" + escapeHtml(a.title) + '</b><br><span class="muted" style="font-size:11.5px">' + escapeHtml(a.message) + "</span></td><td>" + (ouverte ? "Ouverte" : "Traitée") + "</td><td>" + action + "</td></tr>";
      }).join("") || '<tr><td colspan="5" class="muted" style="text-align:center;padding:24px">Aucune alerte de sécurité.</td></tr>';
    }
    function loadAlerts() { return api("GET", "/security/alerts?limit=30").then(renderAlerts).catch(function () {}); }
    loadJobs();
    loadAlerts();
    setInterval(function () { if (!document.hidden) { loadJobs(); } }, 30000);
    document.addEventListener("click", function (e) {
      var runBtn = e.target.closest("[data-run-job]");
      if (runBtn) {
        var name = runBtn.getAttribute("data-run-job");
        api("POST", "/automation/jobs/" + encodeURIComponent(name) + "/runs", {}).then(function () {
          toast("Demande transmise à l'exécutant : le travail démarre dans les secondes qui viennent.", "ok");
          loadJobs();
          setTimeout(function () { loadJobs(); loadBackup(); }, 8000);
        }).catch(function (err) { toast(err.message || "Demande impossible.", "err"); });
        return;
      }
      var alertBtn = e.target.closest("[data-handle-alert]");
      if (alertBtn) {
        var id = alertBtn.getAttribute("data-handle-alert");
        openModal({
          title: "Traiter l'alerte", ok: "Marquer traitée",
          bodyHtml: '<div class="field"><label>Vérifications effectuées / décision</label><textarea class="ta" name="note" placeholder="Ex. : le collaborateur a confirmé l\'export pour un rendez-vous client."></textarea></div>',
          onOk: function (payload) {
            if (!String(payload.note || "").trim()) { toast("Indiquez ce qui a été vérifié.", "err"); return Promise.reject(); }
            return api("PATCH", "/security/alerts/" + encodeURIComponent(id), { status: "traitée", note: payload.note }).then(function () {
              toast("Alerte marquée traitée.", "ok");
              return loadAlerts();
            });
          }
        });
      }
    });
    var backupBtn = document.createElement("button");
    backupBtn.className = "btn block";
    backupBtn.style.marginTop = "10px";
    backupBtn.textContent = "Lancer une sauvegarde complète";
    var restoreBtn = Array.from(document.querySelectorAll("button.btn")).find(function (b) { return /restauration/i.test(b.textContent); });
    if (restoreBtn && restoreBtn.parentNode) restoreBtn.parentNode.appendChild(backupBtn);
    backupBtn.addEventListener("click", function () {
      openModal({ title: "Sauvegarde complète", text: "Les copies chiffrées seront enregistrées dans le stockage local de sauvegarde et, si configuré, sur le stockage cloud hors site.", ok: "Sauvegarder", onOk: function () {
        return api("POST", "/backups/run", {}).then(function (result) { toast((result.run && result.run.message) || "Sauvegarde terminée.", "ok"); return loadBackup(); });
      }});
    });
    var btn = Array.from(document.querySelectorAll("button.btn")).find(function (b) { return /restauration/i.test(b.textContent); });
    if (!btn) return;
    btn.addEventListener("click", function () {
      openModal({
        title: "Restauration test",
        text: "Un test de restauration sera lancé sans écraser les données de production.",
        ok: "Lancer",
        onOk: function () {
          return api("POST", "/backups/restore-test", {}).then(function (result) {
            toast(result.message || "Test de restauration terminé.", "ok");
            return loadBackup();
          });
        }
      });
    });
  }

  function setupAudit() {
    if (page.indexOf("journal") !== 0) return;
    if (!can("audit")) return;
    // Le cahier des charges exige l'ancienne ET la nouvelle valeur pour les
    // événements qui modifient un droit ou une classification. Elles étaient
    // écrites en base et ne sortaient d'aucune API : le journal était
    // consultable sans être exploitable. On les rend ici lisibles.
    function auditDetails(details) {
      if (!details || typeof details !== "object") return "";
      var bouts = [];
      if (details.previous !== undefined || details.next !== undefined) {
        bouts.push("\u00ab\u00a0" + escapeHtml(String(details.previous == null ? "\u2014" : details.previous)) + "\u00a0\u00bb \u2192 \u00ab\u00a0" + escapeHtml(String(details.next == null ? "\u2014" : details.next)) + "\u00a0\u00bb");
      }
      if (details.motif) bouts.push("Motif\u00a0: " + escapeHtml(String(details.motif)));
      if (details.propagation) {
        var p = details.propagation;
        bouts.push("\u00ab\u00a0" + escapeHtml(String(p.niveau_precedent)) + "\u00a0\u00bb \u2192 \u00ab\u00a0" + escapeHtml(String(p.niveau_suivant)) + "\u00a0\u00bb (" + p.pieces_relevees + " pi\u00e8ce(s) relev\u00e9e(s), " + p.pieces_abaissees + " abaiss\u00e9e(s))");
      }
      if (details.nombre !== undefined) bouts.push(details.nombre + " habilitation(s)");
      if (details.pieces_omises) bouts.push(details.pieces_exportees + " pi\u00e8ce(s) export\u00e9e(s), " + details.pieces_omises + " omise(s) faute d'habilitation");
      if (details.sessions_revoquees) bouts.push("sessions r\u00e9voqu\u00e9es");
      if (!bouts.length) return "";
      return '<div class="ls" style="margin-top:3px;opacity:.85">' + bouts.join(" \u00b7 ") + "</div>";
    }
    // Libellés français des actions : le journal stocke des codes techniques
    // (« document_searched ») qui ne parlent pas à l'étude.
    var AUDIT_LIBELLES = {
      login: "Connexion", login_mfa: "Connexion validée (double authentification)", login_failed: "Échec de connexion",
      login_locked: "Compte verrouillé après échecs", logout: "Déconnexion", mfa_challenge_sent: "Code de connexion demandé",
      otp_sent_login_mfa: "Code de connexion envoyé par e-mail", password_changed: "Mot de passe modifié",
      password_reset_requested: "Réinitialisation du mot de passe demandée", password_reset_completed: "Mot de passe réinitialisé",
      totp_enabled: "Application d'authentification activée", totp_disabled: "Application d'authentification désactivée",
      totp_setup_started: "Activation de l'application d'authentification commencée", totp_confirm_failed: "Code d'application refusé",
      document_uploaded: "Document déposé", document_viewed: "Document consulté", document_searched: "Recherche de documents",
      document_validated: "Acte validé", document_archived: "Document archivé", document_exported: "Document exporté",
      document_new_version: "Nouvelle version déposée", document_quality_checked: "Contrôle qualité effectué",
      document_trashed: "Document mis à la corbeille", document_destroyed: "Document détruit",
      document_destruction_authorized: "Destruction autorisée", document_restriction_updated: "Confidentialité du document modifiée",
      document_ocr_processed: "Texte extrait (OCR)", document_ocr_failed: "Échec de l'extraction du texte (OCR)",
      document_upload_blocked_virus: "Dépôt bloqué : virus détecté", document_upload_scan_unavailable: "Dépôt refusé : antivirus indisponible",
      document_expiry_notified: "Alerte d'expiration de pièce", document_reencrypted: "Document rechiffré",
      dossier_created: "Dossier créé", dossier_viewed: "Dossier consulté", dossier_exported: "Dossier exporté",
      dossier_status_updated: "Statut du dossier modifié", dossier_confidentiality_updated: "Confidentialité du dossier modifiée",
      dossier_assignment_created: "Collaborateur affecté au dossier", dossier_assignment_removed: "Affectation retirée",
      dossier_checklist_item_completed: "Pièce de checklist reçue", dossier_checklist_item_created: "Pièce ajoutée à la checklist",
      dossier_checklist_complete: "Checklist du dossier complète", missing_documents_detected: "Pièces manquantes détectées",
      client_created: "Client créé", task_created: "Tâche créée", task_updated: "Tâche modifiée",
      task_reminder_sent: "Rappel de tâche envoyé", task_overdue_notified: "Tâche en retard signalée", task_escalated: "Tâche en retard remontée au notaire",
      notification_read: "Notification lue", permission_granted: "Droit accordé", permission_revoked: "Droit retiré",
      access_requested: "Demande d'accès", access_request_accepted: "Demande d'accès acceptée", access_request_refused: "Demande d'accès refusée",
      user_role_changed: "Rôle d'utilisateur modifié", settings_updated: "Paramètres modifiés",
      backup_run_completed: "Sauvegarde réussie", backup_run_failed: "Échec de sauvegarde", backup_stale_alert: "Alerte : sauvegarde en retard",
      backup_restore_drill: "Exercice de restauration", backup_restored: "Sauvegarde restaurée",
      encryption_key_generated: "Clé de chiffrement générée", encryption_key_activated: "Clé de chiffrement activée",
      encryption_keys_recovery_exported: "Paquet de récupération des clés exporté",
      security_alert_raised: "Alerte de sécurité levée", security_alert_handled: "Alerte de sécurité traitée",
      audit_export: "Journal exporté", audit_integrity_checked: "Intégrité du journal vérifiée", job_requested: "Travail automatique lancé à la main"
    };
    function auditLibelle(action) {
      action = String(action || "");
      if (AUDIT_LIBELLES[action]) return AUDIT_LIBELLES[action];
      if (action.indexOf("job_") === 0) return "Travail automatique : " + action.slice(4).replace(/_/g, " ");
      return action.replace(/_/g, " ");
    }
    function auditHorodatage(value, secondes) {
      var d = new Date(value);
      if (!value || isNaN(d)) return "";
      var heure = { hour: "2-digit", minute: "2-digit" };
      if (secondes) heure.second = "2-digit";
      return d.toLocaleDateString("fr-FR", { day: "2-digit", month: secondes ? "long" : "short", year: "numeric" }) + " · " + d.toLocaleTimeString("fr-FR", heure);
    }
    function auditValeur(v) {
      if (v === null || v === undefined || v === "") return "—";
      if (typeof v === "object") return JSON.stringify(v);
      return String(v);
    }
    function auditFiche(log) {
      if (!log) return;
      var echec = log.result && log.result !== "success";
      var ligne = function (titre, valeur, mono) {
        return '<div style="display:flex;gap:12px;padding:8px 0;border-bottom:1px solid var(--line,#eee)"><div class="ls" style="width:130px;flex-shrink:0">' + titre + '</div><div style="font-size:13px;word-break:break-all' + (mono ? ";font-family:ui-monospace,Consolas,monospace;font-size:12px" : "") + '">' + escapeHtml(valeur) + "</div></div>";
      };
      var details = log.details && typeof log.details === "object" ? Object.keys(log.details) : [];
      var corps =
        ligne("Date et heure", auditHorodatage(log.timestamp, true)) +
        ligne("Auteur", log.user || "Système") +
        ligne("Action", auditLibelle(log.action)) +
        ligne("Code technique", log.action || "—", true) +
        ligne("Objet concerné", [log.targetType, log.targetId].filter(Boolean).join(" · ") || "—") +
        ligne("Résultat", echec ? "Échec (" + log.result + ")" : "Réussi") +
        ligne("Adresse IP", log.ip || "—", true) +
        details.map(function (k) { return ligne(escapeHtml(k.replace(/_/g, " ")), auditValeur(log.details[k])); }).join("") +
        (log.entryHash ? ligne("Empreinte", log.entryHash, true) : "") +
        '<div class="ls" style="margin-top:10px">Cette entrée est scellée dans la chaîne d’intégrité du journal : elle ne peut être ni modifiée ni supprimée.</div>';
      openModal({ title: "Détail de l’événement", ok: "Fermer", cancel: false, bodyHtml: '<div style="max-height:60vh;overflow:auto">' + corps + "</div>" });
    }
    if (!cfg.useMock) api("GET", "/audit?scope=" + (cfg.role === "admin" ? "cabinet" : "me")).then(function (data) {
      // Le journal renvoie { total, returned, entries } ; on accepte aussi un
      // tableau nu pour ne dépendre d'aucun ordre de déploiement.
      var logs = Array.isArray(data) ? data : ((data && data.entries) || []);
      var total = Array.isArray(data) ? logs.length : ((data && data.total) || logs.length);
      var card = document.querySelector(".wrap .card");
      if (!card) return;
      if (!logs.length) {
        card.innerHTML = '<div class="lrow"><div><div class="lt">Aucune activit\u00e9</div><div class="ls">Les actions r\u00e9alis\u00e9es appara\u00eetront ici.</div></div></div>';
        return;
      }
      var reste = total > logs.length
        ? '<div class="lrow"><div><div class="ls">' + logs.length + " entr\u00e9e(s) affich\u00e9e(s) sur " + total + ". L'export CSV contient le journal complet et le d\u00e9tail de chaque \u00e9v\u00e9nement.</div></div></div>"
        : "";
      card.innerHTML = logs.map(function (log, i) {
        var echec = log.result && log.result !== "success";
        return '<div class="lrow audit-row" data-audit-index="' + i + '" role="button" tabindex="0" aria-label="Voir le détail de l\u2019événement" style="cursor:pointer"><span class="li" style="background:var(--' + (echec ? "danger-soft);color:var(--danger" : "primary-soft);color:var(--primary") + ')">' + (echec ? "\u2717" : "\u25f7") + '</span><div><div class="lt">' + escapeHtml(log.user) + '</div><div class="ls">' + escapeHtml(auditLibelle(log.action)) + (log.targetId ? " \u2014 " + escapeHtml(log.targetId) : "") + "</div>" + auditDetails(log.details) + '</div><div class="lx"><span class="ls">' + escapeHtml(auditHorodatage(log.timestamp)) + "</span></div></div>";
      }).join("") + reste;
      function ouvrir(e) {
        var ligne = e.target.closest("[data-audit-index]");
        if (!ligne) return;
        if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
        e.preventDefault();
        auditFiche(logs[Number(ligne.getAttribute("data-audit-index"))]);
      }
      card.addEventListener("click", ouvrir);
      card.addEventListener("keydown", ouvrir);
    });
    var exportBtn = Array.from(document.querySelectorAll("a.btn,.btn")).find(function (b) { return /export csv/i.test(b.textContent); });
    if (exportBtn) {
      exportBtn.addEventListener("click", function (e) {
        e.preventDefault();
        var scope = cfg.role === "admin" ? "cabinet" : "me";
        fetch(cfg.apiBase + "/audit/export?scope=" + scope, { headers: { Authorization: "Bearer " + cfg.token } }).then(function (r) {
          if (r.status === 401) throw new Error("Session expirée.");
          if (r.status === 403) throw new Error("Droits insuffisants.");
          if (!r.ok) throw new Error("Export impossible.");
          return r.blob();
        }).then(function (blob) {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = "audit.csv";
          a.click();
          URL.revokeObjectURL(a.href);
          toast("Export CSV téléchargé.", "ok");
        }).catch(function (err) { toast(err.message || "Échec de l'export.", "err"); });
      });
    }
  }

  function setupDocumentDetail() {
    if (page.indexOf("document-detail") !== 0) return;
    var ref = params.get("ref");
    var currentDoc = null;

    function updateActionButtons(doc) {
      currentDoc = doc;
      var validerBtn = document.getElementById("btnValiderActe");
      if (validerBtn) {
        if (doc.statut === "validé") {
          validerBtn.classList.add("disabled");
          validerBtn.setAttribute("aria-disabled", "true");
          validerBtn.innerHTML = validerBtn.innerHTML.replace(/Valider l'acte/i, "Acte validé");
        } else {
          validerBtn.classList.remove("disabled");
          validerBtn.removeAttribute("aria-disabled");
          validerBtn.innerHTML = validerBtn.innerHTML.replace(/Acte validé/i, "Valider l'acte");
        }
      }
      var restrictionBtn = document.getElementById("btnRestriction");
      if (restrictionBtn) {
        var restreint = doc.niveau_de_confidentialite && doc.niveau_de_confidentialite !== "Standard";
        restrictionBtn.innerHTML = restrictionBtn.innerHTML.replace(/Restreindre ce document|Lever la restriction/i, restreint ? "Lever la restriction" : "Restreindre ce document");
      }
      var archiverBtn = document.getElementById("btnArchiver");
      if (archiverBtn) {
        var enCorbeilleOuDetruit = ["corbeille", "destruction_demandée", "destruction_autorisée", "détruit"].indexOf(doc.statut) !== -1;
        if (enCorbeilleOuDetruit) {
          archiverBtn.style.display = "none";
        } else {
          archiverBtn.style.display = "";
          if (doc.is_archived) {
            archiverBtn.classList.add("disabled");
            archiverBtn.setAttribute("aria-disabled", "true");
            archiverBtn.innerHTML = archiverBtn.innerHTML.replace(/Archiver ce document/i, "Document archivé");
          } else {
            archiverBtn.classList.remove("disabled");
            archiverBtn.removeAttribute("aria-disabled");
            archiverBtn.innerHTML = archiverBtn.innerHTML.replace(/Document archivé/i, "Archiver ce document");
          }
        }
      }
      var corbeilleBtn = document.getElementById("btnCorbeille");
      var restaurerBtn = document.getElementById("btnRestaurer");
      if (corbeilleBtn) {
        var trashLabels = { "corbeille": "Demander la destruction", "destruction_demandée": "Autoriser la destruction", "destruction_autorisée": "Détruire définitivement" };
        var trashActions = { "corbeille": "request-destruction", "destruction_demandée": "authorize-destruction", "destruction_autorisée": "destroy" };
        if (doc.statut === "détruit") {
          corbeilleBtn.style.display = "none";
        } else {
          corbeilleBtn.style.display = "";
          var nextLabel = trashLabels[doc.statut] || "Mettre à la corbeille";
          corbeilleBtn.setAttribute("data-trash-action", trashActions[doc.statut] || "trash");
          corbeilleBtn.innerHTML = corbeilleBtn.innerHTML.replace(/Mettre à la corbeille|Demander la destruction|Autoriser la destruction|Détruire définitivement/i, nextLabel);
        }
      }
      if (restaurerBtn) restaurerBtn.style.display = (doc.statut === "corbeille" || doc.statut === "destruction_demandée") ? "" : "none";
      var ocrBtn = document.getElementById("btnOCR");
      if (ocrBtn) {
        if (doc.ocr_status === "extrait") {
          ocrBtn.innerHTML = ocrBtn.innerHTML.replace(/Lancer l'OCR|Relancer l'OCR/i, "Relancer l'OCR");
        } else {
          ocrBtn.innerHTML = ocrBtn.innerHTML.replace(/Lancer l'OCR|Relancer l'OCR/i, "Lancer l'OCR");
        }
      }
      var qcBtn = document.getElementById("btnQualityCheck");
      if (qcBtn) qcBtn.innerHTML = qcBtn.innerHTML.replace(/Contrôle qualité|Contrôle qualité effectué/i, doc.quality_checked_at ? "Contrôle qualité effectué" : "Contrôle qualité");
    }
    function reloadCurrentDoc() {
      return api("GET", "/documents/" + encodeURIComponent(ref) + "?format=json").then(updateActionButtons);
    }
    var corbeilleBtnEl = document.getElementById("btnCorbeille");
    if (corbeilleBtnEl) {
      corbeilleBtnEl.addEventListener("click", function (e) {
        e.preventDefault();
        var action = corbeilleBtnEl.getAttribute("data-trash-action") || "trash";
        var copy = {
          trash: { title: "Mettre ce document à la corbeille", ok: "Mettre à la corbeille", text: "Le document reste récupérable tant qu'il n'est pas définitivement détruit." },
          "request-destruction": { title: "Demander la destruction", ok: "Demander la destruction", text: "Une autorisation distincte sera encore nécessaire avant toute destruction effective." },
          "authorize-destruction": { title: "Autoriser la destruction", ok: "Autoriser", text: "Cette autorisation permettra la destruction définitive et irréversible du fichier." },
          destroy: { title: "Détruire définitivement", ok: "Détruire", text: "" }
        }[action];
        if (action === "destroy") {
          openModal({
            title: copy.title, ok: copy.ok,
            bodyHtml: "<p>Cette action supprime définitivement le fichier. Elle est irréversible.</p><div class=\"field\"><label>Confirmation</label><input class=\"inp\" name=\"confirm\" placeholder=\"Saisissez DETRUIRE\"></div>",
            onOk: function (payload) {
              if (payload.confirm !== "DETRUIRE") { toast("Confirmation invalide.", "err"); throw new Error("confirm"); }
              return api("POST", "/documents/" + encodeURIComponent(ref) + "/destroy", {}).then(function () { toast("Document détruit définitivement.", "ok"); reloadCurrentDoc(); });
            }
          });
        } else {
          openModal({
            title: copy.title, ok: copy.ok, text: copy.text,
            onOk: function () { return api("POST", "/documents/" + encodeURIComponent(ref) + "/" + action, {}).then(function () { toast(copy.ok + " effectué.", "ok"); reloadCurrentDoc(); }); }
          });
        }
      });
    }
    var restaurerBtnEl = document.getElementById("btnRestaurer");
    if (restaurerBtnEl) {
      restaurerBtnEl.addEventListener("click", function (e) {
        e.preventDefault();
        openModal({
          title: "Restaurer ce document", ok: "Restaurer", text: "Le document quitte la corbeille et retrouve son statut précédent.",
          onOk: function () { return api("POST", "/documents/" + encodeURIComponent(ref) + "/restore", {}).then(function () { toast("Document restauré.", "ok"); reloadCurrentDoc(); }); }
        });
      });
    }

    if (ref) {
      document.querySelectorAll(".kv").forEach(function (kv) {
        if (/référence/i.test(kv.textContent) && kv.querySelector("b")) kv.querySelector("b").textContent = ref;
      });
      if (!cfg.useMock) api("GET", "/documents/" + encodeURIComponent(ref) + "?format=json").then(function (doc) {
        updateActionButtons(doc);
        var cards = document.querySelectorAll(".two > .card");
        if (cards[0]) {
          cards[0].classList.add("doc-viewer-card");
          cards[0].innerHTML = '<div class="doc-viewer-status" style="min-height:520px">Chargement du document…</div>';
          fetch(cfg.apiBase + "/documents/" + encodeURIComponent(ref), { headers: { Authorization: "Bearer " + cfg.token } }).then(function (r) { if (!r.ok) throw new Error(); return r.blob(); }).then(function (blob) {
            if (window.GEDPdfViewer) {
              window.GEDPdfViewer.mount(cards[0], { blob: blob, filename: doc.nom, contentType: doc.content_type });
            } else {
              cards[0].innerHTML = '<div class="muted" style="height:520px;display:grid;place-items:center">Prévisualisation indisponible.</div>';
            }
          }).catch(function () { cards[0].innerHTML = '<div class="muted" style="height:520px;display:grid;place-items:center">Prévisualisation indisponible.</div>'; });
        }
        document.querySelectorAll(".kv").forEach(function (kv) {
          var label = kv.querySelector("span"); var value = kv.querySelector("b"); if (!label || !value) return;
          if (/référence/i.test(label.textContent)) value.textContent = doc.reference;
          if (/type/i.test(label.textContent)) value.textContent = doc.type;
          if (/confidentialité/i.test(label.textContent)) value.textContent = doc.niveau_de_confidentialite;
          if (/empreinte/i.test(label.textContent)) value.textContent = doc.sha256;
          if (/format/i.test(label.textContent)) value.textContent = doc.content_type;
        });
        var kvCode = document.getElementById("kvCodeNotarial");
        if (kvCode) kvCode.textContent = (doc.codeNotarialActuel || doc.code_notarial || "—") + "  ·  " + (doc.idMaitre || "");
        var title = document.querySelector(".two h3"); if (title) title.textContent = doc.nom;
        var meta = document.getElementById("docMeta");
        if (meta) meta.textContent = (doc.uploadedByName || "Auteur inconnu") + " · " + (doc.created_at ? docDate(doc.created_at) : "date inconnue");
        var versionsHost = document.getElementById("versionsList");
        if (versionsHost) {
          api("GET", "/documents/" + encodeURIComponent(ref) + "/versions").then(function (versions) {
            if (!Array.isArray(versions) || !versions.length) {
              versionsHost.innerHTML = '<div class="kv"><span>Aucune version enregistrée.</span><b></b></div>';
              return;
            }
            versionsHost.innerHTML = versions.slice().sort(function (a, b) { return b.version - a.version; }).map(function (v) {
              return '<div class="kv"><span>Version ' + v.version + (v.is_current ? ' — actuelle' : '') + '</span><b>' + escapeHtml(v.created_at ? docDate(v.created_at) : "—") + '</b></div>';
            }).join("");
          }).catch(function () {
            versionsHost.innerHTML = '<div class="kv"><span>Historique des versions indisponible.</span><b></b></div>';
          });
        }
      });
    }
    Array.from(document.querySelectorAll("a.btn,button.btn")).forEach(function (el) {
      el.addEventListener("click", function (e) {
        var label = el.textContent.trim();
        if (/voir en plein écran/i.test(label)) {
          e.preventDefault();
          var viewerCard = document.querySelector(".two > .card");
          if (viewerCard && viewerCard.querySelector(".doc-viewer") && window.GEDPdfViewer) {
            window.GEDPdfViewer.toggleFullscreen(viewerCard);
          } else {
            toast("Le document n'est pas encore chargé.", "err");
          }
        } else if (/demander l'accès/i.test(label)) {
          e.preventDefault();
          requestAccess(ref || "");
        } else if (/valider l'acte/i.test(label)) {
          e.preventDefault();
          if (!can("validateActs")) return deny("Seul le notaire valide un acte.");
          if (currentDoc && currentDoc.statut === "validé") return; // déjà validé, rien à refaire tant qu'aucune nouvelle demande n'existe
          api("POST", "/documents/" + encodeURIComponent(ref || "current") + "/validate", {}).then(function (doc) {
            toast("Acte validé.", "ok");
            updateActionButtons(doc);
          });
        } else if (/acte validé/i.test(label)) {
          e.preventDefault();
          toast("Cet acte est déjà validé.", "err");
        } else if (/restreindre ce document|lever la restriction/i.test(label)) {
          e.preventDefault();
          if (!can("validateActs")) return deny("Seul le notaire modifie la confidentialité d'un document.");
          var estRestreint = currentDoc && currentDoc.niveau_de_confidentialite && currentDoc.niveau_de_confidentialite !== "Standard";
          if (estRestreint) {
            api("PATCH", "/documents/" + encodeURIComponent(ref || "current"), { niveau_de_confidentialite: "Standard" }).then(function (doc) {
              toast("Restriction levée sur ce document.", "ok");
              updateActionButtons(doc);
            });
          } else {
            openModal({
              title: "Restreindre ce document",
              ok: "Restreindre",
              bodyHtml:
                '<p>Seuls les collaborateurs et clercs ayant un accès accordé explicitement pourront le consulter.</p>' +
                '<div class="field"><label>Niveau</label><select class="inp" name="niveau_de_confidentialite">' +
                '<option value="Restreint">Restreint</option><option value="Confidentiel">Confidentiel</option><option value="Très confidentiel">Très confidentiel</option>' +
                '</select></div>',
              onOk: function (payload) {
                return api("PATCH", "/documents/" + encodeURIComponent(ref || "current"), { niveau_de_confidentialite: payload.niveau_de_confidentialite || "Restreint" }).then(function (doc) {
                  toast("Restriction ajoutée sur ce document.", "ok");
                  updateActionButtons(doc);
                });
              }
            });
          }
        } else if (/archiver ce document/i.test(label)) {
          e.preventDefault();
          if (!can("validateActs")) return deny("Seul le notaire archive un document.");
          if (currentDoc && currentDoc.is_archived) return;
          openModal({
            title: "Archiver ce document",
            ok: "Archiver",
            text: "Le document sera retiré des listes actives et marqué comme archivé. Cette action n'est pas réversible depuis l'interface.",
            onOk: function () {
              return api("POST", "/documents/archive", { reference: ref || "current" }).then(function () {
                toast("Document archivé.", "ok");
                reloadCurrentDoc();
              });
            }
          });
        } else if (/document archivé/i.test(label)) {
          e.preventDefault();
          toast("Ce document est déjà archivé.", "err");
        } else if (/exporter/i.test(label)) {
          e.preventDefault();
          fetch(cfg.apiBase + "/documents/" + encodeURIComponent(ref || "current") + "/export", { headers: { Authorization: "Bearer " + cfg.token } }).then(function (r) { if (!r.ok) throw new Error("Export impossible."); return r.blob(); }).then(function (blob) { var a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "document"; a.click(); URL.revokeObjectURL(a.href); toast("Export téléchargé.", "ok"); }).catch(function (err) { toast(err.message, "err"); });
        } else if (/imprimer/i.test(label)) {
          e.preventDefault();
          window.print();
        } else if (/lancer l'ocr|relancer l'ocr/i.test(label)) {
          e.preventDefault();
          api("POST", "/documents/" + encodeURIComponent(ref || "current") + "/ocr", {}).then(function (doc) {
            toast(doc.ocr_status === "extrait" ? "Texte extrait avec succès." : "OCR terminé : " + (doc.ocr_error || "aucun texte exploitable trouvé."), doc.ocr_status === "extrait" ? "ok" : "err");
            updateActionButtons(doc);
          }).catch(function (err) { toast(err.message || "Échec de l'OCR.", "err"); });
        } else if (/contrôle qualité/i.test(label)) {
          e.preventDefault();
          openModal({
            title: "Contrôle qualité de la numérisation",
            ok: "Valider le contrôle",
            bodyHtml:
              '<div class="field"><label><input type="checkbox" name="complete"> Document complet</label></div>' +
              '<div class="field"><label><input type="checkbox" name="ordered"> Pages dans le bon ordre</label></div>' +
              '<div class="field"><label><input type="checkbox" name="legible"> Lisible</label></div>' +
              '<div class="field"><label><input type="checkbox" name="noMissingPage"> Aucune page manquante</label></div>' +
              '<div class="field"><label><input type="checkbox" name="noDuplicate"> Aucun doublon</label></div>' +
              '<div class="field"><label><input type="checkbox" name="orientationCorrect"> Orientation correcte</label></div>' +
              '<div class="field"><label><input type="checkbox" name="dossierCorrect"> Bon dossier / bonne affaire</label></div>' +
              '<div class="field"><label>Notes (optionnel)</label><textarea class="inp" name="notes" rows="2"></textarea></div>',
            onOk: function (payload) {
              var checks = {};
              ["complete", "ordered", "legible", "noMissingPage", "noDuplicate", "orientationCorrect", "dossierCorrect"].forEach(function (key) {
                var box = document.querySelector('.modal-backdrop [name="' + key + '"]');
                checks[key] = box ? box.checked : false;
              });
              return api("POST", "/documents/" + encodeURIComponent(ref || "current") + "/quality-check", { checks: checks, notes: payload.notes || "" }).then(function (doc) {
                toast(doc.quality_passed ? "Contrôle qualité validé : document en attente de validation notariale." : "Contrôle qualité échoué : document renvoyé à corriger.", doc.quality_passed ? "ok" : "err");
                updateActionButtons(doc);
              });
            }
          });
        }
      });
    });
  }

  // Lien de la notification vers ce qu'elle concerne (pièce, dossier, tâche,
  // supervision) : l'utilisateur agit sans devoir rechercher.
  function notificationHref(item) {
    var id = encodeURIComponent(item.targetId || "");
    switch (item.targetType) {
      case "document": return id ? "document-detail.html?ref=" + id : "";
      case "dossier": return id ? "dossier-detail.html?ref=" + id : "";
      case "task": return "taches.html";
      case "job": case "backup": case "security_alert": return "sauvegarde.html#supervision";
      default: return "";
    }
  }
  var NOTIF_ICONS = {
    alerte: { bg: "var(--danger-soft)", fg: "var(--danger)", path: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0"/><path d="M12 9v4"/><path d="M12 17h.01"/>' },
    attention: { bg: "var(--warn-soft)", fg: "#A9740A", path: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>' },
    backup: { bg: "var(--green-soft)", fg: "#1E9C72", path: '<path d="m5 12 4 4 10-10"/>' },
    permission_granted: { bg: "var(--warn-soft)", fg: "#A9740A", path: '<path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/><path d="m9 12 2 2 4-4"/>' },
    document: { bg: "var(--blue-soft)", fg: "#2E6FD6", path: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h6"/>' }
  };
  function timeAgo(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var diffMs = Date.now() - d.getTime();
    var mins = Math.round(diffMs / 60000);
    if (mins < 1) return "à l'instant";
    if (mins < 60) return "il y a " + mins + " min";
    var hours = Math.round(mins / 60);
    if (hours < 24) return "il y a " + hours + " h";
    var days = Math.round(hours / 24);
    if (days === 1) return "hier";
    if (days < 7) return "il y a " + days + " j";
    return docDate(iso);
  }
  function notificationRow(item) {
    var grave = item.severity === "haute" || item.severity === "critique";
    var icon = grave ? NOTIF_ICONS.alerte : (item.severity === "attention" ? NOTIF_ICONS.attention : (NOTIF_ICONS[item.type] || NOTIF_ICONS.document));
    return '<div class="lrow" data-id="' + item.id + '" data-href="' + escapeHtml(notificationHref(item)) + '" style="cursor:pointer' + (item.read ? ";opacity:.6" : "") + '"><span class="li" style="background:' + icon.bg + ';color:' + icon.fg + '"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + icon.path + '</svg></span>' +
      '<div><div class="lt">' + escapeHtml(item.title || "Notification") + '</div><div class="ls">' + escapeHtml(item.message || "") + '</div></div>' +
      '<div class="lx"><span class="ls">' + timeAgo(item.createdAt) + '</span></div></div>';
  }
  function renderNotifications(items) {
    var host = document.getElementById("notificationsList");
    if (!host) return;
    if (!Array.isArray(items) || !items.length) {
      host.innerHTML = '<div class="muted" style="text-align:center;padding:24px">Aucune notification.</div>';
      return;
    }
    host.innerHTML = items.map(notificationRow).join("");
  }
  function loadNotifications() {
    var host = document.getElementById("notificationsList");
    return api("GET", "/notifications").then(function (items) { renderNotifications(items); return items; }).catch(function () {
      if (host) host.innerHTML = '<div class="muted" style="text-align:center;padding:24px">Impossible de charger les notifications.</div>';
    });
  }
  function refreshNotifDot() {
    var dot = document.querySelector(".bell .dot");
    if (!dot) return;
    api("GET", "/notifications/unread-count").then(function (data) {
      dot.classList.toggle("show", !!(data && data.count > 0));
    }).catch(function () {});
  }
  function setupNotifications() {
    refreshNotifDot();
    // Les rappels et alertes naissent côté serveur, sans action de
    // l'utilisateur : la cloche se rafraîchit seule (onglet visible
    // uniquement, pour ne pas solliciter le serveur inutilement).
    setInterval(function () { if (!document.hidden) refreshNotifDot(); }, 60000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refreshNotifDot(); });
    if (page.indexOf("notifications") !== 0) return;
    loadNotifications();
    var host = document.getElementById("notificationsList");
    if (host) {
      host.addEventListener("click", function (e) {
        var row = e.target.closest(".lrow");
        if (!row) return;
        var id = row.getAttribute("data-id");
        var href = row.getAttribute("data-href");
        api("PATCH", "/notifications/read", { id: id }).then(function () {
          row.style.opacity = ".6";
          refreshNotifDot();
          if (href) window.location.href = href;
        });
      });
    }
  }

  function setupAccessLinks() {
    document.querySelectorAll("a.link").forEach(function (a) {
      if (!/tout voir/i.test(a.textContent)) return;
      if (a.getAttribute("href") !== "#") return;
      a.addEventListener("click", function (e) {
        e.preventDefault();
        location.href = "notifications.html";
      });
    });
  }

  function setupFilterBtn() {
    document.querySelectorAll(".toolbar .iconbtn").forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        openModal({
          title: "Filtres",
          ok: "Appliquer",
          bodyHtml:
            '<div class="field"><label>Niveau</label><select class="sel" name="niveau"><option>Tous</option><option>Standard</option><option>Restreint</option><option>Confidentiel</option></select></div>',
          onOk: function (payload) {
            return api("GET", "/documents?niveau=" + encodeURIComponent(payload.niveau || "")).then(function (items) {
              renderDocuments(items);
              toast("Filtres appliqués à votre périmètre.", "ok");
            });
          }
        });
      });
    });
  }

  function setupChips() {
    document.querySelectorAll(".fchip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        chip.parentElement.querySelectorAll(".fchip").forEach(function (c) { c.classList.remove("on"); });
        chip.classList.add("on");
        api("POST", "/context/filiale", { name: (chip.querySelector(".ft") && chip.querySelector(".ft").textContent) || "" });
      });
    });
  }

  var __session = requireSession();
  if (!__session) return;
  cfg.token = __session.token;
  if (!guardPage()) return;
  applySessionIdentity(__session);
  wireLogout();
  surveillerInactivite();
  setupNav();
  setupNavTooltips();
  setupBackButtons();
  setupFixedHeader();
  setupSearch();
  setupSegs();
  setupDocs();
  setupTables();
  setupRecherche();
  setupProfil();
    if (page.indexOf("profil") === 0) setupSecuriteConnexion();
  setupScan();
  setupDossiers();
  setupClients();
  setupTaches();
  function setupKeyManagement() {
    if (page !== "configuration.html") return;
    var box=document.getElementById("keyStatus");
    var listBox=document.getElementById("keyList");
    function load(){
      return api("GET","/key-management/status").then(function(x){
        if(box) box.textContent="Clé active : "+(x.activeKeyId||"non configurée")+" · "+x.keyCount+" génération(s)";
        if(listBox){
          var known=x.knownKeyIds||[];
          listBox.innerHTML = known.length ? known.map(function(id){
            var isActive = id===x.activeKeyId;
            var refs = (x.documentReferences||{})[id]||0;
            return '<div class="kv"><span>'+escapeHtml(id)+(isActive?' · <b style="color:var(--primary)">active</b>':'')+' · '+refs+' document(s) référencé(s)</span>'+
              (isActive?'':'<b><button class="btn" data-activate-key="'+escapeHtml(id)+'" style="margin-right:6px">Activer</button><button class="btn" data-retire-key="'+escapeHtml(id)+'">Retirer</button></b>')+
              '</div>';
          }).join('') : '<div class="hint">Aucune clé connue du serveur.</div>';
          listBox.querySelectorAll('[data-activate-key]').forEach(function(b){
            b.addEventListener('click',function(){
              var keyId=b.getAttribute('data-activate-key');
              openModal({ title:'Activer cette clé', ok:'Activer', text:'La clé « '+keyId+' » deviendra la clé active pour tout nouveau document chiffré. Les documents déjà chiffrés avec une autre clé restent lisibles tant qu\'elle n\'est pas retirée.', onOk:function(){
                return api('POST','/key-management/activate',{keyId:keyId}).then(function(){ toast('Clé activée.','ok'); load(); });
              }});
            });
          });
          listBox.querySelectorAll('[data-retire-key]').forEach(function(b){
            b.addEventListener('click',function(){
              var keyId=b.getAttribute('data-retire-key');
              openModal({ title:'Retirer cette clé', ok:'Approuver le retrait', text:'Cette action vérifie qu\'aucun document n\'utilise plus cette clé, puis autorise sa suppression du secret-store serveur. Assurez-vous d\'avoir un paquet de récupération à jour avant de continuer.', onOk:function(){
                return api('POST','/key-management/retire',{keyId:keyId}).then(function(x){ toast(x.instruction||'Retrait approuvé.','ok'); load(); });
              }});
            });
          });
        }
      });
    }
    load();
    var gen=document.getElementById("generateKeyBtn"); if(gen) gen.addEventListener("click",function(){
      openModal({ title:"Générer une nouvelle clé", ok:"Générer",
        bodyHtml:'<div class="field"><label>Identifiant de la clé</label><input class="inp" name="keyId" placeholder="Ex. 2026-q4"></div>',
        onOk:function(payload){
          if(!payload.keyId) { toast("L'identifiant est obligatoire.","err"); throw new Error("keyId"); }
          return api("POST","/key-management/generate",{keyId:payload.keyId}).then(function(x){
            openModal({ title:"Clé générée", ok:"J'ai enregistré la clé", text:"Enregistrez-la immédiatement dans le secret-store du serveur, elle ne sera plus jamais affichée : "+x.keyId+" = "+x.key, onOk:function(){ load(); } });
          });
        }
      });
    });
    var exp=document.getElementById("exportKeysBtn"); if(exp) exp.addEventListener("click",function(){
      openModal({ title:"Exporter le paquet de récupération", ok:"Exporter",
        bodyHtml:'<div class="field"><label>Phrase secrète (12 caractères minimum)</label><input class="inp" type="password" name="passphrase"></div><div class="hint">Cette phrase sera nécessaire pour réimporter le paquet plus tard. Conservez-la séparément du fichier exporté.</div>',
        onOk:function(payload){
          if(!payload.passphrase || payload.passphrase.length<12){ toast("La phrase secrète doit faire au moins 12 caractères.","err"); throw new Error("passphrase"); }
          return fetch(cfg.apiBase+"/key-management/recovery-export",{method:"POST",headers:{Authorization:"Bearer "+cfg.token,"Content-Type":"application/json"},body:JSON.stringify({passphrase:payload.passphrase})}).then(function(r){if(!r.ok)return r.json().then(function(x){throw new Error(x.detail||"Export impossible")});return r.blob()}).then(function(blob){var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="ged-key-recovery.json";a.click();URL.revokeObjectURL(a.href);toast("Paquet de récupération exporté.","ok")});
        }
      });
    });
    var imp=document.getElementById("importRecoveryBtn"); if(imp) imp.addEventListener("click",function(){
      var fileInput=document.getElementById("recoveryFile");
      var passInput=document.getElementById("recoveryPassphrase");
      var file=fileInput && fileInput.files[0];
      var passphrase=passInput ? passInput.value : "";
      if(!file){ toast("Sélectionnez un fichier de paquet de récupération.","err"); return; }
      if(!passphrase){ toast("La phrase secrète est obligatoire.","err"); return; }
      var fd=new FormData(); fd.append("package",file); fd.append("passphrase",passphrase);
      fetch(cfg.apiBase+"/key-management/recovery-import",{method:"POST",headers:{Authorization:"Bearer "+cfg.token},body:fd}).then(function(r){return r.json().then(function(x){ if(!r.ok) throw new Error(x.detail||"Import impossible."); return x; })}).then(function(x){
        toast("Clés importées : "+(x.keyIds||[]).join(", "),"ok");
        if(fileInput) fileInput.value=""; if(passInput) passInput.value="";
        load();
      }).catch(function(e){ toast(e.message,"err"); });
    });
  }
  setupKeyManagement();
  setupUsers();
  setupPermissions();
  setupConfig();
  setupBackup();
  setupAudit();
  setupDocumentDetail();
  setupNotifications();
  setupAccessLinks();
  setupFilterBtn();
  setupPasswordToggles();
  function setupPasswordToggles() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest(".toggle-eye");
      if (!btn) return;
      var input = document.getElementById(btn.getAttribute("data-target"));
      if (!input) return;
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? "Masquer le mot de passe" : "Afficher le mot de passe");
    });
  }
  function alertRow(icon, title, message, href) {
    return '<a class="lrow" href="' + href + '" style="cursor:pointer;text-decoration:none;color:inherit"><span class="li" style="background:' + icon.bg + ';color:' + icon.fg + '"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + icon.path + '</svg></span>' +
      '<div><div class="lt">' + escapeHtml(title) + '</div><div class="ls">' + escapeHtml(message) + '</div></div></a>';
  }
  /* La tuile « sauvegarde » affichait « 100 % — 30/30 jours » en dur : elle
     restait au vert même si aucune sauvegarde n'avait jamais tourné, ce qui
     est exactement le contraire de ce qu'on attend d'un indicateur. Elle lit
     désormais l'état réel renvoyé par /api/backups. */
  function renderBackupTile() {
    var valeur = document.getElementById("statBackup");
    var tendance = document.getElementById("statBackupTrend");
    if (!valeur && !tendance) return;
    api("GET", "/backups").then(function (data) {
      var derniere = data && data.lastBackup;
      if (!derniere) {
        if (valeur) valeur.textContent = "Jamais";
        if (tendance) {
          tendance.className = "b red";
          tendance.textContent = "Aucune sauvegarde enregistrée";
        }
        return;
      }
      var reussie = derniere.status === "success";
      if (valeur) valeur.textContent = derniere.createdAt ? docDate(derniere.createdAt) : "—";
      if (tendance) {
        tendance.className = reussie ? "b green" : "b red";
        tendance.textContent = reussie
          ? (derniere.checkedDocuments || 0) + " document(s) sauvegardé(s)"
          : "Dernière sauvegarde en échec";
      }
    }).catch(function () {
      if (valeur) valeur.textContent = "—";
      if (tendance) { tendance.className = "b grey"; tendance.textContent = "État indisponible"; }
    });
  }

  /* Carte « Alertes » de l'accueil : état RÉEL de l'étude. Elle affichait
     jusqu'ici en dur « Sauvegarde OK · Réplication terminée » et « 5 documents
     à indexer », quelle que soit la réalité. */
  function renderAlertesReelles(data) {
    var hote = document.getElementById("alertesReelles");
    if (!hote) return;
    var ICONES = {
      ok: { bg: "var(--green-soft)", fg: "#1E9C72", path: '<path d="m5 12 4 4 10-10"/>' },
      info: { bg: "var(--blue-soft)", fg: "#2E6FD6", path: '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01"/><path d="M11 12h1v4h1"/>' },
      attention: { bg: "var(--warn-soft)", fg: "#A9740A", path: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>' },
      danger: { bg: "var(--danger-soft)", fg: "var(--danger)", path: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0"/><path d="M12 9v4"/><path d="M12 17h.01"/>' }
    };
    function ligne(icone, titre, texte, lien) {
      var i = ICONES[icone];
      var contenu = '<span class="li" style="background:' + i.bg + ';color:' + i.fg + '"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + i.path + '</svg></span>' +
        '<div><div class="lt">' + escapeHtml(titre) + '</div><div class="ls">' + escapeHtml(texte) + '</div></div>';
      return lien ? '<a class="lrow" href="' + lien + '" style="padding:13px 4px;text-decoration:none;color:inherit">' + contenu + '</a>'
        : '<div class="lrow" style="padding:13px 4px">' + contenu + '</div>';
    }
    var lignes = [];
    var auto = data.automatisations;
    if (auto) {
      var travailleurs = auto.travailleurs || [];
      var actifs = travailleurs.filter(function (w) { return w.alive; }).length;
      if (!travailleurs.length || actifs < travailleurs.length) lignes.push(ligne("danger", "Automatisations arrêtées", "Rappels, OCR et sauvegardes ne tournent plus : vérifiez les exécutants.", "sauvegarde.html#supervision"));
      if ((auto.travauxEnEchec || []).length) lignes.push(ligne("danger", "Travaux en échec", auto.travauxEnEchec.join(", "), "sauvegarde.html#supervision"));
      lignes.push(auto.sauvegardeAJour ? ligne("ok", "Sauvegarde à jour", "Dernière sauvegarde réussie dans le délai prévu.", "sauvegarde.html")
        : ligne("danger", "Sauvegarde en retard", "Aucune sauvegarde réussie récente : intervention requise.", "sauvegarde.html"));
    }
    if (data.alertesOuvertesCount) lignes.push(ligne("danger", data.alertesOuvertesCount + " alerte(s) de sécurité", "À examiner et marquer traitées.", "sauvegarde.html#supervision"));
    if (data.ocr && data.ocr.echecs) lignes.push(ligne("attention", data.ocr.echecs + " OCR en échec", "Texte non extrait : relancez l'OCR depuis la fiche.", "documents.html"));
    if (data.pendingIndexationCount) lignes.push(ligne("info", data.pendingIndexationCount + " document(s) à indexer", "File de numérisation.", "scan.html"));
    if (data.dossiersIncompletsCount) lignes.push(ligne("attention", data.dossiersIncompletsCount + " dossier(s) incomplet(s)", (data.piecesManquantesCount || 0) + " pièce(s) obligatoire(s) manquante(s).", "dossiers.html"));
    if ((data.echeances || []).length) lignes.push(ligne("info", data.echeances.length + " échéance(s) à venir", "Vos tâches ouvertes datées.", "taches.html"));
    hote.innerHTML = lignes.length ? lignes.join("") : ligne("ok", "Rien à signaler", "Aucune alerte en cours.", null);
  }

  function setupDashboard() {
    if (page.indexOf("index") !== 0) return;
    api("GET", "/dashboard/summary").then(function (data) {
      var elDocs = document.getElementById("statDocuments");
      var elDossiers = document.getElementById("statDossiers");
      var elUsers = document.getElementById("statUsers");
      if (elDocs) elDocs.textContent = data.documentsArchivedCount;
      if (elDossiers) elDossiers.textContent = data.dossiersCount;
      if (elUsers) elUsers.textContent = data.activeUsersCount;
      renderBackupTile();
      var heroTitle = document.querySelector(".hero h3");
      if (heroTitle) heroTitle.textContent = "Bonjour " + (__session.name || "");
      var heroSummary = document.getElementById("heroSummary");
      if (heroSummary) {
        var bits = [];
        if (data.pendingIndexationCount) bits.push(data.pendingIndexationCount + (data.pendingIndexationCount > 1 ? " nouveaux documents attendent une indexation" : " nouveau document attend une indexation"));
        if (data.pendingValidationCount) bits.push(data.pendingValidationCount + (data.pendingValidationCount > 1 ? " actes sont en attente de votre validation" : " acte est en attente de votre validation"));
        if (data.pendingAccessRequestCount) bits.push(data.pendingAccessRequestCount + (data.pendingAccessRequestCount > 1 ? " demandes d'accès attendent votre décision" : " demande d'accès attend votre décision"));
        heroSummary.textContent = bits.length ? "Votre étude a besoin de vous : " + bits.join(", ") + "." : "Votre étude est à jour.";
      }
      var alertsWrap = document.getElementById("importantAlerts");
      var alertsList = document.getElementById("importantAlertsList");
      if (alertsWrap && alertsList) {
        var rows = [];
        if (data.pendingValidationCount) rows.push(alertRow(NOTIF_ICONS.document, "Actes à valider", data.pendingValidationCount + (data.pendingValidationCount > 1 ? " documents nécessitent votre validation." : " document nécessite votre validation."), "documents.html"));
        if (data.pendingAccessRequestCount) rows.push(alertRow(NOTIF_ICONS.permission_granted, "Demandes d'accès à traiter", data.pendingAccessRequestCount + (data.pendingAccessRequestCount > 1 ? " demandes attendent une décision." : " demande attend une décision."), "permissions.html"));
        if (rows.length) {
          alertsList.innerHTML = rows.join("");
          alertsWrap.style.display = "";
        } else {
          alertsWrap.style.display = "none";
        }
      }
      renderDocuments(data.recentDocuments);
      renderAlertesReelles(data);
    });
  }

  setupChips();
  setupDashboard();
})();
