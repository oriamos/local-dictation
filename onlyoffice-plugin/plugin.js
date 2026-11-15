/* Lecture Transcription plugin for ONLYOFFICE.
 * Talks only to Local Dictation on 127.0.0.1 (port + token come from settings.js, written by the app).
 * New paragraphs are appended at the END of the document, one at a time and in order, so the user can keep
 * editing anywhere else without the cursor being moved.
 */
(function (window) {
  "use strict";
  var CFG = window.LD_CONFIG || { port: 47821, token: "" };
  var BASE = "http://127.0.0.1:" + CFG.port;
  var since = null;          // last event id inserted into THIS document
  var session = "";
  var queue = [], inserting = false, pollTimer = null;
  var startedHere = false;   // this document's Start button began the current session → insert from its first event
  var $ = function (id) { return document.getElementById(id); };

  function api(path, method, body) {
    var url = BASE + path + (path.indexOf("?") < 0 ? "?" : "&") + "t=" + encodeURIComponent(CFG.token);
    return fetch(url, {
      method: method || "GET",
      body: body ? JSON.stringify(body) : undefined,
      headers: body ? { "Content-Type": "text/plain" } : undefined   // simple request: no CORS preflight
    }).then(function (r) { return r.json(); });
  }

  function key() { return "ld_since_" + session; }
  function remember() { try { localStorage.setItem(key(), String(since)); } catch (e) {} }

  // ------------------------------------------------------------------ document insertion
  function insertNext() {
    if (inserting || !queue.length) return;
    inserting = true;
    var ev = queue.shift();
    Asc.scope.ev = ev;
    window.Asc.plugin.callCommand(function () {
      var ev = Asc.scope.ev;
      var doc = Api.GetDocument();
      var p = Api.CreateParagraph();
      try { p.SetJc("right"); } catch (e) {}
      try { p.GetParaPr().SetBidi(true); } catch (e) {}
      function run(text, opts) {
        var r = Api.CreateRun();
        r.AddText(text);
        if (opts.color) r.SetColor(opts.color[0], opts.color[1], opts.color[2]);
        if (opts.size) r.SetFontSize(opts.size);
        if (opts.bold) r.SetBold(true);
        if (opts.highlight) { try { r.SetHighlight(opts.highlight); } catch (e) {} }
        p.AddElement(r);
      }
      if (ev.type === "title") {
        run(ev.text, { bold: true, size: 32 });
      } else if (ev.type === "mark") {
        run("⭐ " + ev.text + " [" + ev.ts + "]", { bold: true, highlight: "yellow" });
      } else if (ev.type === "info") {
        run("[" + ev.ts + "] " + ev.text, { color: [150, 150, 150], size: 18 });
      } else {
        run("[" + ev.ts + "] ", { color: [150, 150, 150], size: 18 });
        run(ev.text, {});
      }
      doc.Push(p);
    }, false, true, function () {
      since = Math.max(since || 0, ev.id);
      remember();
      inserting = false;
      insertNext();
    });
  }

  // ------------------------------------------------------------------ polling
  function setStatus(st) {
    var box = $("status");
    var rec = st && st.state === "recording";
    box.className = st ? (rec ? "rec" : "") : "off";
    $("statusText").textContent = !st ? "Local Dictation לא פועל — הפעל את האפליקציה"
      : rec ? "מקליט ומתמלל · " + st.elapsed + (st.title ? " · " + st.title : "")
      : "מוכן";
    $("start").disabled = !st || rec;
    $("stop").disabled = !rec;
    $("mark").disabled = !rec;
    if (st && !$("terms").value && st.terms && st.terms.length && document.activeElement !== $("terms")) {
      $("terms").value = st.terms.join(", ");
    }
  }

  function poll() {
    api("/events?since=" + (since == null ? 0 : since)).then(function (d) {
      if (d.session && d.session !== session) {          // new lecture (or first contact)
        session = d.session;
        var stored = null;
        try { stored = localStorage.getItem(key()); } catch (e) {}
        if (stored !== null) since = parseInt(stored, 10);
        else if (startedHere) { since = 0; startedHere = false; }
        else {                                            // opened mid-lecture: don't dump history uninvited
          since = d.next;
          $("catchup").style.display = d.next > 0 ? "block" : "none";
        }
        remember();
      }
      if (since == null) since = 0;
      (d.events || []).forEach(function (ev) {
        if (ev.id > since && !queue.some(function (q) { return q.id === ev.id; })) queue.push(ev);
      });
      insertNext();
      setStatus(d);
    }).catch(function () { setStatus(null); })
      .then(function () { pollTimer = setTimeout(poll, 800); });
  }

  // ------------------------------------------------------------------ buttons
  function terms() {
    return $("terms").value.split(/[,،\n]/).map(function (s) { return s.trim(); }).filter(Boolean);
  }

  window.Asc.plugin.init = function () {
    $("start").onclick = function () {
      startedHere = true; queue = [];
      api("/start", "POST", { title: $("title").value, terms: terms() }).then(setStatus).catch(function () { setStatus(null); });
    };
    $("stop").onclick = function () { api("/stop", "POST", {}).then(setStatus); };
    $("mark").onclick = function () { api("/mark", "POST", {}); };
    $("terms").onchange = function () { api("/terms", "POST", { terms: terms() }); };
    $("catchup").onclick = function () {
      since = 0; remember(); $("catchup").style.display = "none";
    };
    poll();
  };

  window.Asc.plugin.button = function () { this.executeCommand("close", ""); };
})(window);
