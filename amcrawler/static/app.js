(function () {
  "use strict";

  function post(url) {
    return fetch(url, { method: "POST" })
      .then(function (r) {
        return r.json().then(function (b) { return r.ok && b.ok === true; });
      })
      .catch(function () { return false; });
  }

  // View toggle (phone): list or map. map.js listens for "amc:view".
  var layout = document.querySelector(".layout");
  var segButtons = document.querySelectorAll(".seg-btn");
  function setView(view) {
    if (!layout) { return; }
    layout.setAttribute("data-view", view);
    segButtons.forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-view") === view));
    });
    document.dispatchEvent(new CustomEvent("amc:view", { detail: view }));
  }
  segButtons.forEach(function (b) {
    b.addEventListener("click", function () { setView(b.getAttribute("data-view")); });
  });

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

  function moveCard(card, action, reverse, text) {
    var parent = card.parentNode;
    var next = card.nextSibling;
    var id = card.getAttribute("data-id");
    post("/" + action + "/" + encodeURIComponent(id)).then(function (ok) {
      if (!ok) { showSnackbar("Operazione non riuscita", null); return; }
      card.remove();
      showSnackbar(text, function () {
        post("/" + reverse + "/" + encodeURIComponent(id)).then(function (undone) {
          if (undone) { parent.insertBefore(card, next); }
          else { showSnackbar("Operazione non riuscita", null); }
        });
      });
    });
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("button[data-action]");
    if (!btn) { return; }
    var card = btn.closest(".card");
    if (btn.getAttribute("data-action") === "hide") {
      moveCard(card, "hide", "unhide", "Partenza nascosta");
    } else {
      moveCard(card, "unhide", "hide", "Partenza ripristinata");
    }
  });
})();
