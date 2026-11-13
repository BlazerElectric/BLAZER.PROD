/*!
 * Blazer Electric AI Order Desk — Billtrust storefront bridge.
 *
 * Install on buyblazer.com (Billtrust HTML editor) next to the order-app iframe:
 *
 *   <script>
 *     window.BLAZER_BRIDGE_CONFIG = { quickPad: { pageUrl: "/quickpad" } }; // optional overrides
 *   </script>
 *   <script src="https://YOUR-APP.vercel.app/billtrust-bridge.js"
 *           data-app-origin="https://YOUR-APP.vercel.app" defer></script>
 *
 * Also include this script on the QuickPad and cart pages (e.g. a site-wide footer) so queued
 * QuickPad lines, line comments and shipping instructions are applied after navigation.
 *
 * Security model:
 *  - Only messages from allowlisted app origins AND from the order-app iframe window are accepted.
 *  - Payloads are re-validated (SKU pattern, quantity range, length limits) — never trusted.
 *  - Values are only assigned via the native `.value` setter; no HTML is ever injected.
 *  - Nothing is submitted automatically unless `quickPad.autoSubmit` is explicitly enabled.
 *
 * The Billtrust selectors below are best guesses and MUST be verified against the live site.
 */
(function () {
  "use strict";
  if (window.__blazerBridgeLoaded) return;
  window.__blazerBridgeLoaded = true;

  var APP_SOURCE = "blazer-order-app";
  var BRIDGE_SOURCE = "billtrust-bridge";
  var VERSION = 1;
  var SKU_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/;
  var LIMITS = { maxItems: 200, maxQuantity: 99999, maxCommentLength: 250, maxShippingLength: 1000 };

  var currentScript = document.currentScript;
  var scriptOrigin = (function () {
    try {
      return currentScript && currentScript.src ? new URL(currentScript.src).origin : null;
    } catch {
      return null;
    }
  })();

  var DEFAULTS = {
    appOrigins: [],
    iframeSelector: "iframe[data-blazer-order-app], iframe#blazer-order-app",
    minIframeHeight: 640,
    debug: false,
    quickPad: {
      inputSelectors: [
        "textarea[id*='quickpad' i]",
        "textarea[name*='quickpad' i]",
        "textarea[class*='quickpad' i]",
        "[id*='quickpad' i] textarea",
        "[class*='quick-pad' i] textarea",
        "[class*='quickpad' i] textarea",
      ],
      submitSelectors: [
        "[id*='quickpad' i] button[type='submit']",
        "[class*='quickpad' i] button[type='submit']",
        "button[id*='quickpad' i]",
        "input[type='submit'][id*='quickpad' i]",
      ],
      lineTemplate: "{sku},{qty}",
      autoSubmit: false,
      pageUrl: "",
    },
    cart: {
      rowSelectors: ["tr[class*='cart' i]", "[class*='cart-item' i]", "[class*='cartItem' i]", "[class*='line-item' i]"],
      rowSkuSelectors: ["[class*='sku' i]", "[class*='item-number' i]", "[class*='itemNumber' i]", "[class*='part' i]"],
      rowCommentSelectors: [
        "textarea[name*='comment' i]",
        "input[name*='comment' i]",
        "textarea[id*='comment' i]",
        "input[id*='comment' i]",
        "textarea[class*='comment' i]",
        "input[class*='note' i]",
      ],
    },
    shipping: {
      selectors: [
        "textarea[name*='shipping' i][name*='instruction' i]",
        "textarea[id*='shipping' i][id*='instruction' i]",
        "textarea[id*='ShippingInstructions' i]",
        "textarea[name*='instruction' i]",
        "textarea[id*='instruction' i]",
      ],
      marker: "[Blazer AI Order]",
      maxLength: 1000,
    },
    storageKey: "blazerAiOrder.pending",
    pendingTtlMs: 2 * 60 * 60 * 1000,
    observeMs: 60 * 1000,
  };

  function isObject(v) {
    return v && typeof v === "object" && !Array.isArray(v);
  }
  function merge(base, extra) {
    var out = {};
    var k;
    for (k in base) out[k] = base[k];
    if (!isObject(extra)) return out;
    for (k in extra) {
      if (!Object.prototype.hasOwnProperty.call(extra, k)) continue;
      out[k] = isObject(base[k]) && isObject(extra[k]) ? merge(base[k], extra[k]) : extra[k];
    }
    return out;
  }

  var config = merge(DEFAULTS, window.BLAZER_BRIDGE_CONFIG);
  var attrOrigins = currentScript && currentScript.getAttribute("data-app-origin");
  var appOrigins = []
    .concat(config.appOrigins || [], attrOrigins ? attrOrigins.split(",") : [], scriptOrigin ? [scriptOrigin] : [])
    .map(function (o) {
      return String(o).trim().replace(/\/$/, "");
    })
    .filter(function (o) {
      return /^https:\/\/[^/]+$/.test(o) || /^http:\/\/localhost(:\d+)?$/.test(o);
    });

  function log() {
    if (config.debug && window.console) console.info.apply(console, ["[blazer-bridge]"].concat([].slice.call(arguments)));
  }

  // ---------- helpers ----------
  function sanitize(value, max) {
    return String(value == null ? "" : value)
      .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
      .replace(/[<>]/g, "")
      .replace(/[ \t]+/g, " ")
      .trim()
      .slice(0, max);
  }

  function validatePayload(p) {
    if (!isObject(p) || !Array.isArray(p.items)) return null;
    var items = [];
    var seen = {};
    for (var i = 0; i < p.items.length && items.length < LIMITS.maxItems; i++) {
      var it = p.items[i];
      if (!isObject(it)) continue;
      var sku = String(it.sku || "").trim();
      var qty = Math.floor(Number(it.quantity));
      if (!SKU_PATTERN.test(sku) || !isFinite(qty) || qty < 1 || qty > LIMITS.maxQuantity || seen[sku]) continue;
      seen[sku] = true;
      items.push({ sku: sku, quantity: qty });
    }
    var comments = [];
    if (Array.isArray(p.lineComments)) {
      for (var j = 0; j < p.lineComments.length && comments.length < LIMITS.maxItems; j++) {
        var c = p.lineComments[j];
        if (!isObject(c) || !SKU_PATTERN.test(String(c.sku || "")) || !seen[c.sku]) continue;
        var text = sanitize(c.comment, LIMITS.maxCommentLength);
        if (text) comments.push({ sku: c.sku, comment: text });
      }
    }
    return {
      items: items,
      lineComments: comments,
      shippingInstructions: sanitize(String(p.shippingInstructions || "").replace(/\r\n/g, "\n"), LIMITS.maxShippingLength),
    };
  }

  function query(selectors, root) {
    root = root || document;
    for (var i = 0; i < selectors.length; i++) {
      try {
        var el = root.querySelector(selectors[i]);
        if (el) return el;
      } catch {
        log("bad selector", selectors[i]);
      }
    }
    return null;
  }
  function queryAll(selectors, root) {
    var out = [];
    for (var i = 0; i < selectors.length; i++) {
      try {
        var list = (root || document).querySelectorAll(selectors[i]);
        for (var j = 0; j < list.length; j++) if (out.indexOf(list[j]) < 0) out.push(list[j]);
      } catch {
        log("bad selector", selectors[i]);
      }
    }
    return out;
  }

  /** Set a value the way a user would, so React/Angular/Knockout bindings notice it. */
  function setValue(el, value) {
    var proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new Event("blur", { bubbles: true }));
  }

  function formatQuickPad(items) {
    return items
      .map(function (i) {
        return config.quickPad.lineTemplate.replace("{sku}", i.sku).replace("{qty}", String(i.quantity));
      })
      .join("\n");
  }

  // ---------- pending storage (survives navigation to QuickPad / cart) ----------
  function readPending() {
    try {
      var raw = window.sessionStorage.getItem(config.storageKey);
      if (!raw) return null;
      var p = JSON.parse(raw);
      if (!p || Date.now() - p.createdAt > config.pendingTtlMs) {
        window.sessionStorage.removeItem(config.storageKey);
        return null;
      }
      return p;
    } catch {
      return null;
    }
  }
  function writePending(p) {
    try {
      if (p) window.sessionStorage.setItem(config.storageKey, JSON.stringify(p));
      else window.sessionStorage.removeItem(config.storageKey);
      return true;
    } catch {
      return false;
    }
  }

  // ---------- 1. QuickPad ----------
  function injectQuickPad(items) {
    var input = query(config.quickPad.inputSelectors);
    if (!input) return "not_found";
    var existing = (input.value || "").trim();
    setValue(input, (existing ? existing + "\n" : "") + formatQuickPad(items));
    try {
      input.scrollIntoView({ block: "center", behavior: "smooth" });
    } catch {}
    if (config.quickPad.autoSubmit) {
      var btn = query(config.quickPad.submitSelectors);
      if (btn) {
        btn.click();
        return "injected_and_submitted";
      }
    }
    return "injected";
  }

  // ---------- 2. Line-item comments ----------
  function normalizeSku(text) {
    return String(text || "")
      .replace(/^(item|sku|part|product)\s*(#|no\.?|number)?\s*:?\s*/i, "")
      .replace(/^#/, "")
      .trim()
      .toUpperCase();
  }

  /** Find cart rows by exact SKU text on leaf-ish elements (never substring matches). */
  function findRowSku(row) {
    var candidates = queryAll(config.cart.rowSkuSelectors, row);
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      if (el.children.length > 2) continue;
      var sku = normalizeSku(el.textContent);
      if (SKU_PATTERN.test(sku)) return sku;
    }
    return null;
  }

  function applyLineComments(pending) {
    if (!pending.lineComments || !pending.lineComments.length) return 0;
    var bySku = {};
    pending.lineComments.forEach(function (c) {
      if (!c.applied) bySku[c.sku.toUpperCase()] = c;
    });
    var applied = 0;
    queryAll(config.cart.rowSelectors).forEach(function (row) {
      var sku = findRowSku(row);
      var entry = sku && bySku[sku];
      if (!entry) return;
      var field = query(config.cart.rowCommentSelectors, row);
      if (!field) return;
      var current = (field.value || "").trim();
      if (current.indexOf(entry.comment) < 0) {
        var max = field.maxLength > 0 ? field.maxLength : LIMITS.maxCommentLength;
        setValue(field, (current ? current + " | " : "") + entry.comment.slice(0, Math.max(0, max - (current ? current.length + 3 : 0))));
      }
      entry.applied = true;
      applied++;
    });
    return applied;
  }

  // ---------- 3. Shipping instructions catch-all ----------
  function applyShipping(pending) {
    if (!pending.shippingInstructions || pending.shippingApplied) return false;
    var field = query(config.shipping.selectors);
    if (!field) return false;
    var marker = config.shipping.marker;
    var current = field.value || "";
    var max = field.maxLength > 0 ? Math.min(field.maxLength, config.shipping.maxLength) : config.shipping.maxLength;
    var block = marker + " " + pending.shippingInstructions;
    // Replace a previous Blazer block instead of appending duplicates.
    var idx = current.indexOf(marker);
    var base = (idx >= 0 ? current.slice(0, idx) : current).replace(/\s+$/, "");
    var next = (base ? base + "\n" : "") + block;
    if (next.length > max) next = next.slice(0, max);
    setValue(field, next);
    pending.shippingApplied = true;
    return true;
  }

  function applyPendingOnPage() {
    var pending = readPending();
    if (!pending) return true;

    if (pending.quickPadItems && pending.quickPadItems.length) {
      var status = injectQuickPad(pending.quickPadItems);
      if (status !== "not_found") {
        pending.quickPadItems = [];
        log("QuickPad filled from queue", status);
      }
    }
    applyLineComments(pending);
    applyShipping(pending);

    var done =
      (!pending.quickPadItems || !pending.quickPadItems.length) &&
      (!pending.shippingInstructions || pending.shippingApplied) &&
      pending.lineComments.every(function (c) {
        return c.applied;
      });
    writePending(done ? null : pending);
    return done;
  }

  function watchPage() {
    if (!readPending()) return;
    if (applyPendingOnPage()) return;
    var timer = null;
    var observer = new MutationObserver(function () {
      if (timer) return;
      timer = window.setTimeout(function () {
        timer = null;
        if (applyPendingOnPage()) observer.disconnect();
      }, 250);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    window.setTimeout(function () {
      observer.disconnect();
    }, config.observeMs);
  }

  // ---------- messaging with the iframe ----------
  function appFrames() {
    return queryAll([config.iframeSelector]).filter(function (f) {
      return f.tagName === "IFRAME";
    });
  }
  function frameForSource(source) {
    var frames = appFrames();
    for (var i = 0; i < frames.length; i++) if (frames[i].contentWindow === source) return frames[i];
    return null;
  }

  function reply(event, message) {
    message.source = BRIDGE_SOURCE;
    message.version = VERSION;
    event.source.postMessage(message, event.origin);
  }

  function handleSubmit(event, data) {
    var payload = validatePayload(data.payload);
    if (!payload || !payload.items.length) {
      reply(event, { type: "SUBMIT_RESULT", requestId: data.requestId, ok: false, quickPad: "not_found", commentsQueued: 0, shippingQueued: false, error: "No valid items." });
      return;
    }
    var pending = {
      createdAt: Date.now(),
      quickPadItems: [],
      lineComments: payload.lineComments.map(function (c) {
        return { sku: c.sku, comment: c.comment, applied: false };
      }),
      shippingInstructions: payload.shippingInstructions,
      shippingApplied: false,
    };

    var status = injectQuickPad(payload.items);
    if (status === "not_found" && config.quickPad.pageUrl) {
      pending.quickPadItems = payload.items;
      status = "redirecting";
    }
    var stored = writePending(pending);
    var result = {
      type: "SUBMIT_RESULT",
      requestId: data.requestId,
      ok: status !== "not_found" && stored,
      quickPad: status,
      commentsQueued: stored ? pending.lineComments.length : 0,
      shippingQueued: stored && !!pending.shippingInstructions,
    };
    if (status === "not_found") result.error = "QuickPad was not found on this page.";
    else if (!stored) result.error = "Browser storage is blocked; comments and shipping notes could not be queued.";
    reply(event, result);

    if (status === "redirecting") {
      var url = new URL(config.quickPad.pageUrl, window.location.href);
      if (url.origin === window.location.origin) {
        window.setTimeout(function () {
          window.location.assign(url.href);
        }, 300);
      }
    } else if (stored) {
      watchPage();
    }
  }

  window.addEventListener("message", function (event) {
    var data = event.data;
    if (appOrigins.indexOf(event.origin) < 0) return;
    if (!isObject(data) || data.source !== APP_SOURCE || data.version !== VERSION) return;
    var frame = frameForSource(event.source);
    if (!frame) return;

    switch (data.type) {
      case "PING":
        reply(event, { type: "READY", page: window.location.pathname });
        break;
      case "RESIZE": {
        var h = Math.floor(Number(data.height));
        if (isFinite(h) && h > 0 && h < 20000) frame.style.height = Math.max(config.minIframeHeight, h) + "px";
        break;
      }
      case "SUBMIT_ORDER":
        if (typeof data.requestId === "string" && data.requestId.length <= 64) handleSubmit(event, data);
        break;
    }
  });

  if (!appOrigins.length && window.console) {
    console.warn("[blazer-bridge] No app origins configured; set data-app-origin on the script tag.");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchPage);
  else watchPage();
})();
