// SubGen website. Small progressive enhancements only; the page works without this file.
(function () {
  "use strict";

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- Header border once the page scrolls ---------- */
  var header = document.querySelector(".site-header");
  if (header) {
    var onScroll = function () { header.classList.toggle("is-scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  /* ---------- Hero: one cue, five languages, three formats ---------- */
  var CUES = {
    en: { dir: "ltr", lines: ["We should leave before", "the tide comes in."], file: "Low Tide (2024).en.srt", format: "srt" },
    ar: { dir: "rtl", lines: ["علينا أن نغادر", "قبل أن يرتفع المد."], file: "Low Tide (2024).ar.srt", format: "srt" },
    fr: { dir: "ltr", lines: ["On devrait partir avant", "que la marée monte."], file: "Low Tide (2024).fr.vtt", format: "vtt" },
    es: { dir: "ltr", lines: ["Deberíamos irnos antes", "de que suba la marea."], file: "Low Tide (2024).es.ass", format: "ass" },
    ja: { dir: "ltr", lines: ["潮が満ちる前に", "出発しよう。"], file: "Low Tide (2024).ja.srt", format: "srt" }
  };
  var ORDER = ["en", "ar", "fr", "es", "ja"];

  var player = document.getElementById("player");
  var cueEl = document.getElementById("cue");
  var srtFile = document.getElementById("srt-file");
  var srtBody = document.getElementById("srt-body");
  var srt = document.getElementById("srt");
  var toggle = document.getElementById("cycle-toggle");
  var buttons = player ? player.querySelectorAll("[data-lang]") : [];

  function el(tag, text) { var n = document.createElement(tag); n.textContent = text; return n; }

  function fileBody(cue) {
    var text = cue.lines.join("\n");
    var head;
    if (cue.format === "vtt") head = "WEBVTT\n\n00:42:17.480 --> 00:42:20.120\n";
    else if (cue.format === "ass") { head = "Dialogue: 0,0:42:17.48,0:42:20.12,Default,,0,0,0,,"; text = cue.lines.join("\\N"); }
    else head = "128\n00:42:17,480 --> 00:42:20,120\n";
    var frag = document.createDocumentFragment();
    frag.appendChild(document.createTextNode(head));
    var b = el("b", text);
    b.setAttribute("lang", cueLang);
    b.setAttribute("dir", cue.dir);
    frag.appendChild(b);
    return frag;
  }

  var cueLang = "en";
  var current = 0;
  var timer = null;
  var swapTimer = null;

  function render(lang) {
    var cue = CUES[lang];
    cueLang = lang;
    cueEl.textContent = "";
    cue.lines.forEach(function (line) { cueEl.appendChild(el("span", line)); });
    cueEl.setAttribute("lang", lang);
    cueEl.setAttribute("dir", cue.dir);
    srtFile.textContent = cue.file;
    srtBody.textContent = "";
    srtBody.appendChild(fileBody(cue));
    buttons.forEach(function (btn) { btn.setAttribute("aria-pressed", String(btn.dataset.lang === lang)); });
  }

  function show(lang, animate) {
    current = ORDER.indexOf(lang);
    if (!animate || reduceMotion) { render(lang); return; }
    cueEl.classList.add("is-swapping");
    clearTimeout(swapTimer);
    swapTimer = setTimeout(function () { render(lang); cueEl.classList.remove("is-swapping"); }, 280);
  }

  function setPlaying(playing) {
    clearInterval(timer);
    timer = null;
    if (player) player.classList.toggle("is-playing", playing);
    if (toggle) {
      toggle.setAttribute("aria-label", playing ? "Pause language cycling" : "Play language cycling");
      // SVG elements have no .hidden property, so set the attribute directly.
      toggle.querySelector(".i-pause").toggleAttribute("hidden", !playing);
      toggle.querySelector(".i-play").toggleAttribute("hidden", playing);
    }
    if (playing) {
      timer = setInterval(function () {
        if (document.hidden) return;
        show(ORDER[(current + 1) % ORDER.length], true);
      }, 3200);
    }
  }

  if (player && cueEl && srtBody) {
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        setPlaying(false);
        srt.setAttribute("aria-live", "polite");
        show(btn.dataset.lang, true);
      });
    });
    if (toggle) {
      toggle.hidden = false;
      toggle.addEventListener("click", function () { setPlaying(!timer); });
    }
    // Cycle automatically only when the visitor has not asked for reduced motion.
    setPlaying(!reduceMotion);
  }

  /* ---------- Copy buttons on code blocks ---------- */
  if (navigator.clipboard && window.isSecureContext) {
    document.querySelectorAll(".code").forEach(function (block) {
      var btn = block.querySelector(".copy");
      var code = block.querySelector("pre code");
      if (!btn || !code) return;
      btn.hidden = false;
      btn.setAttribute("aria-label", "Copy " + (block.querySelector(".code-head span") || {}).textContent + " snippet");
      btn.addEventListener("click", function () {
        navigator.clipboard.writeText(code.textContent).then(function () {
          btn.textContent = "Copied";
          btn.dataset.state = "done";
          setTimeout(function () { btn.textContent = "Copy"; delete btn.dataset.state; }, 1800);
        }, function () {
          btn.textContent = "Select and copy";
        });
      });
    });
  }

  /* ---------- Screenshots: show a calm placeholder if a file is missing ---------- */
  document.querySelectorAll(".shot").forEach(function (fig) {
    var img = fig.querySelector("img");
    var link = fig.querySelector("a.shot-frame");
    if (!img) return;
    var markMissing = function () {
      fig.classList.add("is-missing");
      img.setAttribute("aria-hidden", "true");
      if (link) { link.removeAttribute("href"); link.setAttribute("aria-hidden", "true"); }
    };
    if (img.complete && img.naturalWidth === 0 && img.currentSrc) markMissing();
    else img.addEventListener("error", markMissing, { once: true });
  });
})();
