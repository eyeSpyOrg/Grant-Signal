// Large-text toggle (persists across pages)
(function () {
  var btn = document.getElementById("font-toggle");
  var on = localStorage.getItem("bigText") === "1";
  function apply() {
    document.documentElement.classList.toggle("big-text", on);
    if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  apply();
  if (btn) btn.addEventListener("click", function () {
    on = !on;
    localStorage.setItem("bigText", on ? "1" : "0");
    apply();
  });
})();

// Indexer status polling (only on the indexer page)
(function () {
  var box = document.getElementById("indexer-status");
  if (!box) return;
  function render(s) {
    var html = "";
    if (s.current) {
      html += '<p class="badge badge-vision">Working: ' + esc(s.current.name || s.current.ein) +
              " — " + esc(s.current.step || "") + "</p>";
    } else if (!s.running && s.queue.length === 0) {
      html += '<p class="muted">Idle. Queue a funder below to begin.</p>';
    }
    if (s.queue.length) {
      html += "<p><strong>Queued (" + s.queue.length + "):</strong> " +
              s.queue.map(function (i) { return esc(i.name || i.ein); }).join(", ") + "</p>";
    }
    if (s.done.length) {
      html += "<p><strong>Completed:</strong></p><ul>";
      s.done.forEach(function (d) {
        html += "<li>" + esc(d.name || d.ein) + " — " + d.filings + " filings, " +
                d.grants + " grants added</li>";
      });
      html += "</ul>";
    }
    if (s.errors.length) {
      html += '<p class="error">';
      s.errors.forEach(function (e) { html += esc(e.name || e.ein) + ": " + esc(e.error) + "<br>"; });
      html += "</p>";
    }
    box.innerHTML = html;
  }
  function esc(t) {
    var d = document.createElement("div");
    d.textContent = t == null ? "" : String(t);
    return d.innerHTML;
  }
  function poll() {
    fetch("/indexer/status")
      .then(function (r) { return r.json(); })
      .then(function (s) {
        render(s);
        setTimeout(poll, s.running || s.queue.length ? 2000 : 8000);
      })
      .catch(function () { setTimeout(poll, 10000); });
  }
  poll();
})();
