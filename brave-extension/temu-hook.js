(() => {
  const match = location.pathname.match(/-g-(\d+)\.html/i);
  const goodsId = match?.[1] || new URLSearchParams(location.search).get("goods_id");
  if (!goodsId) return;

  const gallery = new Set();
  const outputId = "product-extractor-temu-gallery-data";
  const priceOutputId = "product-extractor-temu-price-data";
  const originalJsonParse = JSON.parse.bind(JSON);
  let bestPrice = null;

  function isImageUrl(value) {
    if (typeof value !== "string" || !value.includes("/product/")) return false;
    try {
      const url = new URL(value.replaceAll("\\/", "/"), location.href);
      const host = url.hostname.toLowerCase();
      const path = url.pathname.toLowerCase();
      return url.protocol === "https:"
        && (host.endsWith(".kwcdn.com") || host.endsWith(".temu.com"))
        && !/(logo|icon|avatar|sprite|review|user|flag|payment)/i.test(path);
    } catch {
      return false;
    }
  }

  function publish() {
    let output = document.getElementById(outputId);
    if (!output) {
      output = document.createElement("script");
      output.id = outputId;
      output.type = "application/json";
      (document.head || document.documentElement).appendChild(output);
    }
    output.textContent = JSON.stringify([...gallery].slice(0, 20));
    window.dispatchEvent(new CustomEvent("product-extractor:temu-gallery-data"));
  }

  function publishPrice(info) {
    let output = document.getElementById(priceOutputId);
    if (!output) {
      output = document.createElement("script");
      output.id = priceOutputId;
      output.type = "application/json";
      (document.head || document.documentElement).appendChild(output);
    }
    output.textContent = JSON.stringify(info);
  }

  function asPriceCandidate(entry, key) {
    if (entry == null) return null;
    const saleLike = !/(market|retail|list|origin|was|strike)/i.test(key);
    const source = saleLike ? "sale" : "other";
    if (typeof entry === "string") {
      if (!/\d/.test(entry)) return null;
      if (/[₱$£]|PHP|USD|GBP/i.test(entry) && !/€|EUR/i.test(entry)) return null;
      return { price_text: entry, price_eur: null, source };
    }
    if (typeof entry === "number" && Number.isFinite(entry) && entry > 0) {
      if (entry >= 500) return null;
      return { price_text: String(entry), price_eur: entry, source };
    }
    if (typeof entry !== "object" || Array.isArray(entry)) return null;
    const currency = String(
      entry.currency || entry.priceCurrency || entry.currencyCode || "",
    ).toUpperCase();
    if (currency && currency !== "EUR") return null;
    const text = String(
      entry.priceStr
      || entry.showPriceStr
      || entry.amountText
      || entry.priceText
      || entry.text
      || "",
    );
    if (text && /[₱$£]|PHP|USD|GBP/i.test(text) && !/€|EUR/i.test(text)) {
      return null;
    }
    if (text && /\d/.test(text)) {
      return { price_text: text, price_eur: null, source };
    }
    const amount = entry.amount ?? entry.price ?? entry.value ?? entry.number;
    if (typeof amount === "number" && Number.isFinite(amount) && amount > 0 && amount < 500) {
      return { price_text: String(amount), price_eur: amount, source };
    }
    if (typeof amount === "string" && /\d/.test(amount)) {
      return { price_text: amount, price_eur: null, source };
    }
    return null;
  }

  function considerPrice(candidate) {
    if (!candidate) return;
    const score = candidate.source === "sale" ? 2 : 1;
    if (!bestPrice || score > bestPrice.score) {
      bestPrice = { ...candidate, score };
      publishPrice({
        price_text: bestPrice.price_text,
        price_eur: bestPrice.price_eur,
        source: bestPrice.source,
      });
    }
  }

  function collectPrice(value, seen = new WeakSet(), depth = 0) {
    if (!value || typeof value !== "object" || seen.has(value) || depth > 4) {
      return;
    }
    seen.add(value);
    for (const [key, entry] of Object.entries(value)) {
      const looksLikePrice = /(?:price|amount)/i.test(key)
        && !/(image|percent|discountRate|coupon|qty|quantity)/i.test(key);
      if (looksLikePrice) {
        considerPrice(asPriceCandidate(entry, key));
        if (entry && typeof entry === "object" && !Array.isArray(entry)) {
          collectPrice(entry, seen, depth + 1);
        }
      } else if (
        entry
        && typeof entry === "object"
        && /(?:info|goods|store|data)/i.test(key)
        && depth < 2
      ) {
        collectPrice(entry, seen, depth + 1);
      }
    }
  }

  function collectImages(value, seen = new WeakSet()) {
    if (!value || typeof value !== "object" || seen.has(value)) return;
    seen.add(value);
    for (const entry of Object.values(value)) {
      if (isImageUrl(entry)) {
        const url = new URL(entry.replaceAll("\\/", "/"), location.href);
        if (url.hostname.toLowerCase().endsWith(".kwcdn.com")) {
          url.search = "";
          url.hash = "";
        }
        gallery.add(url.href);
      } else if (entry && typeof entry === "object") {
        collectImages(entry, seen);
      }
    }
  }

  function inspect(value, seen = new WeakSet()) {
    if (!value || typeof value !== "object" || seen.has(value)) return;
    seen.add(value);
    const entries = Object.entries(value);
    const ownsRequestedProduct = entries.some(([key, entry]) => (
      /^(?:goods|product)_?id$/i.test(key)
      && String(entry) === goodsId
    ));
    if (ownsRequestedProduct) {
      const before = gallery.size;
      collectImages(value);
      collectPrice(value);
      if (gallery.size !== before) publish();
      return;
    }
    for (const entry of Object.values(value)) {
      if (entry && typeof entry === "object") inspect(entry, seen);
    }
  }

  function inspectText(text) {
    if (
      typeof text !== "string"
      || !text.includes(goodsId)
      || text.length > 15_000_000
    ) return;
    try {
      inspect(originalJsonParse(text));
    } catch {}
  }

  const originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      response.clone().text().then(inspectText).catch(() => {});
      return response;
    };
  }

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (...args) {
    this.__productExtractorUrl = String(args[1] || "");
    return originalOpen.apply(this, args);
  };
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener("load", () => {
      try {
        if (this.responseType === "json") inspect(this.response);
        else if (!this.responseType || this.responseType === "text") {
          inspectText(this.responseText);
        }
      } catch {}
    }, { once: true });
    return originalSend.apply(this, args);
  };

  JSON.parse = function (text, ...args) {
    const value = originalJsonParse(text, ...args);
    if (typeof text === "string" && text.includes(goodsId)) {
      queueMicrotask(() => inspect(value));
    }
    return value;
  };
})();
