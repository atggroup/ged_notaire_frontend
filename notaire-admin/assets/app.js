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
  var ROLE_LABELS = { notaire: "Notaire \u00b7 Admin", clerc: "Clerc principal", collaborateur: "Collaborateur" };

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
        clearSession();
        toast("D\u00e9connexion r\u00e9ussie.", "ok");
        setTimeout(function () { location.href = "../login.html"; }, 350);
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
      if (res.status === 403) throw new Error("Droits insuffisants.");
      if (!res.ok) throw new Error("Erreur serveur (" + res.status + ").");
      var ct = res.headers.get("content-type") || "";
      return ct.indexOf("json") >= 0 ? res.json() : res.text();
    }).catch(function (err) {
      toast(err.message || "Échec de la requête.", "err");
      throw err;
    });
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

  function setupSearch() {
    document.querySelectorAll(".search input").forEach(function (input) {
      var q = params.get("q");
      if (q && page.indexOf("recherche") === 0) input.value = q;
      input.addEventListener("keydown", function (e) {
        if (e.key !== "Enter") return;
        e.preventDefault();
        var value = input.value.trim();
        if (page.indexOf("recherche") === 0) {
          filterDocs(value);
          api("GET", "/search?q=" + encodeURIComponent(value));
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
        filterDocs("", type);
        api("GET", "/documents?type=" + encodeURIComponent(type));
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

    document.querySelectorAll(".ask").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        var code = (a.closest(".doc") && a.closest(".doc").querySelector(".code") && a.closest(".doc").querySelector(".code").textContent.trim()) || params.get("ref") || "";
        requestAccess(code);
      });
    });

    document.querySelectorAll(".doc .fav").forEach(function (fav) {
      fav.style.cursor = "pointer";
      fav.addEventListener("click", function (e) {
        e.preventDefault();
        var code = fav.closest(".doc") && fav.closest(".doc").querySelector(".code");
        api("POST", "/documents/favorite", { ref: code ? code.textContent.trim() : "" }).then(function () {
          toast("Favori mis à jour.", "ok");
        });
      });
    });
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
    var btn = document.querySelector(".btn.pri");
    if (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var data = collectFields();
        var q = data.mot_cle_reference || data.q || "";
        api("GET", "/search?q=" + encodeURIComponent(q) + "&type=" + encodeURIComponent(data.type_d_acte || "")).then(function () {
          var n = filterDocs(q, data.type_d_acte || "Tous");
          toast(n + " résultat" + (n > 1 ? "s" : "") + " dans votre périmètre.", "ok");
        });
      });
    }
    if (params.get("q")) filterDocs(params.get("q"));
  }

  function setupProfil() {
    if (page.indexOf("profil") !== 0) return;
    // La déconnexion est gérée par wireLogout() via #logoutLink (session réelle + redirection vers ../login.html).
    var saveTimer;
    document.querySelectorAll(".inp").forEach(function (inp) {
      inp.addEventListener("change", function () {
        clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
          api("PATCH", "/me", collectFields()).then(function () { toast("Profil mis à jour.", "ok"); });
        }, 200);
      });
    });
  }

  function setupScan() {
    if (page.indexOf("scan") !== 0) return;
    if (!can("scan")) return deny("La numérisation n'est pas dans votre socle d'accès.");
    var btn = Array.from(document.querySelectorAll("button.btn.pri")).find(function (b) { return /archiver/i.test(b.textContent); });
    if (btn) {
      btn.addEventListener("click", function () {
        var data = collectFields();
        if (/confidentiel/i.test(data.niveau_de_confidentialite || "") && !can("validateActs")) {
          return deny("Seul le notaire archive un acte confidentiel.");
        }
        api("POST", "/documents/archive", data).then(function () {
          toast("Document envoyé à l'archivage (PDF/A + empreinte).", "ok");
        });
      });
    }
    document.querySelectorAll("table .iconbtn").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        var name = a.closest("tr") && a.closest("tr").querySelector(".tname");
        api("GET", "/documents/queue/" + encodeURIComponent((name && name.textContent.trim()) || "")).then(function () {
          toast("Ouverture de la file d'indexation.", "ok");
        });
      });
    });
  }

  function setupDossiers() {
    if (page.indexOf("dossiers") !== 0) return;
    if (!can("dossiers")) return;
    var add = Array.from(document.querySelectorAll(".btn.pri")).find(function (b) { return /nouveau dossier/i.test(b.textContent); });
    if (add) {
      add.addEventListener("click", function (e) {
        e.preventDefault();
        if (!can("createDossier")) return deny("La création de dossier est réservée au notaire.");
        openModal({
          title: "Nouveau dossier",
          ok: "Créer",
          bodyHtml:
            '<div class="field"><label>Client</label><input class="inp" name="client" placeholder="Nom du client"></div>' +
            '<div class="field"><label>Objet</label><input class="inp" name="objet" placeholder="Vente, donation…"></div>',
          onOk: function (payload) {
            return api("POST", "/dossiers", payload).then(function () { toast("Dossier créé.", "ok"); });
          }
        });
      });
    }
  }

  function setupUsers() {
    if (page.indexOf("utilisateurs") !== 0) return;
    if (!can("users")) return deny("La gestion des comptes est réservée au notaire.");
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
            });
          }
        });
      });
    }
  }

  function setupPermissions() {
    if (page.indexOf("permissions") !== 0) return;
    if (!can("permissions")) return deny("L'attribution des accès est réservée au notaire.");
    var btn = Array.from(document.querySelectorAll("button.btn.pri")).find(function (b) { return /ouvrir l'accès/i.test(b.textContent); });
    if (btn) {
      btn.addEventListener("click", function () {
        var data = collectFields();
        if (!data.motif_obligatoire && !data.motif) {
          toast("Le motif est obligatoire.", "err");
          return;
        }
        api("POST", "/permissions", data).then(function () { toast("Accès attribué.", "ok"); });
      });
    }
    document.querySelectorAll("table .iconbtn").forEach(function (a) {
      a.addEventListener("click", function (e) {
        e.preventDefault();
        openModal({
          title: "Révoquer l'accès",
          text: "Cette révocation est journalisée.",
          ok: "Révoquer",
          onOk: function () {
            var row = a.closest("tr");
            var who = row && row.querySelector(".tname");
            return api("DELETE", "/permissions", { beneficiaire: who ? who.textContent.trim() : "" }).then(function () {
              toast("Accès révoqué.", "ok");
            });
          }
        });
      });
    });
  }

  function setupConfig() {
    if (page.indexOf("configuration") !== 0) return;
    if (!can("config")) return deny("La configuration du cabinet est réservée au notaire.");
    document.querySelectorAll(".pill-tabs .pt").forEach(function (pt) {
      pt.addEventListener("click", function () {
        pt.parentElement.querySelectorAll(".pt").forEach(function (p) { p.classList.remove("on"); });
        pt.classList.add("on");
      });
    });
    var btn = Array.from(document.querySelectorAll("button.btn.pri")).find(function (b) { return /enregistrer/i.test(b.textContent); });
    if (btn) {
      btn.addEventListener("click", function () {
        api("PUT", "/settings", collectFields()).then(function () { toast("Configuration enregistrée.", "ok"); });
      });
    }
  }

  function setupBackup() {
    if (page.indexOf("sauvegarde") !== 0) return;
    if (!can("backup")) return deny("La sauvegarde est réservée au notaire.");
    var btn = Array.from(document.querySelectorAll("button.btn")).find(function (b) { return /restauration/i.test(b.textContent); });
    if (!btn) return;
    btn.addEventListener("click", function () {
      openModal({
        title: "Restauration test",
        text: "Un test de restauration sera lancé sans écraser les données de production.",
        ok: "Lancer",
        onOk: function () {
          return api("POST", "/backups/restore-test", {}).then(function () {
            toast("Restauration test lancée.", "ok");
          });
        }
      });
    });
  }

  function setupAudit() {
    if (page.indexOf("journal") !== 0) return;
    if (!can("audit")) return;
    var exportBtn = Array.from(document.querySelectorAll("a.btn,.btn")).find(function (b) { return /export csv/i.test(b.textContent); });
    if (exportBtn) {
      exportBtn.addEventListener("click", function (e) {
        e.preventDefault();
        api("GET", "/audit/export?scope=" + (cfg.role === "notaire" ? "cabinet" : "me")).then(function () {
          toast("Export CSV préparé.", "ok");
        });
      });
    }
  }

  function setupDocumentDetail() {
    if (page.indexOf("document-detail") !== 0) return;
    var ref = params.get("ref");
    if (ref) {
      document.querySelectorAll(".kv").forEach(function (kv) {
        if (/référence/i.test(kv.textContent) && kv.querySelector("b")) kv.querySelector("b").textContent = ref;
      });
    }
    Array.from(document.querySelectorAll("a.btn,button.btn")).forEach(function (el) {
      el.addEventListener("click", function (e) {
        var label = el.textContent.trim();
        if (/demander l'accès/i.test(label)) {
          e.preventDefault();
          requestAccess(ref || "");
        } else if (/valider l'acte/i.test(label)) {
          e.preventDefault();
          if (!can("validateActs")) return deny("Seul le notaire valide un acte.");
          api("POST", "/documents/" + encodeURIComponent(ref || "current") + "/validate", {}).then(function () {
            toast("Acte validé.", "ok");
          });
        } else if (/exporter/i.test(label)) {
          e.preventDefault();
          api("GET", "/documents/" + encodeURIComponent(ref || "current") + "/export").then(function () {
            toast("Export PDF/A lancé.", "ok");
          });
        } else if (/imprimer/i.test(label)) {
          e.preventDefault();
          window.print();
        }
      });
    });
  }

  function setupNotifications() {
    if (page.indexOf("notifications") !== 0) return;
    document.querySelectorAll(".lrow").forEach(function (row) {
      row.style.cursor = "pointer";
      row.addEventListener("click", function () {
        api("PATCH", "/notifications/read", { title: (row.querySelector(".lt") && row.querySelector(".lt").textContent) || "" }).then(function () {
          toast("Notification marquée comme lue.", "ok");
        });
      });
    });
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
            return api("GET", "/documents?niveau=" + encodeURIComponent(payload.niveau || "")).then(function () {
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
  if (!guardPage()) return;
  applySessionIdentity(__session);
  wireLogout();
  setupNav();
  setupSearch();
  setupSegs();
  setupDocs();
  setupTables();
  setupRecherche();
  setupProfil();
  setupScan();
  setupDossiers();
  setupUsers();
  setupPermissions();
  setupConfig();
  setupBackup();
  setupAudit();
  setupDocumentDetail();
  setupNotifications();
  setupAccessLinks();
  setupFilterBtn();
  setupChips();
})();
