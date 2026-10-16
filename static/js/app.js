/* BEAT — one small script for every page. Each block checks for its elements
   so the file is safe to load everywhere. The pre-paint theme snippet stays
   inline in the layouts because it must run before first render. */
(function () {
  "use strict";
  var html = document.documentElement;

  /* theme toggle */
  var toggles = document.querySelectorAll("[data-theme-toggle]");
  for (var i = 0; i < toggles.length; i++) {
    toggles[i].addEventListener("click", function () {
      html.dataset.theme = html.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem("theme", html.dataset.theme); } catch (e) {}
    });
  }

  /* dismissable flash messages */
  var xs = document.querySelectorAll(".flash .x");
  for (var j = 0; j < xs.length; j++) {
    xs[j].addEventListener("click", function () { this.parentNode.remove(); });
  }

  /* home: analyze form → fold the page away, show status + elapsed time */
  var form = document.getElementById("analyzeForm");
  if (form) {
    var home = document.getElementById("home"), btn = document.getElementById("goBtn");
    var panel = document.getElementById("working"), msg = document.getElementById("wkMsg"), time = document.getElementById("wkTime");
    var started = false;
    form.addEventListener("submit", function () {
      if (started) return;           /* native validation already passed */
      started = true;
      home.classList.add("loading");
      panel.hidden = false;
      btn.disabled = true;           /* after the submit event, so the form still posts */
      btn.textContent = "Checking…";
      window.scrollTo({ top: 0, behavior: "smooth" });
      var t0 = Date.now();
      var iv = setInterval(function () {
        var s = Math.floor((Date.now() - t0) / 1000);
        time.textContent = Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
        if (s === 45) msg.textContent = "Still working — listings with many photos take a bit longer.";
        if (s > 300) clearInterval(iv);
      }, 1000);
    });
  }

  /* analyses: live filter + delete confirmation */
  var filter = document.getElementById("filter");
  if (filter) {
    var rows = document.querySelectorAll("#rows .hrow"), count = document.getElementById("count");
    filter.addEventListener("input", function () {
      var q = filter.value.trim().toLowerCase(), n = 0;
      for (var k = 0; k < rows.length; k++) {
        var hit = !q || rows[k].dataset.s.indexOf(q) !== -1;
        rows[k].hidden = !hit; if (hit) n++;
      }
      if (count) count.textContent = n + (n === 1 ? " analysis" : " analyses");
    });
  }
  var dels = document.querySelectorAll("form[data-confirm]");
  for (var d = 0; d < dels.length; d++) {
    dels[d].addEventListener("submit", function (e) {
      if (!confirm(this.dataset.confirm)) e.preventDefault();
    });
  }

  /* secret fields: cleared right after their form submits (the browser has
     already serialized the body) and whenever the page is restored from the
     back/forward cache, so a key never lingers in a form on a shared machine */
  var secrets = document.querySelectorAll("input[data-secret]");
  function wipeSecrets() { for (var w = 0; w < secrets.length; w++) secrets[w].value = ""; }
  for (var s = 0; s < secrets.length; s++) {
    secrets[s].form && secrets[s].form.addEventListener("submit", function () { setTimeout(wipeSecrets, 0); });
  }
  window.addEventListener("pageshow", function (e) { if (e.persisted) wipeSecrets(); });
  window.addEventListener("pagehide", wipeSecrets);

  /* [data-toggle="id"] buttons show/hide a panel */
  var tg = document.querySelectorAll("[data-toggle]");
  for (var t = 0; t < tg.length; t++) {
    tg[t].addEventListener("click", function () {
      var el = document.getElementById(this.dataset.toggle);
      if (!el) return;
      el.hidden = !el.hidden;
      this.setAttribute("aria-expanded", el.hidden ? "false" : "true");
      if (!el.hidden) { var f = el.querySelector("input"); if (f) f.focus(); }
    });
  }

  /* any form with data-busy: disable its submit button and relabel it while the request runs */
  var busy = document.querySelectorAll("form[data-busy]");
  for (var b = 0; b < busy.length; b++) {
    busy[b].addEventListener("submit", function () {
      var btn = this.querySelector("button[type=submit]");
      if (btn) { setTimeout(function () { btn.disabled = true; }, 0); btn.textContent = this.dataset.busy; }
    });
  }

  /* report: tabs with hash deep-links (/report/5#price) */
  var bar = document.querySelector("#result .tabs");
  if (bar) {
    var btns = [].slice.call(bar.querySelectorAll("button[data-t]")), panels = {};
    var tps = document.querySelectorAll("#result .tp[data-t]");
    for (var p = 0; p < tps.length; p++) panels[tps[p].dataset.t] = tps[p];
    var first = btns.length ? btns[0].dataset.t : "";
    function show(key, push) {
      if (!panels[key]) key = first;
      btns.forEach(function (b) {
        var on = b.dataset.t === key;
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
      for (var k in panels) panels[k].hidden = k !== key;
      if (push && history.replaceState) history.replaceState(null, "", "#" + key);
    }
    btns.forEach(function (b) { b.addEventListener("click", function () { show(b.dataset.t, true); }); });
    show((location.hash || "").replace("#", ""), false);
  }
})();
