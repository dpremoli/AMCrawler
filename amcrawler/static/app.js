(function () {
  "use strict";

  // POST (with optional form fields) and resolve {ok, error}; never rejects.
  function request(url, fields) {
    var init = { method: "POST" };
    if (fields) { init.body = new URLSearchParams(fields); }
    return fetch(url, init)
      .then(function (r) {
        return r.json().then(function (b) {
          return { ok: r.ok && b.ok === true, error: b.error || null };
        });
      })
      .catch(function () { return { ok: false, error: null }; });
  }
  function post(url) {
    return request(url).then(function (r) { return r.ok; });
  }

  // View toggle: list, map or calendar; every page load opens on the list. map.js and
  // calendar.js listen for "amc:view". On desktop the list is always shown, so "list" means the map.
  var layout = document.querySelector(".layout");
  var segButtons = document.querySelectorAll(".seg-btn");
  var desktop = window.matchMedia ? window.matchMedia("(min-width: 900px)") : null;
  var view = "list";

  function paintSeg() {
    var shown = desktop && desktop.matches && view === "list" ? "map" : view;
    segButtons.forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-view") === shown));
    });
  }
  function setView(v) {
    if (!layout) { return; }
    view = v;
    layout.setAttribute("data-view", v);
    paintSeg();
    document.dispatchEvent(new CustomEvent("amc:view", { detail: v }));
  }
  segButtons.forEach(function (b) {
    b.addEventListener("click", function () { setView(b.getAttribute("data-view")); });
  });
  if (desktop && desktop.addEventListener) { desktop.addEventListener("change", paintSeg); }
  // a reload made by the page itself (pin, Alert) comes back to the same view; calendar.js
  // has already read the month and day from the same saved state
  var restored = window.amcRestore || null;
  try { window.sessionStorage.removeItem("amc-restore"); } catch (e) { /* nothing saved */ }
  setView(restored ? restored.view : "list");

  function reloadHere() {
    try {
      window.sessionStorage.setItem("amc-restore", JSON.stringify({
        url: window.location.href, view: view,
        month: layout && layout.getAttribute("data-cal-month"),
        selected: layout && layout.getAttribute("data-cal-selected")
      }));
    } catch (e) { /* private mode: reloads onto the list */ }
    window.location.reload();
  }

  // Theme (Impostazioni): auto follows the device; light/dark are stored per device.
  // base.html applies the stored choice before the stylesheet loads.
  var themeInputs = document.querySelectorAll('input[name="theme"]');
  if (themeInputs.length) {
    var current = document.documentElement.getAttribute("data-theme") || "auto";
    themeInputs.forEach(function (input) {
      input.checked = input.value === current;
      input.addEventListener("change", function () {
        var root = document.documentElement;
        if (input.value === "auto") { root.removeAttribute("data-theme"); }
        else { root.setAttribute("data-theme", input.value); }
        try {
          if (input.value === "auto") { window.localStorage.removeItem("amc-theme"); }
          else { window.localStorage.setItem("amc-theme", input.value); }
        } catch (e) { /* private mode: applies to this page only */ }
        document.dispatchEvent(new CustomEvent("amc:theme", { detail: input.value }));
      });
    });
  }

  // Filter bottom sheet.
  var sheet = document.getElementById("filters");
  var openFilters = document.getElementById("open-filters");
  var closeFilters = document.getElementById("close-filters");
  if (sheet && openFilters) {
    openFilters.addEventListener("click", function () { sheet.showModal(); });
  }
  if (sheet && closeFilters) {
    closeFilters.addEventListener("click", function () { sheet.close(); });
  }

  // Notes: tap to expand.
  document.addEventListener("click", function (e) {
    var notes = e.target.closest && e.target.closest(".notes");
    if (!notes) { return; }
    notes.setAttribute("aria-expanded", String(notes.getAttribute("aria-expanded") !== "true"));
  });
  document.addEventListener("keydown", function (e) {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList &&
        e.target.classList.contains("notes")) {
      e.preventDefault();
      e.target.click();
    }
  });

  // Hide / unhide with a 5 s undo snackbar.
  var snackbar = document.getElementById("snackbar");
  var snackText = document.getElementById("snackbar-text");
  var undoBtn = document.getElementById("snackbar-undo");
  var timer = null;
  var undo = null;

  function closeSnackbar() {
    if (!snackbar) { return; }
    snackbar.hidden = true;
    undo = null;
    if (timer) { clearTimeout(timer); timer = null; }
  }
  function showSnackbar(text, onUndo) {
    if (!snackbar) { return; }
    closeSnackbar();
    snackText.textContent = text;
    undoBtn.hidden = !onUndo;
    undo = onUndo;
    snackbar.hidden = false;
    timer = setTimeout(closeSnackbar, 5000);
  }
  if (undoBtn) {
    undoBtn.addEventListener("click", function () {
      var fn = undo;
      closeSnackbar();
      if (fn) { fn(); }
    });
  }

  // The card in the main list and, for a pinned Departure, its row in Fissati.
  function mainCard(id) {
    return Array.prototype.filter.call(document.querySelectorAll("#lista > .card"),
      function (c) { return c.getAttribute("data-id") === id; })[0] || null;
  }
  function pinRow(id) {
    var found = null;
    document.querySelectorAll(".pins .prow").forEach(function (row) {
      var c = row.querySelector(".card");
      if (c && c.getAttribute("data-id") === id) { found = row; }
    });
    return found;
  }
  // Fissati follows its rows: the count shows what is left and an empty block goes away.
  function syncPins() {
    var block = document.querySelector("details.pins");
    if (!block) { return; }
    var n = block.querySelectorAll(".prow").length;
    if (n === 0) {
      var divider = document.querySelector(".divider");
      block.remove();
      if (divider) { divider.remove(); }
    } else {
      block.querySelector(".count").textContent = "(" + n + ")";
    }
  }

  function moveCard(id, action, reverse, text) {
    var card = mainCard(id);
    if (!card) { return; }
    var parent = card.parentNode;
    var next = card.nextSibling;
    // a pin and an Alert go with a hidden Departure and are not restored by Annulla
    var flagged = card.hasAttribute("data-flagged");
    post("/" + action + "/" + encodeURIComponent(id)).then(function (ok) {
      if (!ok) { showSnackbar("Operazione non riuscita", null); return; }
      var row = pinRow(id);
      card.remove();
      if (row) { row.remove(); syncPins(); }
      var shown = action === "hide" && flagged ? "Nascosta. Fissata e avviso rimossi." : text;
      showSnackbar(shown, function () {
        post("/" + reverse + "/" + encodeURIComponent(id)).then(function (undone) {
          if (!undone) { showSnackbar("Operazione non riuscita", null); }
          else if (flagged) { reloadHere(); }
          else { parent.insertBefore(card, next); }
        });
      });
    });
  }

  // Fissati: open or closed is remembered per device; a row opens its card in place.
  var pins = document.querySelector("details.pins");
  if (pins) {
    try {
      if (window.localStorage.getItem("amc-pins-open") === "0") { pins.open = false; }
    } catch (e) { /* private mode: stays open */ }
    pins.addEventListener("toggle", function () {
      try { window.localStorage.setItem("amc-pins-open", pins.open ? "1" : "0"); }
      catch (e) { /* private mode: not remembered */ }
    });
  }
  document.addEventListener("click", function (e) {
    var row = e.target.closest && e.target.closest(".prow-btn");
    if (!row) { return; }
    var body = row.parentNode.querySelector(".open-card");
    body.hidden = !body.hidden;
    row.setAttribute("aria-expanded", String(!body.hidden));
  });

  // Bell sheet: one dialog for every card; a stepper picks the Participant number.
  var bell = document.getElementById("bell");
  var bellId = null;
  var bellMin = 0;
  var bellMax = 0;
  var bellN = 0;
  var bellError = document.getElementById("bell-error");
  function bellPaint() {
    document.getElementById("bell-num").textContent = String(bellN);
    document.getElementById("bell-minus").disabled = bellN <= bellMin;
    document.getElementById("bell-plus").disabled = bellN >= bellMax;
    document.getElementById("bell-save").disabled = bellMin > bellMax;
  }
  function bellOpen(btn) {
    var participants = parseInt(btn.getAttribute("data-participants"), 10);
    var seats = parseInt(btn.getAttribute("data-max"), 10);
    var current = parseInt(btn.getAttribute("data-threshold"), 10);
    bellId = btn.getAttribute("data-id");
    bellMin = participants + 1;
    bellMax = seats > 0 ? Math.min(seats, 99) : 99;
    bellN = current > 0 ? current : bellMin;
    bellN = Math.max(bellMin, Math.min(bellN, Math.max(bellMin, bellMax)));
    document.getElementById("bell-title").textContent =
      "Avvisami per " + btn.getAttribute("data-name");
    document.getElementById("bell-now").textContent = "Ora: " + participants + " iscritt" +
      (participants === 1 ? "o" : "i") + (seats > 0 ? " su " + seats + " posti." : ".");
    document.getElementById("bell-remove").hidden = !(current > 0);
    bellError.hidden = true;
    bellPaint();
    bell.showModal();
  }
  function bellFail(r) {
    bellError.textContent = r.error || "Operazione non riuscita";
    bellError.hidden = false;
  }
  if (bell) {
    document.getElementById("bell-minus").addEventListener("click", function () {
      if (bellN > bellMin) { bellN -= 1; bellPaint(); }
    });
    document.getElementById("bell-plus").addEventListener("click", function () {
      if (bellN < bellMax) { bellN += 1; bellPaint(); }
    });
    document.getElementById("bell-cancel").addEventListener("click", function () { bell.close(); });
    document.getElementById("bell-save").addEventListener("click", function () {
      request("/alert/" + encodeURIComponent(bellId), { threshold: String(bellN) })
        .then(function (r) { if (r.ok) { reloadHere(); } else { bellFail(r); } });
    });
    document.getElementById("bell-remove").addEventListener("click", function () {
      request("/alert/" + encodeURIComponent(bellId) + "/delete")
        .then(function (r) { if (r.ok) { reloadHere(); } else { bellFail(r); } });
    });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("button[data-action]");
    if (!btn) { return; }
    var action = btn.getAttribute("data-action");
    var id = btn.getAttribute("data-id");
    if (action === "hide") { moveCard(id, "hide", "unhide", "Partenza nascosta"); }
    else if (action === "unhide") { moveCard(id, "unhide", "hide", "Partenza ripristinata"); }
    else if (action === "bell") { bellOpen(btn); }
    else if (action === "pin" || action === "unpin") {
      post("/" + action + "/" + encodeURIComponent(id)).then(function (ok) {
        if (ok) { reloadHere(); }
        else { showSnackbar("Operazione non riuscita", null); }
      });
    }
  });
})();
