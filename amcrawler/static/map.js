(function () {
  "use strict";

  var dataEl = document.getElementById("map-data");
  var mapEl = document.getElementById("map");
  var missingEl = document.getElementById("map-missing");
  var layout = document.querySelector(".layout");
  if (!dataEl || !mapEl || !layout) { return; }

  var data = JSON.parse(dataEl.textContent);
  var world = null;
  var loading = null;
  var drawnWidth = -1;
  var drawnScheme = null;

  function plural(n) { return n + (n === 1 ? " partenza" : " partenze"); }

  // Per numeric country id: { count, isNew, codes }.
  function tally() {
    var byId = {};
    data.departures.forEach(function (dep) {
      dep.countries.forEach(function (code) {
        var id = data.numeric[code];
        var key = id || ("?" + code);
        var t = byId[key] || (byId[key] = { count: 0, isNew: false, code: code });
        t.count += 1;
        if (dep.new) { t.isNew = true; }
      });
    });
    return { byId: byId };
  }

  function isDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function isPhone() {
    return window.matchMedia && window.matchMedia("(max-width: 899px)").matches;
  }

  function targetUrl(code) {
    var url = new URL(window.location.href);
    var has = url.searchParams.getAll("country").indexOf(code) !== -1;
    if (!has) { url.searchParams.append("country", code); }
    url.hash = isPhone() ? "lista" : "";
    return url.toString();
  }

  // The map's height follows the projection, so only the width says whether it is shown.
  function visible() { return mapEl.offsetWidth > 0; }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function zoomButton(label, text, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "map-zoom-btn";
    b.setAttribute("aria-label", label);
    b.textContent = text;
    b.addEventListener("click", onClick);
    return b;
  }

  function draw() {
    if (!world || !visible() || !window.d3 || !window.topojson) { return; }
    var d3 = window.d3;
    var w = mapEl.clientWidth;
    drawnWidth = w;
    drawnScheme = isDark();

    var features = window.topojson.feature(world, world.objects.countries).features;
    var tallied = tally();
    var byId = tallied.byId;

    var maxCount = 1;
    Object.keys(byId).forEach(function (k) { maxCount = Math.max(maxCount, byId[k].count); });
    var scale = d3.scaleSequential(
      d3.interpolateRgb(cssVar("--map-low"), cssVar("--map-high"))
    ).domain([1, Math.max(maxCount, 2)]);

    // Size the box to the projected world: no empty space around it.
    var sphere = { type: "Sphere" };
    var projection = d3.geoNaturalEarth1().fitWidth(w, sphere);
    var path = d3.geoPath(projection);
    var h = Math.ceil(path.bounds(sphere)[1][1]);

    d3.select(mapEl).selectAll("*").remove();
    var svg = d3.select(mapEl).append("svg")
      .attr("viewBox", "0 0 " + w + " " + h)
      .attr("role", "group")
      .attr("aria-label", "Mappa delle partenze");

    var layer = svg.append("g");
    var zoom = d3.zoom()
      .scaleExtent([1, 8])
      .extent([[0, 0], [w, h]])
      .translateExtent([[0, 0], [w, h]])
      .on("zoom", function (event) { layer.attr("transform", event.transform); });
    svg.call(zoom);

    var ctl = document.createElement("div");
    ctl.className = "map-zoom";
    ctl.appendChild(zoomButton("Ingrandisci", "+", function () {
      svg.transition().duration(200).call(zoom.scaleBy, 1.6);
    }));
    ctl.appendChild(zoomButton("Riduci", "−", function () {
      svg.transition().duration(200).call(zoom.scaleBy, 1 / 1.6);
    }));
    mapEl.appendChild(ctl);

    var shown = {};
    layer.selectAll("path").data(features).enter().append("path")
      .attr("d", path)
      .attr("class", function (f) {
        return "map-country" + (byId[String(f.id)] ? " has" : "");
      })
      .each(function (f) {
        var t = byId[String(f.id)];
        var node = d3.select(this);
        if (t) {
          shown[String(f.id)] = true;
          node.style("fill", t.isNew ? "var(--accent)" : scale(t.count));
          var name = data.names[t.code] || t.code;
          var label = name + " · " + plural(t.count);
          node.append("title").text(label);
          node.attr("tabindex", "0").attr("role", "link").attr("aria-label", label)
            .on("click", function () { window.location.href = targetUrl(t.code); })
            .on("keydown", function (event) {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                window.location.href = targetUrl(t.code);
              }
            });
        }
      });

    var missing = Object.keys(byId).filter(function (k) { return !shown[k]; })
      .map(function (k) { return byId[k]; })
      .sort(function (a, b) {
        return (data.names[a.code] || a.code).localeCompare(data.names[b.code] || a.code, "it");
      })
      .map(function (t) { return (data.names[t.code] || t.code) + " (" + t.count + ")"; });
    missingEl.textContent = missing.length ? "Non sulla mappa: " + missing.join(", ") : "";
  }

  function load() {
    if (world) { return Promise.resolve(); }
    if (!loading) {
      loading = fetch("/static/vendor/countries-50m.json", { credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok) { throw new Error("map shapes " + r.status); }
          return r.json();
        })
        .then(function (json) { world = json; })
        .catch(function () {
          loading = null;
          missingEl.textContent = "Mappa non disponibile.";
        });
    }
    return loading;
  }

  function ensureDrawn() {
    if (!visible()) { return; }
    load().then(draw);
  }

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (visible() && mapEl.clientWidth !== drawnWidth) { ensureDrawn(); }
    }, 150);
  });
  document.addEventListener("amc:view", ensureDrawn);
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: dark)");
    var onScheme = function () { if (drawnScheme !== null && drawnScheme !== isDark()) { ensureDrawn(); } };
    if (mq.addEventListener) { mq.addEventListener("change", onScheme); }
  }

  // Desktop shows list and map together, so draw straight away; on a phone the
  // map is hidden until the Mappa toggle fires "amc:view".
  ensureDrawn();
})();
