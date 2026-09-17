(function () {
  var cfg = Object.assign(
    { apiBase: "/api", useMock: true, role: "collaborateur", capabilities: {}, token: "" },
    window.GED_CONFIG || {}
  );
  var page = (location.pathname.split("/").pop() || "index.html").toLowerCase();
  var params = new URLSearchParams(location.search);

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
        if (!res.ok) throw new Error(data.detail || "Le document n'a pas pu être archivé.");
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
      '<button type="button" class="btn" data-modal-cancel>Annuler</button>' +
      '<button type="button" class="btn pri" data-modal-ok>' + (opts.ok || "Confirmer") + "</button>" +
      "</div></div>";
    document.body.appendChild(back);
    back.addEventListener("click", function (e) { if (e.target === back) closeModal(); });
    back.querySelector("[data-modal-cancel]").addEventListener("click", closeModal);
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

  var QUEUE_STATUS_LABEL = { "brouillon": "Brouillon", "en_attente_validation": "À valider" };
  function queueRow(doc) {
    var label = QUEUE_STATUS_LABEL[doc.statut] || doc.statut || "À indexer";
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
  function setupScan() {
    if (page.indexOf("scan") !== 0) return;
    if (!can("scan")) return deny("La numérisation n'est pas dans votre socle d'accès.");
    loadQueue();
    populateReferentielSelects();
    var btn = Array.from(document.querySelectorAll("button.btn.pri")).find(function (b) { return /contrôle/i.test(b.textContent); });
    if (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var data = collectFields();
        var input = document.querySelector("[data-upload-file]");
        var file = input && input.files && input.files[0];
        if (!file) return toast("Choisissez le fichier à archiver.", "err");
        if (!data.type_code) return toast("Choisissez un type de document dans le référentiel.", "err");
        if (!data.dossier) return toast("Sélectionnez le dossier existant auquel rattacher ce document.", "err");
        if (/confidentiel/i.test(data.niveau_de_confidentialite || "") && !can("validateActs")) {
          return deny("Seul le notaire archive un acte confidentiel.");
        }
        var form = new FormData();
        form.append("fichier", file);
        form.append("type_code", data.type_code);
        form.append("niveau", data.niveau_de_confidentialite || "Standard");
        if (data.dossier) form.append("dossier", data.dossier);
        btn.disabled = true;
        apiMultipart("/documents/upload", form).then(function (doc) {
          toast("Document envoyé au contrôle : " + doc.reference, "ok");
          if (input) input.value = "";
          loadQueue();
        }).finally(function () {
          btn.disabled = false;
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

  function userRow(item) {
    var initialsTxt = initials(item.name);
    var scanBtn = item.role === "collaborateur"
      ? '<button type="button" class="iconbtn" style="width:34px;height:34px" data-toggle-scan data-id="' + item.id + '" data-can-scan="' + (item.canScan ? "1" : "0") + '" title="' + (item.canScan ? "Révoquer la numérisation" : "Autoriser la numérisation") + '" aria-label="' + (item.canScan ? "Révoquer la numérisation" : "Autoriser la numérisation") + '"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"' + (item.canScan ? ' style="color:var(--green)"' : '') + '><path d="M4 8V6a2 2 0 0 1 2-2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v2"/><path d="M20 16v2a2 2 0 0 1-2 2h-2"/><path d="M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M4 12h16"/></svg></button>'
      : "";
    var session = getSession();
    var isSelf = !!(session && session.email && item.email && String(session.email).toLowerCase() === String(item.email).toLowerCase());
    var activeBtn = isSelf ? "" : (
      item.isActive
        ? '<button type="button" class="iconbtn" style="width:34px;height:34px;color:var(--danger)" data-toggle-active data-id="' + item.id + '" data-is-active="1" data-name="' + escapeHtml(item.name) + '" title="Suspendre l\u2019accès" aria-label="Suspendre l\u2019accès"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="17" y1="8" x2="22" y2="13"/><line x1="22" y1="8" x2="17" y2="13"/></svg></button>'
        : '<button type="button" class="iconbtn" style="width:34px;height:34px;color:var(--green)" data-toggle-active data-id="' + item.id + '" data-is-active="0" data-name="' + escapeHtml(item.name) + '" title="Réactiver l\u2019accès" aria-label="Réactiver l\u2019accès"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><polyline points="16 11 18 13 22 9"/></svg></button>'
    );
    return '<tr><td class="tname"><span class="av" style="background:var(--primary-soft);color:var(--primary-600);border-radius:11px;width:34px;height:34px">' + escapeHtml(initialsTxt) + '</span>' + escapeHtml(item.name) + '</td>' +
      '<td>' + escapeHtml(item.roleLabel || item.role) + '</td><td><span class="b ' + (item.isActive ? "green" : "grey") + '">' + (item.isActive ? "Actif" : "Accès suspendu") + '</span></td>' +
      '<td style="display:flex;gap:6px"><a class="iconbtn" style="width:34px;height:34px" href="permissions.html"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z"/><path d="m9 12 2 2 4-4"/></svg></a>' + scanBtn + activeBtn + '</td></tr>';
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
      var fields = { cabinet_name: settings.cabinet_name, city: settings.city, codification_policy: settings.codification_policy, storage_mode: settings.storage_mode };
      Object.keys(fields).forEach(function (key) { var el = document.querySelector('[name="' + key + '"]'); if (el && fields[key] != null) el.value = fields[key]; });
    });
    Array.from(document.querySelectorAll("button.btn.pri")).filter(function (b) { return /enregistrer/i.test(b.textContent); }).forEach(function (btn) {
      btn.addEventListener("click", function () {
        api("PUT", "/settings", collectFields()).then(function () { toast("Configuration enregistrée.", "ok"); });
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
      var history = document.getElementById("backupHistoryBody");
      if (history) history.innerHTML = (data.history || []).length ? data.history.map(function (run) {
        var good = run.status === "success";
        return "<tr><td>" + escapeHtml(stamp(run.createdAt)) + "</td><td>Test d'intégrité</td><td>" + escapeHtml(formatBytes(run.checkedBytes)) + "</td><td><span class=\"b " + (good ? "green" : "warn") + "\">" + (good ? "Réussie" : "Échouée") + "</span></td></tr>";
      }).join("") : '<tr><td colspan="4" class="muted" style="text-align:center;padding:24px">Aucun test de restauration n\'a encore été exécuté.</td></tr>';
    }
    function loadBackup() { return api("GET", "/backups").then(renderBackup); }
    loadBackup();
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
    if (!cfg.useMock) api("GET", "/audit?scope=" + (cfg.role === "admin" ? "cabinet" : "me")).then(function (logs) {
      var card = document.querySelector(".wrap .card");
      if (!card || !Array.isArray(logs)) return;
      card.innerHTML = logs.length ? logs.map(function (log) {
        return '<div class="lrow"><span class="li" style="background:var(--primary-soft);color:var(--primary)">◷</span><div><div class="lt">' + escapeHtml(log.user) + '</div><div class="ls">' + escapeHtml(log.action.replace(/_/g, " ")) + (log.targetId ? " — " + escapeHtml(log.targetId) : "") + '</div></div><div class="lx"><span class="ls">' + docDate(log.timestamp) + '</span></div></div>';
      }).join("") : '<div class="lrow"><div><div class="lt">Aucune activité</div><div class="ls">Les actions réalisées apparaîtront ici.</div></div></div>';
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
              '<div class="field"><label><input type="checkbox" name="complete" checked> Document complet</label></div>' +
              '<div class="field"><label><input type="checkbox" name="ordered" checked> Pages dans le bon ordre</label></div>' +
              '<div class="field"><label><input type="checkbox" name="legible" checked> Lisible</label></div>' +
              '<div class="field"><label><input type="checkbox" name="noMissingPage" checked> Aucune page manquante</label></div>' +
              '<div class="field"><label><input type="checkbox" name="noDuplicate" checked> Aucun doublon</label></div>' +
              '<div class="field"><label><input type="checkbox" name="orientationCorrect" checked> Orientation correcte</label></div>' +
              '<div class="field"><label><input type="checkbox" name="dossierCorrect" checked> Bon dossier / bonne affaire</label></div>' +
              '<div class="field"><label>Notes (optionnel)</label><textarea class="inp" name="notes" rows="2"></textarea></div>',
            onOk: function (payload) {
              var checks = {};
              ["complete", "ordered", "legible", "noMissingPage", "noDuplicate", "orientationCorrect", "dossierCorrect"].forEach(function (key) {
                var box = document.querySelector('.modal-backdrop [name="' + key + '"]');
                checks[key] = box ? box.checked : true;
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

  var NOTIF_ICONS = {
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
    var icon = NOTIF_ICONS[item.type] || NOTIF_ICONS.document;
    return '<div class="lrow" data-id="' + item.id + '" style="cursor:pointer' + (item.read ? ";opacity:.6" : "") + '"><span class="li" style="background:' + icon.bg + ';color:' + icon.fg + '"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + icon.path + '</svg></span>' +
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
    if (page.indexOf("notifications") !== 0) return;
    loadNotifications();
    var host = document.getElementById("notificationsList");
    if (host) {
      host.addEventListener("click", function (e) {
        var row = e.target.closest(".lrow");
        if (!row) return;
        var id = row.getAttribute("data-id");
        api("PATCH", "/notifications/read", { id: id }).then(function () {
          row.style.opacity = ".6";
          refreshNotifDot();
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
  setupScan();
  setupDossiers();
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
  function alertRow(icon, title, message, href) {
    return '<a class="lrow" href="' + href + '" style="cursor:pointer;text-decoration:none;color:inherit"><span class="li" style="background:' + icon.bg + ';color:' + icon.fg + '"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + icon.path + '</svg></span>' +
      '<div><div class="lt">' + escapeHtml(title) + '</div><div class="ls">' + escapeHtml(message) + '</div></div></a>';
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
    });
  }

  setupChips();
  setupDashboard();
})();
