/* search.js - full-text search of the protocols (docs/search.html), index built by tools/build.py.
   Index format: [{u: url, t: title, n: number, s: [[anchor, heading, text], ...]}, ...] */
(function () {
  "use strict";
  var form = document.getElementById("search-form");
  var input = document.getElementById("search-input");
  var out = document.getElementById("search-results");
  var summary = document.getElementById("search-summary");
  if (!form || !input || !out) { return; }

  function norm(s) {
    return s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  }
  function esc(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function highlight(text, terms) {
    /* escape, then wrap the terms in <mark> (accent-insensitive positions) */
    var n = norm(text);
    var marks = [];
    terms.forEach(function (t) {
      var i = n.indexOf(t);
      while (i !== -1) { marks.push([i, i + t.length]); i = n.indexOf(t, i + t.length); }
    });
    marks.sort(function (a, b) { return a[0] - b[0]; });
    var res = "", pos = 0;
    marks.forEach(function (m) {
      if (m[0] < pos) { return; }
      res += esc(text.slice(pos, m[0])) + "<mark>" + esc(text.slice(m[0], m[1])) + "</mark>";
      pos = m[1];
    });
    return res + esc(text.slice(pos));
  }
  function snippet(text, terms) {
    var n = norm(text), first = -1;
    terms.forEach(function (t) {
      var i = n.indexOf(t);
      if (i !== -1 && (first === -1 || i < first)) { first = i; }
    });
    if (first === -1) { first = 0; }
    var start = Math.max(0, first - 90), end = Math.min(text.length, first + 170);
    var s = text.slice(start, end);
    return (start > 0 ? "… " : "") + highlight(s, terms) + (end < text.length ? " …" : "");
  }
  function count(hay, t) {
    var c = 0, i = hay.indexOf(t);
    while (i !== -1) { c++; i = hay.indexOf(t, i + t.length); }
    return c;
  }

  function run(index, query) {
    var terms = norm(query).split(/\s+/).filter(function (t) { return t.length > 0; });
    if (!terms.length) { out.innerHTML = ""; summary.textContent = ""; return; }
    var results = [];
    index.forEach(function (doc) {
      var title = norm(doc.n + " " + doc.t);
      var hits = [];
      doc.s.forEach(function (sec) {
        var head = norm(sec[1]), body = norm(sec[2]);
        var all = title + " " + head + " " + body;
        if (!terms.every(function (t) { return all.indexOf(t) !== -1; })) { return; }
        var inSec = terms.filter(function (t) { return head.indexOf(t) !== -1 || body.indexOf(t) !== -1; });
        if (!inSec.length) { return; }
        var score = 0;
        terms.forEach(function (t) {
          score += 8 * count(head, t) + Math.min(count(body, t), 10);
        });
        hits.push({ sec: sec, score: score });
      });
      var titleScore = terms.reduce(function (a, t) { return a + (title.indexOf(t) !== -1 ? 30 : 0); }, 0);
      var titleAll = terms.every(function (t) { return title.indexOf(t) !== -1; });
      if (!hits.length && !titleAll) { return; }
      hits.sort(function (a, b) { return b.score - a.score; });
      var best = hits.length ? hits[0].score : 0;
      results.push({ doc: doc, hits: hits, score: titleScore + best + Math.min(hits.length, 10) });
    });
    results.sort(function (a, b) { return b.score - a.score; });
    var nsec = results.reduce(function (a, r) { return a + r.hits.length; }, 0);
    summary.textContent = results.length
      ? results.length + " protocol" + (results.length > 1 ? "s" : "") + ", " + nsec + " section" +
        (nsec > 1 ? "s" : "") + " matching “" + query + "”."
      : "No protocol matches “" + query + "”.";
    var html = [];
    results.slice(0, 80).forEach(function (r) {
      html.push('<li><a class="result-title" href="' + esc(r.doc.u) + '">' + highlight(r.doc.t, terms) +
        '</a><span class="result-num">protocol ' + esc(r.doc.n) + "</span>");
      if (r.hits.length) {
        html.push("<ul>");
        r.hits.slice(0, 4).forEach(function (h) {
          var href = r.doc.u + (h.sec[0] ? "#" + h.sec[0] : "") ;
          html.push('<li><a href="' + esc(href) + '">' + highlight(h.sec[1] || r.doc.t, terms) + "</a>" +
            '<span class="snippet">' + snippet(h.sec[2], terms) + "</span></li>");
        });
        if (r.hits.length > 4) {
          html.push('<li class="more">… ' + (r.hits.length - 4) + " more section" + (r.hits.length > 5 ? "s" : "") + "</li>");
        }
        html.push("</ul>");
      }
      html.push("</li>");
    });
    out.innerHTML = html.join("");
  }

  var params = new URLSearchParams(location.search);
  var q = params.get("q") || "";
  input.value = q;
  var sideBox = document.querySelector('.wy-side-nav-search input[name="q"]');
  if (sideBox) { sideBox.value = q; }
  var indexPromise = null;
  function load() {
    if (!indexPromise) {
      summary.textContent = "Loading the search index…";
      indexPromise = fetch("search_index.json").then(function (r) { return r.json(); });
    }
    return indexPromise;
  }
  function search(query) {
    load().then(function (index) { run(index, query); }, function () {
      summary.textContent = "The search index could not be loaded (open the site through a web server).";
    });
  }
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var query = input.value.trim();
    history.replaceState(null, "", "?q=" + encodeURIComponent(query));
    search(query);
  });
  if (q.trim()) { search(q.trim()); } else { input.focus(); }
})();
