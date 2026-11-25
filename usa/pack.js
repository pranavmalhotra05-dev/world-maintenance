/* pack.js: serves files that pack_site.py bundled into pack files (see pack-manifest.json).
   Load it FIRST on every page:  <script src="pack.js"></script>
   In the local (unpacked) site it does nothing: ENABLED stays false, so no request is made.
   pack_site.py flips ENABLED to true in the dist copy. If pack-manifest.json is then missing,
   every request still falls through to the network unchanged.
   Covers: fetch() (GET/HEAD), folder listings of packed folders, <img> elements (src property,
   setAttribute, and <img src> parsed from HTML) and clicks on <a href> that point at a packed file. */
(function () {
  "use strict";
  var ENABLED = false; /*PACK_ENABLED*/
  if (!ENABLED || window.__packJs) return;
  window.__packJs = true;

  var cs = document.currentScript;
  var ROOT = new URL(".", (cs && cs.src) || location.href);
  var origFetch = window.fetch.bind(window);
  var MAN = null, DIRS = null;
  var manP = origFetch(new URL("pack-manifest.json", ROOT).href)
    .then(function (r) { return r.ok ? r.json() : null; })
    .catch(function () { return null; })
    .then(function (m) {
      MAN = m && typeof m === "object" ? m : {};
      DIRS = {};
      Object.keys(MAN).forEach(function (k) {
        var parts = k.split("/"), dir = "";
        for (var i = 0; i < parts.length; i++) {
          var name = parts[i] + (i < parts.length - 1 ? "/" : "");
          (DIRS[dir] = DIRS[dir] || {})[name] = 1;
          dir += name;
        }
      });
      return MAN;
    });

  // site-relative key of a URL (query/hash dropped), or null when outside the site
  function keyOf(u) {
    try { u = new URL(u, location.href); } catch (e) { return null; }
    if (u.origin !== ROOT.origin || u.pathname.indexOf(ROOT.pathname) !== 0) return null;
    try { return decodeURIComponent(u.pathname.slice(ROOT.pathname.length)); } catch (e) { return null; }
  }

  // packs are base64 text (artifacts serve no binary type)
  function b64ToBuf(t) {
    t = t.replace(/\s+/g, "");
    if (Uint8Array.fromBase64) return Uint8Array.fromBase64(t).buffer;
    var s = atob(t), n = s.length, u = new Uint8Array(n);
    for (var i = 0; i < n; i++) u[i] = s.charCodeAt(i);
    return u.buffer;
  }
  // bytes -> ASCII bytes of their base64 text ("b64gz" entries were base64 files stored decoded)
  function b64Bytes(u) {
    var t;
    if (u.toBase64) t = u.toBase64();
    else { var parts = [], C = 0x8000; for (var i = 0; i < u.length; i += C) parts.push(String.fromCharCode.apply(null, u.subarray(i, i + C))); t = btoa(parts.join("")); }
    var o = new Uint8Array(t.length);
    for (var j = 0; j < t.length; j++) o[j] = t.charCodeAt(j);
    return o;
  }
  var PACKS = {};
  function packBuf(p) {
    if (!PACKS[p]) {
      PACKS[p] = origFetch(new URL(p, ROOT).href).then(function (r) {
        if (!r.ok) throw new Error("pack " + p + " HTTP " + r.status);
        return r.text();
      }).then(b64ToBuf);
      PACKS[p].catch(function () { delete PACKS[p]; });
    }
    return PACKS[p];
  }
  // entry: [pack, offset, storedLength, type, encoding, rawLength]
  //   or, for a file split across packs: [null, 0, storedLength, type, encoding, rawLength, [[pack, offset, length], ...]]
  // encoding "gzip": stored compressed, inflated here with DecompressionStream
  function bytesOf(k) {
    var e = MAN && MAN[k];
    if (!e) return Promise.resolve(null);
    var segs = e[0] === null ? e[6] : [[e[0], e[1], e[2]]];
    return Promise.all(segs.map(function (s) {
      return packBuf(s[0]).then(function (b) { return new Uint8Array(b, s[1], s[2]); });
    })).then(function (parts) {
      var out = parts[0];
      if (parts.length > 1) {
        out = new Uint8Array(e[2]); var o = 0;
        parts.forEach(function (p) { out.set(p, o); o += p.length; });
      }
      if (e[4] !== "gzip" && e[4] !== "b64gz") return out;
      return new Response(new Blob([out]).stream().pipeThrough(new DecompressionStream("gzip"))).arrayBuffer()
        .then(function (b) { return e[4] === "b64gz" ? b64Bytes(new Uint8Array(b)) : new Uint8Array(b); });
    });
  }

  function listingHTML(dir) {
    var names = Object.keys(DIRS[dir]).sort();
    return "<!DOCTYPE HTML><html><head><meta charset=\"utf-8\"><title>Directory listing for /" + dir + "</title></head><body><h1>Directory listing for /" + dir +
      "</h1><hr><ul>\n" + names.map(function (n) {
        var h = n.slice(-1) === "/" ? encodeURIComponent(n.slice(0, -1)) + "/" : encodeURIComponent(n);
        return "<li><a href=\"" + h + "\">" + n.replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</a></li>";
      }).join("\n") + "\n</ul><hr></body></html>\n";
  }

  function serve(k, head) {
    if (MAN[k]) {
      var e = MAN[k];
      return bytesOf(k).then(function (b) {
        return new Response(head ? null : b, { status: 200, statusText: "OK", headers: { "Content-Type": e[3] || "application/octet-stream", "Content-Length": String(e[5] != null ? e[5] : e[2]) } });
      });
    }
    if (k.slice(-1) === "/" && DIRS[k]) {   // folder listing of a packed folder (the site root is never packed)
      var html = listingHTML(k);
      return Promise.resolve(new Response(head ? null : html, { status: 200, statusText: "OK", headers: { "Content-Type": "text/html; charset=utf-8" } }));
    }
    return Promise.resolve(null);
  }

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : input instanceof URL ? input.href : input && input.url;
    var method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
    if (!url || (method !== "GET" && method !== "HEAD")) return origFetch(input, init);
    var k = keyOf(url);
    if (k == null) return origFetch(input, init);
    return manP.then(function () { return serve(k, method === "HEAD"); })
      .then(function (res) { return res || origFetch(input, init); });
  };

  // ---- images: packed files become blob: URLs
  var BLOBS = {};
  function blobUrl(k) {
    if (!BLOBS[k]) {
      BLOBS[k] = bytesOf(k).then(function (b) { return b ? URL.createObjectURL(new Blob([b], { type: MAN[k][3] || "" })) : null; })
        .catch(function () { return null; });
      BLOBS[k].then(function (u) { if (!u) delete BLOBS[k]; });
    }
    return BLOBS[k];
  }
  var srcDesc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
  function rawSet(img, v) { img.__packSkip = v; srcDesc.set.call(img, v); }
  function setImg(img, v) {
    var k = keyOf(v);
    if (k == null || /^(blob|data):/i.test(v) || (MAN && !MAN[k])) return rawSet(img, v);
    var tok = img.__packTok = {};
    if (img.hasAttribute("src")) img.removeAttribute("src");   // stops any request for the unpacked path
    manP.then(function () { return MAN[k] ? blobUrl(k) : null; }).then(function (b) {
      if (img.__packTok === tok) rawSet(img, b || v);
    });
  }
  Object.defineProperty(HTMLImageElement.prototype, "src", {
    configurable: true, enumerable: srcDesc.enumerable, get: srcDesc.get,
    set: function (v) { setImg(this, String(v)); }
  });
  var origSetAttr = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (n, v) {
    if (this instanceof HTMLImageElement && String(n).toLowerCase() === "src") return setImg(this, String(v));
    return origSetAttr.apply(this, arguments);
  };
  function fix(img) {
    var v = img.getAttribute("src");
    if (!v || v === img.__packSkip || /^(blob|data):/i.test(v)) return;
    var k = keyOf(v);
    if (k == null || (MAN && !MAN[k])) return;
    setImg(img, v);
  }
  function scan(n) {
    if (n.nodeType !== 1) return;
    if (n.tagName === "IMG") fix(n);
    else if (n.querySelectorAll) Array.prototype.forEach.call(n.querySelectorAll("img[src]"), fix);
  }
  new MutationObserver(function (ms) {
    ms.forEach(function (m) {
      if (m.type === "childList") Array.prototype.forEach.call(m.addedNodes, scan);
      else if (m.target.tagName === "IMG") fix(m.target);
    });
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });

  // ---- links to packed files (e.g. photo thumbnails that open full size)
  document.addEventListener("click", function (ev) {
    var a = ev.target && ev.target.closest && ev.target.closest("a[href]");
    if (!a || ev.defaultPrevented) return;
    var k = keyOf(a.href);
    if (k == null || !MAN || !MAN[k]) return;
    ev.preventDefault();
    var w = a.target && a.target !== "_self" ? window.open("", a.target) : null;
    blobUrl(k).then(function (u) {
      if (!u) return;
      if (w) w.location.href = u; else location.href = u;
    });
  }, true);
})();
