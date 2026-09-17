/*
 * GEDPdfViewer
 * ------------
 * Lecteur de documents intégré basé sur PDF.js (Mozilla).
 * Contrairement à un simple <iframe> qui délègue l'affichage au
 * navigateur, ce module CHARGE et PARSE réellement le fichier PDF :
 * - rendu de chaque page sur un <canvas>
 * - extraction du texte de chaque page (page.getTextContent) pour une
 *   couche de texte sélectionnable/copiable et une recherche interne
 * - navigation par page, zoom, gestion d'erreur avec repli propre
 *
 * Utilisation :
 *   window.GEDPdfViewer.mount(containerEl, {
 *     blob: blob,            // Blob du fichier (issu d'un fetch)
 *     filename: "acte.pdf",  // nom affiché en cas d'erreur
 *     contentType: "application/pdf"
 *   });
 */
(function () {
  "use strict";

  var PDFJS_VERSION = "3.11.174";
  var WORKER_SRC = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/" + PDFJS_VERSION + "/pdf.worker.min.js";

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function ensureWorker() {
    if (window.pdfjsLib && window.pdfjsLib.GlobalWorkerOptions && !window.pdfjsLib.GlobalWorkerOptions.workerSrc) {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = WORKER_SRC;
    }
  }

  function toArrayBuffer(opts) {
    if (opts.arrayBuffer) return Promise.resolve(opts.arrayBuffer);
    if (opts.blob) return opts.blob.arrayBuffer();
    if (opts.url) return fetch(opts.url).then(function (r) {
      if (!r.ok) throw new Error("fetch-failed");
      return r.arrayBuffer();
    });
    return Promise.reject(new Error("no-source"));
  }

  var TOOLBAR_HTML =
    '<div class="doc-viewer-toolbar">' +
      '<button type="button" class="iconbtn dv-prev" aria-label="Page pr\u00e9c\u00e9dente"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg></button>' +
      '<span class="doc-viewer-pages"><span class="dv-page-num">1</span> / <span class="dv-page-count">\u2014</span></span>' +
      '<button type="button" class="iconbtn dv-next" aria-label="Page suivante"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg></button>' +
      '<span class="doc-viewer-sep"></span>' +
      '<button type="button" class="iconbtn dv-zoom-out" aria-label="Zoom arri\u00e8re">\u2212</button>' +
      '<span class="doc-viewer-zoom">100%</span>' +
      '<button type="button" class="iconbtn dv-zoom-in" aria-label="Zoom avant">+</button>' +
      '<span class="doc-viewer-sep"></span>' +
      '<button type="button" class="iconbtn dv-search" aria-label="Rechercher dans le document"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg></button>' +
      '<button type="button" class="iconbtn dv-fullscreen" aria-label="Voir en plein \u00e9cran"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg></button>' +
      '<button type="button" class="iconbtn dv-close-fs" aria-label="Quitter le plein \u00e9cran"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button>' +
    "</div>" +
    '<div class="doc-viewer-search" hidden><input type="text" class="dv-search-input" placeholder="Rechercher un mot dans le document\u2026"><span class="dv-search-count muted"></span></div>';

  function mountPdf(container, opts) {
    container.innerHTML =
      '<div class="doc-viewer">' +
        TOOLBAR_HTML +
        '<div class="doc-viewer-canvas-wrap"><div class="doc-viewer-page"><canvas class="dv-canvas"></canvas><div class="textLayer dv-text-layer"></div></div></div>' +
        '<div class="doc-viewer-status dv-status">Chargement du document\u2026</div>' +
      "</div>";

    var els = {
      root: container.querySelector(".doc-viewer"),
      prev: container.querySelector(".dv-prev"),
      next: container.querySelector(".dv-next"),
      pageNum: container.querySelector(".dv-page-num"),
      pageCount: container.querySelector(".dv-page-count"),
      zoomOut: container.querySelector(".dv-zoom-out"),
      zoomIn: container.querySelector(".dv-zoom-in"),
      zoomLevel: container.querySelector(".doc-viewer-zoom"),
      wrap: container.querySelector(".doc-viewer-page"),
      canvas: container.querySelector(".dv-canvas"),
      textLayer: container.querySelector(".dv-text-layer"),
      status: container.querySelector(".dv-status"),
      searchBtn: container.querySelector(".dv-search"),
      searchBar: container.querySelector(".doc-viewer-search"),
      searchInput: container.querySelector(".dv-search-input"),
      searchCount: container.querySelector(".dv-search-count")
    };

    var BASE_SCALE = 1.15;
    var state = { pdf: null, pageNum: 1, scale: BASE_SCALE, minScale: 0.55, maxScale: 3, rendering: false, pending: null, fullText: [] };

    function setStatus(msg, isError) {
      els.status.textContent = msg || "";
      els.status.style.display = msg ? "flex" : "none";
      if (els.root) els.root.classList.toggle("dv-error", !!isError);
    }

    function renderPage(num) {
      if (!state.pdf) return;
      if (state.rendering) { state.pending = num; return; }
      state.rendering = true;
      state.pdf.getPage(num).then(function (page) {
        var pixelRatio = window.devicePixelRatio || 1;
        var renderViewport = page.getViewport({ scale: state.scale * pixelRatio });
        var cssViewport = page.getViewport({ scale: state.scale });
        var ctx = els.canvas.getContext("2d");
        els.canvas.width = renderViewport.width;
        els.canvas.height = renderViewport.height;
        els.canvas.style.width = cssViewport.width + "px";
        els.canvas.style.height = cssViewport.height + "px";
        els.wrap.style.width = cssViewport.width + "px";
        els.wrap.style.height = cssViewport.height + "px";

        return page.render({ canvasContext: ctx, viewport: renderViewport }).promise
          .then(function () { return page.getTextContent(); })
          .then(function (textContent) {
            els.textLayer.innerHTML = "";
            els.textLayer.style.width = cssViewport.width + "px";
            els.textLayer.style.height = cssViewport.height + "px";
            els.textLayer.style.setProperty("--scale-factor", String(state.scale));
            if (window.pdfjsLib.renderTextLayer) {
              return window.pdfjsLib.renderTextLayer({
                textContentSource: textContent,
                container: els.textLayer,
                viewport: cssViewport
              }).promise;
            }
          });
      }).then(function () {
        state.rendering = false;
        if (state.pending !== null) { var next = state.pending; state.pending = null; renderPage(next); }
      }).catch(function () {
        state.rendering = false;
        setStatus("Impossible d'afficher cette page du document.", true);
      });
    }

    function goTo(num) {
      if (!state.pdf) return;
      num = Math.max(1, Math.min(state.pdf.numPages, num));
      state.pageNum = num;
      els.pageNum.textContent = String(num);
      renderPage(num);
    }

    function setZoom(scale) {
      state.scale = Math.max(state.minScale, Math.min(state.maxScale, scale));
      els.zoomLevel.textContent = Math.round((state.scale / BASE_SCALE) * 100) + "%";
      renderPage(state.pageNum);
    }

    function extractFullText(pdf) {
      var chain = Promise.resolve([]);
      var _loop = function (num) {
        chain = chain.then(function (acc) {
          return pdf.getPage(num).then(function (page) { return page.getTextContent(); }).then(function (tc) {
            acc.push({ page: num, text: tc.items.map(function (it) { return it.str; }).join(" ") });
            return acc;
          });
        });
      };
      for (var n = 1; n <= pdf.numPages; n++) _loop(n);
      return chain;
    }

    els.prev.addEventListener("click", function () { goTo(state.pageNum - 1); });
    els.next.addEventListener("click", function () { goTo(state.pageNum + 1); });
    els.zoomOut.addEventListener("click", function () { setZoom(state.scale - 0.15); });
    els.zoomIn.addEventListener("click", function () { setZoom(state.scale + 0.15); });
    els.searchBtn.addEventListener("click", function () {
      if (els.searchBar.hasAttribute("hidden")) { els.searchBar.removeAttribute("hidden"); els.searchInput.focus(); }
      else { els.searchBar.setAttribute("hidden", ""); }
    });

    var fsBtn = container.querySelector(".dv-fullscreen");
    var closeFsBtn = container.querySelector(".dv-close-fs");
    if (fsBtn) fsBtn.addEventListener("click", function () { toggleFullscreen(container); });
    if (closeFsBtn) closeFsBtn.addEventListener("click", function () { toggleFullscreen(container); });

    var searchDebounce = null;
    els.searchInput.addEventListener("input", function () {
      clearTimeout(searchDebounce);
      var q = els.searchInput.value.trim().toLowerCase();
      searchDebounce = setTimeout(function () {
        if (!q) { els.searchCount.textContent = ""; return; }
        if (!state.fullText.length) { els.searchCount.textContent = "Indexation du texte en cours\u2026"; return; }
        var matches = state.fullText.filter(function (p) { return p.text.toLowerCase().indexOf(q) !== -1; });
        els.searchCount.textContent = matches.length
          ? matches.length + " page(s) contiennent \u00ab\u00a0" + q + "\u00a0\u00bb \u2014 ex. page " + matches[0].page
          : "Aucun r\u00e9sultat";
      }, 250);
    });

    setStatus("Chargement du document\u2026");

    if (!window.pdfjsLib) {
      setStatus("Le lecteur de document n'a pas pu se charger. Vérifiez votre connexion puis réessayez.", true);
      return Promise.reject(new Error("pdfjs-not-loaded"));
    }
    ensureWorker();

    return toArrayBuffer(opts).then(function (buf) {
      return window.pdfjsLib.getDocument({ data: buf }).promise;
    }).then(function (pdf) {
      state.pdf = pdf;
      els.pageCount.textContent = String(pdf.numPages);
      setStatus("");
      goTo(1);
      extractFullText(pdf).then(function (fullText) { state.fullText = fullText; });
      return pdf;
    }).catch(function (err) {
      setStatus("Impossible d'ouvrir ce document (fichier corrompu ou format non pris en charge).", true);
      throw err;
    });
  }

  // Ferme le mode plein écran de repli (fallback) sur la touche Échap.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var el = document.querySelector(".doc-viewer.dv-fullscreen-fallback");
    if (el) el.classList.remove("dv-fullscreen-fallback");
  });

  function isInFullscreen(root) {
    return document.fullscreenElement === root || root.classList.contains("dv-fullscreen-fallback");
  }

  /**
   * Bascule l'affichage plein écran du lecteur PDF monté dans `container`
   * (utilise l'API Fullscreen native du navigateur, avec un repli en
   * position fixe si l'API n'est pas disponible).
   */
  function toggleFullscreen(container) {
    var root = container.querySelector(".doc-viewer");
    if (!root) return false;

    if (isInFullscreen(root)) {
      if (document.fullscreenElement === root && document.exitFullscreen) document.exitFullscreen();
      root.classList.remove("dv-fullscreen-fallback");
      return true;
    }

    if (root.requestFullscreen) {
      root.requestFullscreen().catch(function () { root.classList.add("dv-fullscreen-fallback"); });
    } else if (root.webkitRequestFullscreen) {
      root.webkitRequestFullscreen();
    } else {
      root.classList.add("dv-fullscreen-fallback");
    }
    return true;
  }

  function mountImage(container, opts) {
    var url = opts.blob ? URL.createObjectURL(opts.blob) : opts.url;
    container.innerHTML =
      '<div class="doc-viewer doc-viewer--image"><img class="dv-image" alt="' + escapeHtml(opts.filename || "Document") + '"></div>';
    var img = container.querySelector(".dv-image");
    return new Promise(function (resolve, reject) {
      img.onload = function () { resolve(); };
      img.onerror = function () { reject(new Error("image-load-failed")); };
      img.src = url;
    });
  }

  function mount(container, opts) {
    opts = opts || {};
    var onError = typeof opts.onError === "function" ? opts.onError : function () {};
    var contentType = (opts.contentType || (opts.blob && opts.blob.type) || "").toLowerCase();

    var task = contentType.indexOf("image/") === 0 ? mountImage(container, opts) : mountPdf(container, opts);
    task.catch(function (err) { onError(err); });
    return task;
  }

  window.GEDPdfViewer = { mount: mount, toggleFullscreen: toggleFullscreen };
})();
