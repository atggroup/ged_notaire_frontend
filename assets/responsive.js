/* ============================================================================
   GED Notaire — Comportements responsive partagés (3 portails)
   Chargé APRÈS app.js. Ne touche à rien au-dessus de 1024px.
   1. Bouton « recherche » dans la barre du haut (mobile) ;
   2. Étiquetage des cellules de tableau (affichage en cartes < 720px) ;
   3. Verrouillage du défilement quand le tiroir de navigation est ouvert ;
   4. Fermeture du tiroir à la touche Échap / au retour en desktop.
   ========================================================================== */
(function () {
  "use strict";

  var MOBILE = "(max-width:1024px)";
  var app = document.querySelector(".app");
  var top = document.querySelector(".top");
  if (!app || !top) return;

  /* ---------- 1. Recherche repliée derrière une icône sur mobile ---------- */
  function setupSearchToggle() {
    var search = top.querySelector(".search");
    if (!search) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "iconbtn search-toggle";
    btn.setAttribute("aria-label", "Rechercher");
    btn.setAttribute("aria-expanded", "false");
    btn.innerHTML =
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>';
    search.insertAdjacentElement("beforebegin", btn);

    btn.addEventListener("click", function () {
      var open = app.classList.toggle("search-open");
      btn.setAttribute("aria-expanded", String(open));
      if (open) {
        var input = search.querySelector("input");
        if (input) input.focus();
      }
    });
  }

  /* ---------- 2. Tableaux → cartes empilées sous 720px ----------
     Chaque <td> reçoit l'intitulé de sa colonne dans data-label ; le CSS
     l'affiche en préfixe une fois le tableau passé en blocs. Les cellules
     fusionnées (états vides, lignes d'action) sont marquées pleine largeur. */
  function labelCells(table) {
    var heads = [].map.call(table.querySelectorAll("thead th"), function (th) {
      return (th.textContent || "").trim();
    });
    if (!heads.length) return;
    [].forEach.call(table.querySelectorAll("tbody tr"), function (tr) {
      var i = 0;
      [].forEach.call(tr.children, function (td) {
        if (td.tagName !== "TD") return;
        var span = parseInt(td.getAttribute("colspan") || "1", 10) || 1;
        var label = heads[i] || "";
        // Colonne d'actions sans intitulé, ou cellule fusionnée : pleine largeur
        if (span > 1 || !label) td.setAttribute("data-fullrow", "");
        else td.setAttribute("data-label", label);
        i += span;
      });
    });
  }

  function labelAllTables(root) {
    [].forEach.call((root || document).querySelectorAll("table"), labelCells);
  }

  /* app.js réinjecte les <tbody> après chaque appel API : on ré-étiquette. */
  function watchTables() {
    labelAllTables();
    if (!window.MutationObserver) return;
    var pending = null;
    var mo = new MutationObserver(function (records) {
      var touched = false;
      for (var i = 0; i < records.length; i++) {
        var t = records[i].target;
        if (t && t.closest && t.closest("table")) { touched = true; break; }
      }
      if (!touched) return;
      clearTimeout(pending);
      pending = setTimeout(labelAllTables, 50);
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  /* ---------- 3 & 4. Tiroir : verrou de défilement, Échap, resize ---------- */
  function setupDrawer() {
    var html = document.documentElement;

    function sync() {
      var open = app.classList.contains("nav-open") && window.matchMedia(MOBILE).matches;
      html.classList.toggle("nav-locked", open);
      var btn = top.querySelector(".menu-btn");
      if (btn) {
        btn.setAttribute("aria-expanded", String(open));
        btn.setAttribute("aria-label", open ? "Fermer le menu" : "Ouvrir le menu");
      }
    }

    if (window.MutationObserver) {
      new MutationObserver(sync).observe(app, { attributes: true, attributeFilter: ["class"] });
    }
    sync();

    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      app.classList.remove("nav-open");
      app.classList.remove("search-open");
      var st = top.querySelector(".search-toggle");
      if (st) st.setAttribute("aria-expanded", "false");
    });

    // Retour en desktop : on referme tout pour ne pas laisser d'état bloqué
    var mq = window.matchMedia(MOBILE);
    var onChange = function () {
      if (!mq.matches) {
        app.classList.remove("nav-open");
        app.classList.remove("search-open");
      }
      sync();
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }

  function init() {
    setupSearchToggle();
    watchTables();
    setupDrawer();
  }

  // app.js construit le bouton hamburger au chargement : on passe après lui.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { setTimeout(init, 0); });
  } else {
    setTimeout(init, 0);
  }
})();
