/* The product website's one script (TASK-1463, ADR 0049). First-party, no dependency, no
   build step. Served at /site/landing.js under `script-src 'self'; connect-src 'none'`.

   It is an enhancement only: every word of the page is in index.html, and with this file
   blocked the page says the same thing. It does three things and no others:
     1. marks a picture that has not been drawn yet, so its caption says so;
     2. the hero animation, "One plan, many agents", built over the still image;
     3. "Selecting scopes the work" (section 6): the parts become clickable, and the demo steps
        through them on its own.
   Motion (2, and the automatic stepping in 3) never starts when the visitor asks for reduced
   motion, stops when they switch it on, and has a pause button (WCAG 2.2.2).

   Rules this file keeps, checked by backend/verify_landing_v2.py: no eval, no new Function,
   no string passed to a timer, no innerHTML, no request of any kind (fetch, XHR, beacon,
   WebSocket, import()), nothing read from the URL, cookies or storage. Text is set with
   textContent only. */
(function () {
  "use strict";

  var reduce = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  function motionAllowed() { return !(reduce && reduce.matches); }
  function onMotionChange(fn) {
    if (!reduce) return;
    if (reduce.addEventListener) reduce.addEventListener("change", fn);
    else if (reduce.addListener) reduce.addListener(fn);
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  /* A small looping timeline: `steps` is a list of [delay in ms, function]. */
  function Timeline(steps, onReset) {
    this.steps = steps; this.onReset = onReset; this.i = 0; this.timer = null; this.running = false;
  }
  Timeline.prototype.tick = function () {
    var self = this;
    if (!self.running) return;
    if (self.i >= self.steps.length) { self.i = 0; self.onReset(); }
    var step = self.steps[self.i];
    self.timer = window.setTimeout(function () {
      self.i += 1;
      step[1]();
      self.tick();
    }, step[0]);
  };
  Timeline.prototype.start = function () { if (!this.running) { this.running = true; this.tick(); } };
  Timeline.prototype.stop = function () { this.running = false; window.clearTimeout(this.timer); };

  function pauseButton(figure, timeline) {
    var b = el("button", "motion-toggle", "Pause animation");
    b.type = "button";
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", function () {
      if (timeline.running) {
        timeline.stop(); b.textContent = "Play animation"; b.setAttribute("aria-pressed", "true");
      } else {
        timeline.start(); b.textContent = "Pause animation"; b.setAttribute("aria-pressed", "false");
      }
    });
    figure.appendChild(b);
    return b;
  }

  /* Run only while visible and while the tab is shown. */
  function whileVisible(target, timeline, isPaused) {
    var visible = true;
    function sync() {
      if (visible && !document.hidden && motionAllowed() && !isPaused()) timeline.start();
      else timeline.stop();
    }
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        visible = entries[0].isIntersecting; sync();
      }).observe(target);
    }
    document.addEventListener("visibilitychange", sync);
    onMotionChange(sync);
    return sync;
  }

  /* ---- 1. pictures not drawn yet */
  function markMissing(img) {
    var fig = img.closest("figure");
    if (fig) fig.classList.add("is-missing");
  }
  function watchImages() {
    var imgs = document.querySelectorAll(".visual img");
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      if (img.complete && img.naturalWidth === 0 && img.currentSrc) markMissing(img);
      img.addEventListener("error", function (ev) { markMissing(ev.target); });
    }
  }

  /* ---- 2. hero: one plan, many agents. The same story as hero-one-plan.svg: agents file
     cards into one board (To do, In progress, Blocked, Done); an agent's proposed acceptance
     criteria wait on one card for a person, travel to the person it is assigned to, and are
     approved. Wide layouts only: below 701 px the page shows the narrow still, which carries the
     whole meaning at a size a phone can read. */
  function heroAnimation() {
    var fig = document.getElementById("hero-figure");
    if (!fig || !motionAllowed()) return;
    if (window.matchMedia && !window.matchMedia("(min-width: 701px)").matches) return;

    var scene = el("div", "hero-scene");
    scene.setAttribute("aria-hidden", "true");

    var agents = el("div", "hs-agents");
    agents.appendChild(el("span", "hs-chip", "Agent 1"));
    agents.appendChild(el("span", "hs-chip", "Agent 2"));
    agents.appendChild(el("span", "hs-chip", "Agent 3"));

    var board = el("div", "hs-board");
    board.appendChild(el("span", "hs-epic", "Epic lane: one plan"));
    var cols = el("div", "hs-cols");
    var names = ["To do", "In progress", "Blocked", "Done"];
    var col = names.map(function (n) {
      var c = el("div", "hs-col");
      c.appendChild(el("span", "hs-col-title", n));
      cols.appendChild(c);
      return c;
    });
    var c1 = el("span", "hs-card c1", "Task");
    var c2 = el("span", "hs-card c2", "Task");
    var c3 = el("span", "hs-card c3", "Task, blocked");
    var c4 = el("span", "hs-card c4", "Task, done");
    col[0].appendChild(c1); col[1].appendChild(c2); col[2].appendChild(c3); col[3].appendChild(c4);
    board.appendChild(cols);

    var person = el("div", "hs-person");
    person.appendChild(el("span", "hs-chip", "You, the assignee"));

    var tag = el("span", "hs-tag", "Proposed criteria");

    scene.appendChild(agents); scene.appendChild(board); scene.appendChild(person); scene.appendChild(tag);

    var still = fig.querySelector("picture") || fig.querySelector("img");
    fig.insertBefore(scene, still.nextSibling);
    fig.classList.add("is-animated");

    function add(n) { return function () { scene.classList.add("s" + n); }; }
    function reset() {
      for (var n = 1; n <= 7; n++) scene.classList.remove("s" + n);
      c2.textContent = "Task";
    }
    var tl = new Timeline([
      [600, add(1)], [900, add(2)], [900, add(3)],
      [1200, function () { scene.classList.add("s4"); c2.textContent = "Task: criteria proposed"; }],
      [1000, add(5)], [1300, add(6)],
      [700, function () { scene.classList.add("s7"); c2.textContent = "Task: criteria approved"; }],
      [3000, function () {}]
    ], reset);

    var paused = false;
    var btn = pauseButton(fig, tl);
    btn.addEventListener("click", function () { paused = !tl.running; });
    whileVisible(fig, tl, function () { return paused; })();
    onMotionChange(function () {
      if (!motionAllowed()) { tl.stop(); reset(); scene.classList.add("s1", "s2", "s3"); }
    });
  }

  /* ---- 3. selecting scopes the work */
  function scopeDemo() {
    var fig = document.getElementById("scope-demo");
    if (!fig) return;
    var buttons = fig.querySelectorAll("button.scope-node");
    var cards = fig.querySelectorAll(".scope-card");
    var current = fig.querySelector(".scope-current");
    var order = [];
    for (var i = 0; i < buttons.length; i++) order.push(buttons[i].getAttribute("data-part"));

    function select(part) {
      fig.setAttribute("data-selected", part);
      for (var b = 0; b < buttons.length; b++) {
        var on = buttons[b].getAttribute("data-part") === part;
        buttons[b].setAttribute("aria-pressed", on ? "true" : "false");
        if (on) current.textContent = buttons[b].textContent;
        buttons[b].classList.remove("is-ringed");
      }
      for (var c = 0; c < cards.length; c++) {
        cards[c].classList.toggle("is-out", cards[c].getAttribute("data-part") !== part);
        cards[c].classList.remove("is-hover");
      }
    }
    function hover(card, on) {
      var part = card.getAttribute("data-part");
      card.classList.toggle("is-hover", on);
      for (var b = 0; b < buttons.length; b++) {
        buttons[b].classList.toggle("is-ringed", on && buttons[b].getAttribute("data-part") === part);
      }
    }

    fig.classList.add("is-live");
    var paused = false;
    var steps = [];
    order.forEach(function (part) {
      steps.push([1800, function () { select(part); }]);
      steps.push([1100, function () {
        var first = fig.querySelector(".scope-card:not(.is-out)");
        if (first) hover(first, true);
      }]);
      steps.push([1100, function () {
        var lit = fig.querySelector(".scope-card.is-hover");
        if (lit) hover(lit, false);
      }]);
    });
    var tl = new Timeline(steps, function () {});

    for (var b = 0; b < buttons.length; b++) {
      buttons[b].addEventListener("click", function (ev) {
        /* A visitor who picks a part takes over: the automatic stepping stops for good. */
        paused = true; tl.stop();
        if (btn) { btn.textContent = "Play animation"; btn.setAttribute("aria-pressed", "true"); }
        select(ev.currentTarget.getAttribute("data-part"));
      });
    }
    for (var c = 0; c < cards.length; c++) {
      cards[c].addEventListener("mouseenter", function (ev) { hover(ev.currentTarget, true); });
      cards[c].addEventListener("mouseleave", function (ev) { hover(ev.currentTarget, false); });
    }

    var btn = null;
    if (motionAllowed()) {
      btn = pauseButton(fig, tl);
      btn.addEventListener("click", function () { paused = !tl.running; });
      whileVisible(fig, tl, function () { return paused; })();
    }
  }

  function init() {
    document.documentElement.classList.add("js");
    watchImages();
    heroAnimation();
    scopeDemo();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
