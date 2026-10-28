(function () {
  "use strict";

  var MONTHS = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio",
    "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"];
  var WEEKDAYS = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"];
  var LETTERS = ["L", "M", "M", "G", "V", "S", "D"];

  // ---- rules (pure; dates are "YYYY-MM-DD", months "YYYY-MM") ----

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function monthOf(date) { return date.slice(0, 7); }
  function parts(date) {
    return { y: +date.slice(0, 4), m: +date.slice(5, 7) - 1, d: +date.slice(8, 10) };
  }
  function shiftMonth(key, delta) {
    var total = +key.slice(0, 4) * 12 + (+key.slice(5, 7) - 1) + delta;
    return Math.floor(total / 12) + "-" + pad((total % 12) + 1);
  }
  function months(dates) {
    var seen = {};
    dates.forEach(function (d) { seen[monthOf(d)] = true; });
    return Object.keys(seen).sort();
  }
  // The current month if a Departure starts in it, else the first later month that has one.
  function openingMonth(today, dates) {
    var current = monthOf(today);
    var later = months(dates).filter(function (m) { return m >= current; });
    return later.length ? later[0] : current;
  }
  // Navigation runs from the current month to the last month with a Departure.
  function monthRange(today, dates) {
    var current = monthOf(today);
    var all = months(dates);
    var last = all.length ? all[all.length - 1] : current;
    return [current, last > current ? last : current];
  }
  function firstDayIn(month, dates) {
    var inMonth = dates.filter(function (d) { return monthOf(d) === month; }).sort();
    return inMonth.length ? inMonth[0] : null;
  }
  // Weeks of 7 day numbers, Monday first; 0 for days outside the month.
  function monthWeeks(year, month1) {
    var lead = (new Date(Date.UTC(year, month1 - 1, 1)).getUTCDay() + 6) % 7;
    var days = new Date(Date.UTC(year, month1, 0)).getUTCDate();
    var cells = [];
    var i;
    for (i = 0; i < lead; i++) { cells.push(0); }
    for (i = 1; i <= days; i++) { cells.push(i); }
    while (cells.length % 7) { cells.push(0); }
    var weeks = [];
    for (i = 0; i < cells.length; i += 7) { weeks.push(cells.slice(i, i + 7)); }
    return weeks;
  }
  function weekdayOf(date) {
    var p = parts(date);
    return (new Date(Date.UTC(p.y, p.m, p.d)).getUTCDay() + 6) % 7;
  }
  function plural(n) { return n + (n === 1 ? " partenza" : " partenze"); }
  function monthTitle(key) { return MONTHS[+key.slice(5, 7) - 1] + " " + key.slice(0, 4); }
  function dayLabel(date, count) {
    var p = parts(date);
    return p.d + " " + MONTHS[p.m].toLowerCase() + ", " + plural(count);
  }
  function selectionLine(date, count) {
    var p = parts(date);
    return WEEKDAYS[weekdayOf(date)] + " " + p.d + " " + MONTHS[p.m].toLowerCase() +
      " · " + plural(count);
  }

  // The selection survives its day losing every Departure; it only moves with the month.
  function keepSelection(month, selected, dates) {
    if (selected && monthOf(selected) === month) { return selected; }
    return firstDayIn(month, dates);
  }

  var api = {
    keepSelection: keepSelection, openingMonth: openingMonth, monthRange: monthRange, firstDayIn: firstDayIn,
    monthWeeks: monthWeeks, shiftMonth: shiftMonth, monthTitle: monthTitle,
    dayLabel: dayLabel, selectionLine: selectionLine
  };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  if (typeof document === "undefined") { return; }

  // ---- page ----

  var layout = document.querySelector(".layout");
  var list = document.getElementById("lista");
  var host = document.getElementById("cal");
  if (!layout || !list || !host) { return; }
  var empty = document.getElementById("cal-empty");
  var grid = document.getElementById("cal-grid");
  var title = document.getElementById("cal-title");
  var prev = document.getElementById("cal-prev");
  var next = document.getElementById("cal-next");
  var none = document.getElementById("cal-none");
  var line = document.getElementById("cal-sel");

  function localToday() {
    var n = new Date();
    return n.getFullYear() + "-" + pad(n.getMonth() + 1) + "-" + pad(n.getDate());
  }
  var today = layout.getAttribute("data-today") || localToday();
  var month = null;
  var selected = null;

  function cards() {
    return Array.prototype.slice.call(list.querySelectorAll(".card[data-start]"));
  }
  function countByDate() {
    var counts = {};
    cards().forEach(function (c) {
      var d = c.getAttribute("data-start");
      counts[d] = (counts[d] || 0) + 1;
    });
    return counts;
  }

  // On "cal" the list shows only the selected day's cards; elsewhere all of them.
  function filterList() {
    var on = layout.getAttribute("data-view") === "cal";
    cards().forEach(function (c) {
      c.hidden = on && c.getAttribute("data-start") !== selected;
    });
  }

  function paintSelection(counts) {
    Array.prototype.forEach.call(grid.querySelectorAll("button[data-date]"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-date") === selected));
    });
    line.textContent = selected ? selectionLine(selected, counts[selected] || 0) : "";
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined) { e.textContent = text; }
    return e;
  }

  function paintGrid(counts) {
    var table = el("table", "cal-table");
    var head = el("tr");
    LETTERS.forEach(function (letter, i) {
      var th = el("th", "", letter);
      th.scope = "col";
      th.setAttribute("abbr", WEEKDAYS[i]);
      head.appendChild(th);
    });
    var thead = el("thead");
    thead.appendChild(head);
    table.appendChild(thead);
    var y = +month.slice(0, 4);
    var m = +month.slice(5, 7);
    var body = el("tbody");
    monthWeeks(y, m).forEach(function (week) {
      var tr = el("tr");
      week.forEach(function (day) {
        var td = el("td");
        if (day) {
          var date = month + "-" + pad(day);
          var n = counts[date] || 0;
          var cls = date === today ? " today" : "";
          if (n) {
            var b = el("button", "cal-day has" + cls);
            b.type = "button";
            b.setAttribute("data-date", date);
            b.setAttribute("aria-label", dayLabel(date, n));
            b.appendChild(el("span", "cal-num", String(day)));
            b.appendChild(n > 1 ? el("span", "cal-count", String(n)) : el("span", "cal-dot"));
            td.appendChild(b);
          } else {
            td.appendChild(el("span", "cal-day" + cls, String(day)));
          }
        }
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    grid.textContent = "";
    grid.appendChild(table);
  }

  // Rebuild from the cards now in the list; `reset` also re-picks the month.
  function render(reset) {
    var counts = countByDate();
    var dates = Object.keys(counts);
    host.hidden = dates.length === 0;
    empty.hidden = dates.length !== 0;
    if (!dates.length) { selected = null; filterList(); return; }
    var range = monthRange(today, dates);
    if (reset || month === null) { month = openingMonth(today, dates); }
    if (month < range[0]) { month = range[0]; }
    if (month > range[1]) { month = range[1]; }
    selected = keepSelection(month, selected, dates);
    title.textContent = monthTitle(month);
    prev.disabled = month <= range[0];
    next.disabled = month >= range[1];
    none.hidden = firstDayIn(month, dates) !== null;
    paintGrid(counts);
    paintSelection(counts);
    filterList();
  }

  function go(delta) {
    month = shiftMonth(month, delta);
    selected = null;
    render(false);
  }
  prev.addEventListener("click", function () { go(-1); });
  next.addEventListener("click", function () { go(1); });
  grid.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button[data-date]");
    if (!b) { return; }
    selected = b.getAttribute("data-date");
    paintSelection(countByDate());
    filterList();
  });

  // Hide / unhide / undo add and remove cards: keep the calendar in step.
  if (window.MutationObserver) {
    new MutationObserver(function (mutations) {
      // an undone hide puts a card back: jump to its day so it does not stay filtered out
      mutations.forEach(function (m) {
        Array.prototype.forEach.call(m.addedNodes, function (n) {
          if (n.nodeType === 1 && n.matches(".card[data-start]")) {
            selected = n.getAttribute("data-start");
            month = monthOf(selected);
          }
        });
      });
      render(false);
    }).observe(list, { childList: true });
  }
  document.addEventListener("amc:view", function () { render(false); });
  render(true);
})();
