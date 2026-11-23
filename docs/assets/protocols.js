/* protocols.js - menu toggle, copy buttons, section tracking in the menu, catalogue filter. */
(function () {
  "use strict";

  /* mobile: open / close the left menu (Read the Docs behaviour) */
  function toggleMenu(force) {
    document.querySelectorAll('[data-toggle="wy-nav-shift"]').forEach(function (el) {
      el.classList.toggle("shift", force);
    });
  }
  document.querySelectorAll('[data-toggle="wy-nav-top"]').forEach(function (el) {
    el.addEventListener("click", function () { toggleMenu(); });
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleMenu(); }
    });
  });
  document.querySelectorAll(".wy-menu-vertical a").forEach(function (a) {
    a.addEventListener("click", function () { toggleMenu(false); });
  });

  /* keep the current protocol visible in the menu */
  var side = document.querySelector(".wy-side-scroll");
  var current = document.querySelector(".wy-menu-vertical li.toctree-l1.current");
  if (side && current) {
    side.scrollTop = Math.max(0, current.offsetTop - 140);
  }

  /* copy buttons on code blocks */
  function copyText(text, done) {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { legacyCopy(text, done); });
    } else {
      legacyCopy(text, done);
    }
  }
  function legacyCopy(text, done) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (e) { /* nothing */ }
    document.body.removeChild(ta);
  }
  document.querySelectorAll(".rst-content pre").forEach(function (pre) {
    var host = pre.parentElement && pre.parentElement.classList.contains("sourceCode") ? pre.parentElement : pre;
    if (host.querySelector(":scope > .copybtn")) { return; }
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "copybtn";
    btn.title = "Copy";
    btn.setAttribute("aria-label", "Copy the code");
    btn.innerHTML = '<span class="fa fa-copy" aria-hidden="true"></span>';
    btn.addEventListener("click", function () {
      var code = pre.querySelector("code") || pre;
      copyText(code.innerText.replace(/\n$/, ""), function () {
        btn.classList.add("copied");
        btn.innerHTML = '<span class="fa fa-check" aria-hidden="true"></span>';
        setTimeout(function () {
          btn.classList.remove("copied");
          btn.innerHTML = '<span class="fa fa-copy" aria-hidden="true"></span>';
        }, 1500);
      });
    });
    host.appendChild(btn);
  });

  /* table of contents in the menu: highlight and expand the section being read */
  var toc = document.querySelector(".wy-menu-vertical ul.page-toc");
  if (toc && "IntersectionObserver" in window) {
    var links = {};
    toc.querySelectorAll("a[href^='#']").forEach(function (a) {
      links[decodeURIComponent(a.getAttribute("href").slice(1))] = a;
    });
    var headings = Array.prototype.filter.call(
      document.querySelectorAll(".rst-content h2[id], .rst-content h3[id], .rst-content h4[id]"),
      function (h) { return links[h.id]; });
    var visible = {};
    var setActive = function (id) {
      toc.querySelectorAll("li.active, li.current").forEach(function (li) {
        li.classList.remove("active", "current");
      });
      var a = links[id];
      if (!a) { return; }
      a.parentElement.classList.add("active");
      var li = a.parentElement;
      while (li && li !== toc) {
        if (li.tagName === "LI") { li.classList.add("current"); }
        li = li.parentElement;
      }
    };
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { visible[e.target.id] = e.isIntersecting; });
      var top = null;
      for (var i = 0; i < headings.length; i++) {
        if (visible[headings[i].id]) { top = headings[i].id; break; }
      }
      if (!top) {
        /* no heading on screen: the last one above the viewport */
        for (var j = headings.length - 1; j >= 0; j--) {
          if (headings[j].getBoundingClientRect().top < 0) { top = headings[j].id; break; }
        }
      }
      if (top) { setActive(top); }
    }, { rootMargin: "0px 0px -55% 0px" });
    headings.forEach(function (h) { observer.observe(h); });
    if (location.hash) { setActive(decodeURIComponent(location.hash.slice(1))); }
  }

  /* home page: filter the catalogue */
  var filter = document.getElementById("proto-filter");
  if (filter) {
    var rows = document.querySelectorAll("table.catalog tr[data-search]");
    var cats = document.querySelectorAll(".catalog-cat");
    var count = document.getElementById("proto-count");
    var none = document.getElementById("no-match");
    var norm = function (s) {
      return s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
    };
    var apply = function () {
      var terms = norm(filter.value).split(/\s+/).filter(Boolean);
      var shown = 0;
      rows.forEach(function (tr) {
        var hay = tr.getAttribute("data-search");
        var ok = terms.every(function (t) { return hay.indexOf(t) !== -1; });
        tr.hidden = !ok;
        if (ok) { shown++; }
      });
      cats.forEach(function (c) {
        c.hidden = !c.querySelector("tr[data-search]:not([hidden])");
      });
      if (count) { count.textContent = shown + " / " + rows.length + " protocols"; }
      if (none) { none.hidden = shown !== 0; }
    };
    filter.addEventListener("input", apply);
    filter.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        var first = document.querySelector("table.catalog tr[data-search]:not([hidden]) a");
        if (first) { location.href = first.href; }
      }
    });
    apply();
  }
})();
