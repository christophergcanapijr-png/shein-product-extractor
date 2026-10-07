(() => {
  const sleep = (milliseconds) => new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });

  const send = (message) => chrome.runtime.sendMessage(message).catch(() => null);

  function normalized(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  function isProductUrl(value) {
    try {
      const url = new URL(value, location.href);
      const path = decodeURIComponent(url.pathname).toLowerCase();
      return (url.hostname === "temu.com" || url.hostname.endsWith(".temu.com"))
        && (
          path.includes("goods.html")
          || path.includes("/goods/")
          || path.includes("-g-")
        );
    } catch {
      return false;
    }
  }

  function visible(element) {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none"
      && style.visibility !== "hidden"
      && Number(style.opacity || 1) > 0
      && rect.width > 1
      && rect.height > 1;
  }

  function bodyText() {
    return String(document.body?.innerText || "");
  }

  function needsManualAttention() {
    const path = location.pathname.toLowerCase();
    const text = bodyText().toLowerCase();
    if (
      path.includes("bgn_verification")
      || path.includes("/login")
      || text.includes("verify you are human")
      || text.includes("security verification")
      || text.includes("vérification de sécurité")
    ) {
      return true;
    }
    return false;
  }

  async function waitForManualAttention() {
    await send({
      type: "temu-needs-attention",
      message: "Temu needs attention. Complete the visible check in your normal Brave window.",
    });
    for (let attempt = 0; attempt < 240; attempt += 1) {
      await sleep(500);
      if (!needsManualAttention()) {
        location.reload();
        return;
      }
    }
    await send({
      type: "temu-fail",
      code: "temu_manual_action_timeout",
      message: "Temu still requires login or verification in Brave. Complete it, then retry.",
      retryable: true,
    });
  }

  function canonicalProductKey(value) {
    try {
      const url = new URL(value, location.href);
      const goodsId = url.searchParams.get("goods_id");
      return goodsId ? `goods:${goodsId}` : `${url.origin}${url.pathname}`;
    } catch {
      return value;
    }
  }

  function collectSearchCandidates(sku) {
    const candidates = new Map();
    const wanted = normalized(sku);
    document.querySelectorAll('a[href*="goods.html"], a[href*="-g-"], a[href*="/goods/"]')
      .forEach((anchor) => {
        if (!isProductUrl(anchor.href)) return;
        const blob = [
          anchor.innerText,
          anchor.getAttribute("aria-label"),
          anchor.getAttribute("title"),
          anchor.getAttribute("data-sku"),
          anchor.getAttribute("data-product-sku"),
        ].filter(Boolean).join(" ");
        const key = canonicalProductKey(anchor.href);
        const current = candidates.get(key);
        const rect = anchor.getBoundingClientRect();
        const marker = String(
          anchor.closest('[class*="recommend" i], [id*="recommend" i]')?.className || "",
        );
        const candidate = {
          url: anchor.href,
          blob,
          exact: normalized(blob).includes(wanted),
          visible: visible(anchor),
          top: rect.top + window.scrollY,
          recommended: /recommend/i.test(marker),
        };
        if (!current || (candidate.exact && !current.exact)) {
          candidates.set(key, candidate);
        }
      });
    return [...candidates.values()];
  }

  function hasNoResults() {
    const text = bodyText().toLowerCase().replace(/\s+/g, " ");
    return [
      "no results for",
      "no results found",
      "aucun résultat pour",
      "aucun produit trouvé",
      "we couldn't find",
    ].some((marker) => text.includes(marker));
  }

  async function runSearch(job) {
    const params = new URLSearchParams(location.search);
    if (normalized(params.get("search_key")) !== normalized(job.sku)) {
      await send({
        type: "temu-fail",
        code: "temu_search_changed",
        message: "The Temu search field no longer contains the requested SKU.",
        retryable: true,
      });
      return;
    }
    await send({
      type: "temu-progress",
      phase: "searching",
      message: `Searching Temu for "${job.sku}" in your Brave profile.`,
    });

    for (let attempt = 0; attempt < 24; attempt += 1) {
      if (needsManualAttention()) {
        await waitForManualAttention();
        return;
      }
      if (hasNoResults()) {
        await send({
          type: "temu-fail",
          code: "sku_not_found",
          message: `No Temu product was found for SKU "${job.sku}".`,
          retryable: false,
        });
        return;
      }

      const candidates = collectSearchCandidates(job.sku);
      const exact = candidates.filter((candidate) => candidate.exact);
      const ordinary = candidates.filter((candidate) => !candidate.recommended);
      let selected = null;
      if (exact.length === 1) {
        selected = exact[0];
      } else if (candidates.length === 1) {
        selected = candidates[0];
      } else if (ordinary.length === 1) {
        selected = ordinary[0];
      }
      // Temu search cards almost never include the SKU. After the first
      // paint, a single ordinary result from an exact-SKU search is enough.
      if (!selected && attempt >= 4 && ordinary.length === 1) {
        selected = ordinary[0];
      }
      if (selected) {
        await send({ type: "temu-open-product", url: selected.url });
        return;
      }
      if (attempt >= 8 && ordinary.length > 1 && exact.length !== 1) {
        await send({
          type: "temu-fail",
          code: "temu_result_ambiguous",
          message: "Temu showed several products but none could be proven to be the exact SKU. No product was opened.",
          retryable: false,
        });
        return;
      }
      await sleep(250);
    }

    const candidates = collectSearchCandidates(job.sku);
    await send({
      type: "temu-fail",
      code: candidates.length ? "temu_result_ambiguous" : "sku_not_found",
      message: candidates.length
        ? "Temu showed several products but none could be proven to be the exact SKU. No product was opened."
        : `No Temu product was found for SKU "${job.sku}".`,
      retryable: false,
    });
  }

  function addImage(target, value) {
    if (!value || typeof value !== "string") return;
    value.split(",").forEach((part) => {
      const raw = part.trim().split(/\s+/)[0];
      if (!raw || raw.startsWith("data:")) return;
      try {
        const url = new URL(raw, location.href);
        const host = url.hostname.toLowerCase();
        const path = url.pathname.toLowerCase();
        if (
          url.protocol === "https:"
          && (host.endsWith(".kwcdn.com") || host.endsWith(".temu.com"))
          && path.includes("/product/")
          && !/(logo|icon|avatar|sprite|review|user|flag|payment)/i.test(path)
        ) {
          if (host.endsWith(".kwcdn.com")) {
            url.search = "";
            url.hash = "";
          }
          target.add(url.href);
        }
      } catch {}
    });
  }

  function walkJson(value, visit) {
    if (Array.isArray(value)) {
      value.forEach((entry) => walkJson(entry, visit));
    } else if (value && typeof value === "object") {
      visit(value);
      Object.values(value).forEach((entry) => walkJson(entry, visit));
    }
  }

  function jsonLdProducts() {
    const products = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach((script) => {
      try {
        walkJson(JSON.parse(script.textContent), (entry) => {
          const type = String(entry["@type"] || "").toLowerCase();
          if (type === "product") products.push(entry);
        });
      } catch {}
    });
    return products;
  }

  function sameProductPage(value) {
    try {
      const candidate = new URL(value, location.href);
      const current = new URL(location.href);
      const candidateId = candidate.searchParams.get("goods_id");
      const currentId = current.searchParams.get("goods_id");
      if (candidateId && currentId) return candidateId === currentId;
      return candidate.pathname.replace(/\/+$/, "") === current.pathname.replace(/\/+$/, "");
    } catch {
      return false;
    }
  }

  function primaryJsonLdProduct() {
    const heading = String(document.querySelector("h1")?.innerText || "")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
    let best = null;
    let bestScore = -1;
    for (const product of jsonLdProducts()) {
      let score = 0;
      const productUrl = product.url
        || product.mainEntityOfPage?.["@id"]
        || product.mainEntityOfPage;
      const name = String(product.name || "").replace(/\s+/g, " ").trim().toLowerCase();
      if (productUrl && sameProductPage(productUrl)) score += 20;
      if (heading && name && (heading === name || heading.includes(name) || name.includes(heading))) {
        score += 12;
      }
      if (product.offers) score += 2;
      if (product.image) score += 1;
      if (score > bestScore) {
        best = product;
        bestScore = score;
      }
    }
    return bestScore >= 3 ? best : null;
  }

  function productGalleryImages() {
    const heading = document.querySelector("h1");
    if (!heading) return [];
    const headingRect = heading.getBoundingClientRect();
    const candidates = [...document.images]
      .filter((image) => {
        if (image.closest('a[href*="goods.html"], a[href*="-g-"]')) return false;
        const rect = image.getBoundingClientRect();
        const source = image.currentSrc || image.src || image.dataset.src || "";
        return source.includes("kwcdn.com/product/")
          && rect.width >= 180
          && rect.height >= 180
          && rect.top < headingRect.bottom + 900
          && rect.bottom > headingRect.top - 700
          && (
            rect.left < headingRect.left
            || Math.abs(rect.top - headingRect.bottom) < 500
          );
      })
      .sort((left, right) => {
        const a = left.getBoundingClientRect();
        const b = right.getBoundingClientRect();
        const leftBesideTitle = a.left < headingRect.left ? 2 : 1;
        const rightBesideTitle = b.left < headingRect.left ? 2 : 1;
        return (
          (rightBesideTitle * b.width * b.height)
          - (leftBesideTitle * a.width * a.height)
        );
      });
    const mainImage = candidates[0];
    if (!mainImage) return [];

    // Walk up only through the image-side column. Stop before the ancestor
    // also contains the product title; that next level can include Temu's
    // recommendations and "you may also like" feeds.
    let galleryScope = mainImage.parentElement;
    let node = mainImage.parentElement;
    for (let depth = 0; node?.parentElement && depth < 8; depth += 1) {
      const parent = node.parentElement;
      if (parent.contains(heading)) break;
      const images = [...node.querySelectorAll("img")].filter((image) => (
        !image.closest('a[href*="goods.html"], a[href*="-g-"]')
        && String(
          image.currentSrc || image.src || image.dataset.src || "",
        ).includes("kwcdn.com/product/")
      ));
      if (images.length <= 30) galleryScope = node;
      node = parent;
    }
    const scoped = [...galleryScope.querySelectorAll("img")].filter((image) => {
      if (image.closest('a[href*="goods.html"], a[href*="-g-"]')) return false;
      const source = image.currentSrc || image.src || image.dataset.src || "";
      return source.includes("kwcdn.com/product/");
    });
    const mainRect = mainImage.getBoundingClientRect();
    const nearbyThumbnails = [...document.images].filter((image) => {
      if (image === mainImage) return false;
      if (image.closest('a[href*="goods.html"], a[href*="-g-"]')) return false;
      const rect = image.getBoundingClientRect();
      const source = image.currentSrc || image.src || image.dataset.src || "";
      return source.includes("kwcdn.com/product/")
        && rect.width >= 28
        && rect.height >= 28
        && rect.width <= 180
        && rect.height <= 180
        && rect.left < headingRect.left
        && rect.top >= mainRect.top - 120
        && rect.bottom <= mainRect.bottom + 120;
    }).sort((left, right) => (
      left.getBoundingClientRect().top - right.getBoundingClientRect().top
    ));
    return [
      mainImage,
      ...new Set([
        ...nearbyThumbnails,
        ...scoped.filter((image) => image !== mainImage),
      ]),
    ];
  }

  function readStaticProductImages(target) {
    try {
      const captured = JSON.parse(
        document.getElementById("product-extractor-temu-gallery-data")?.textContent
        || "[]",
      );
      captured.forEach((value) => addImage(target, value));
    } catch {}
    document.querySelectorAll('meta[property="og:image"], meta[name="twitter:image"]')
      .forEach((meta) => addImage(target, meta.content));
    const product = primaryJsonLdProduct();
    if (product) {
      const values = Array.isArray(product.image) ? product.image : [product.image];
      values.forEach((image) => {
        addImage(target, typeof image === "string" ? image : image?.url);
      });
    }
    productGalleryImages().forEach((image) => {
      ["data-original", "data-src", "src", "srcset"].forEach((attribute) => {
        addImage(target, image.getAttribute(attribute));
      });
      addImage(target, image.currentSrc);
    });
  }

  async function collectProductImages(allowInteraction) {
    const images = new Set();
    readStaticProductImages(images);
    if (images.size >= 6 || !allowInteraction) return [...images].slice(0, 6);

    const galleryImages = productGalleryImages();
    for (const [index, image] of galleryImages.slice(0, 8).entries()) {
      const target = image.closest("button, [role=button], li, div") || image;
      for (const eventName of ["pointerenter", "mouseenter", "mouseover"]) {
        target.dispatchEvent(new MouseEvent(eventName, {
          bubbles: true,
          cancelable: true,
          view: window,
        }));
      }
      if (index > 0) {
        try {
          target.click();
        } catch {}
        await sleep(80);
      }
      galleryImages.forEach((updated) => {
        ["data-original", "data-src", "src", "srcset"].forEach((attribute) => {
          addImage(images, updated.getAttribute(attribute));
        });
        addImage(images, updated.currentSrc);
      });
      if (images.size >= 6) break;
    }
    return [...images].slice(0, 6);
  }

  function extractTitle() {
    const product = primaryJsonLdProduct();
    const values = [
      document.querySelector("h1")?.innerText,
      product?.name,
      document.querySelector('meta[property="og:title"]')?.content,
      document.title,
    ];
    return values.map((value) => String(value || "").replace(/\s+/g, " ").trim())
      .find((value) => value && !/^temu\b/i.test(value)) || "";
  }

  function parseDisplayedPrice(value) {
    const compact = String(value || "").replace(/\s+/g, " ").trim();
    if (!compact) return null;
    if (/[₱$£]|PHP|USD|GBP/i.test(compact) && !/€|EUR/i.test(compact)) return null;
    const splitMatches = [...compact.matchAll(/(?<!\d)(\d{1,4})\s*€\s*(\d{1,2})(?!\d)/g)];
    const split = splitMatches.at(-1);
    if (split) {
      return {
        price_eur: Number(`${split[1]}.${split[2].padStart(2, "0")}`),
        price_text: compact,
      };
    }
    const markedMatches = [...compact.matchAll(
      /€\s*(\d{1,4}(?:[ .]\d{3})*(?:[,.]\d{1,2})?)|(\d{1,4}(?:[ .]\d{3})*(?:[,.]\d{1,2})?)\s*(?:€|EUR\b)/gi,
    )];
    const marked = markedMatches.at(-1);
    if (marked) {
      const raw = (marked[1] || marked[2]).replace(/\s/g, "").replace(",", ".");
      const numeric = Number(raw);
      if (Number.isFinite(numeric) && numeric > 0) {
        return { price_eur: numeric, price_text: compact };
      }
    }
    return /\d/.test(compact) ? { price_eur: null, price_text: compact } : null;
  }

  function visibleCurrentPrice() {
    const heading = document.querySelector("h1");
    const scope = heading?.parentElement?.parentElement || document.body;
    const candidates = [];
    scope.querySelectorAll("[aria-label], [itemprop='price'], span, strong, div").forEach((element) => {
      if (!visible(element)) return;
      const style = getComputedStyle(element);
      if (style.textDecorationLine.includes("line-through")) return;
      if (/(original|market|was-|retail|strikethrough)/i.test(element.className || "")) {
        return;
      }
      const label = element.getAttribute("aria-label") || "";
      const text = (element.innerText || "").replace(/\s+/g, " ").trim();
      const value = /€|EUR/i.test(label) ? label : text;
      if (!/€|EUR/i.test(value) || !/\d/.test(value) || value.length > 40) return;
      const parsed = parseDisplayedPrice(value);
      if (parsed?.price_eur) candidates.push(parsed);
    });
    return candidates[0] || null;
  }

  function extractPrice() {
    try {
      const hooked = JSON.parse(
        document.getElementById("product-extractor-temu-price-data")?.textContent
        || "null",
      );
      if (hooked) {
        if (
          typeof hooked.price_eur === "number"
          && hooked.price_eur > 0
          && hooked.price_eur < 500
        ) {
          return {
            price_eur: hooked.price_eur,
            price_text: hooked.price_text || `${hooked.price_eur} EUR`,
          };
        }
        const parsedHook = parseDisplayedPrice(hooked.price_text);
        if (parsedHook?.price_eur) return parsedHook;
      }
    } catch {}

    const primaryProduct = primaryJsonLdProduct();
    for (const product of primaryProduct ? [primaryProduct] : []) {
      const offers = Array.isArray(product.offers) ? product.offers : [product.offers];
      for (const offer of offers) {
        if (!offer) continue;
        const currency = String(offer.priceCurrency || "").toUpperCase();
        if (currency && currency !== "EUR") continue;
        const parsedOffer = parseDisplayedPrice(
          offer.price ?? offer.lowPrice ?? offer.priceCurrency,
        );
        if (parsedOffer?.price_eur) return parsedOffer;
        const numeric = Number(offer.price ?? offer.lowPrice);
        if (Number.isFinite(numeric) && numeric > 0 && numeric < 500) {
          return { price_eur: numeric, price_text: `${numeric} EUR` };
        }
      }
    }

    const visiblePrice = visibleCurrentPrice();
    if (visiblePrice) return visiblePrice;

    const structured = [
      ...document.querySelectorAll(
        'meta[property="product:price:amount"], [itemprop="price"]',
      ),
    ];
    for (const element of structured.slice(0, 8)) {
      const value = element.content
        || element.getAttribute("data-price")
        || element.getAttribute("aria-label")
        || element.innerText;
      const parsed = parseDisplayedPrice(value);
      if (parsed?.price_eur || parsed?.price_text) return parsed;
    }
    return parseDisplayedPrice(
      bodyText().match(/(?:€\s*\d{1,4}(?:[.,]\d{1,2})?|\d{1,4}\s*€\s*\d{1,2}|\d{1,4}(?:[.,]\d{1,2})?\s*(?:€|EUR))/i)?.[0],
    ) || { price_eur: null, price_text: null };
  }

  function extractLabeledDetail(text, labels) {
    for (const label of labels) {
      const pattern = new RegExp(`(?:^|\\n)\\s*${label}\\s*[:：]?\\s*([^\\n|]{1,160})`, "i");
      const match = text.match(pattern);
      if (match) return match[1].trim();
    }
    return null;
  }

  function extractCategory() {
    const breadcrumb = [
      ...document.querySelectorAll(
        'nav[aria-label*="breadcrumb" i] a, [class*="breadcrumb" i] a',
      ),
    ].map((element) => element.innerText.trim()).filter(Boolean);
    return breadcrumb.at(-1) || null;
  }

  function parseMeasurementText(text) {
    const measurements = {};
    const originals = {};
    const labels = [
      "Bust", "Chest", "Waist", "Hip", "Hips", "Length", "Sleeve Length",
      "Shoulder", "Cuff", "Thigh", "Inseam",
      "Poitrine", "Tour de taille", "Hanches", "Longueur",
      "Longueur des manches", "Carrure",
    ];
    for (const label of labels) {
      const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const match = text.match(
        new RegExp(`${escaped}\\s*[:：]?\\s*(\\d+(?:[.,]\\d+)?\\s*(?:cm|in(?:ch(?:es)?)?))`, "i"),
      );
      if (match) {
        measurements[label] = match[1].replace(",", ".");
        originals[label] = match[0].trim();
      }
    }
    return { measurements, originals };
  }

  function extractionSizeLetter(itemType) {
    if (itemType === "dress_m") return "M";
    if (itemType === "jeans") return "L";
    if (["dress", "skirt", "coat", "jacket"].includes(itemType)) return "S";
    return null;
  }

  function sizeLetterMatches(text, sizeLetter) {
    if (sizeLetter === "M") {
      return /(?:^|\s|\()M(?:\)|\s|$)/i.test(text)
        && !/(?:^|\s|\()(?:SM|XM)(?:\)|\s|$)/i.test(text);
    }
    if (sizeLetter === "L") {
      return /(?:^|\s|\()L(?:\)|\s|$)/i.test(text)
        && !/(?:^|\s|\()(?:XL|XXL|2XL|3XL)(?:\)|\s|$)/i.test(text);
    }
    return /(?:^|\s|\()S(?:\)|\s|$)/i.test(text)
      && !/(?:^|\s|\()XS(?:\)|\s|$)/i.test(text);
  }

  async function extractSizeSMeasurements(itemType) {
    const sizeLetter = extractionSizeLetter(itemType);
    if (!sizeLetter) {
      return {
        measurements: {},
        measurement_originals: {},
        measurement_table_type: `Measurements not required for ${itemType}`,
      };
    }

    const sizeCandidates = [
      ...document.querySelectorAll(
        '[data-attr_value_name], [data-size-radio], [role="radio"], button, label',
      ),
    ].filter((element) => {
      const text = [
        element.getAttribute("data-attr_value_name"),
        element.getAttribute("aria-label"),
        element.innerText,
      ].filter(Boolean).join(" ").trim();
      return sizeLetterMatches(text, sizeLetter) && visible(element);
    });
    if (!sizeCandidates.length) {
      return {
        measurements: {},
        measurement_originals: {},
        measurement_table_type: `Size ${sizeLetter} was not offered on this Temu product`,
      };
    }

    const size = sizeCandidates[0];
    size.scrollIntoView({ block: "center", inline: "center" });
    for (const eventName of ["pointerenter", "mouseenter", "mouseover", "mousemove"]) {
      size.dispatchEvent(new MouseEvent(eventName, {
        bubbles: true,
        cancelable: true,
        view: window,
      }));
    }
    await sleep(350);

    const visiblePanels = [
      ...document.querySelectorAll(
        '[role="dialog"], [role="tooltip"], [class*="popover" i], [class*="tooltip" i],'
        + ' [class*="size" i], table',
      ),
    ].filter(visible);
    const measurementText = visiblePanels
      .map((element) => element.innerText || "")
      .filter((text) => /(bust|chest|waist|hip|length|poitrine|taille|hanche|longueur)/i.test(text))
      .join("\n");
    const parsed = parseMeasurementText(measurementText);
    return {
      measurements: parsed.measurements,
      measurement_originals: parsed.originals,
      measurement_table_type: Object.keys(parsed.measurements).length
        ? `Size ${sizeLetter} measurements read by hovering in Brave`
        : `Size ${sizeLetter} found, but no visible Temu measurement card was available`,
    };
  }

  const TEMU_LIVE_LISTING_MARKERS = [
    "add to cart",
    "add to bag",
    "buy now",
    "ajouter au panier",
    "acheter maintenant",
  ];
  const TEMU_UNAVAILABLE_PRODUCT_MARKERS = [
    "this item is sold out",
    "this item is unavailable",
    "cet article est épuisé",
    "cet article est indisponible",
  ];

  function isTemuUnavailableProductText(text) {
    const compact = String(text || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!compact) return false;
    if (TEMU_LIVE_LISTING_MARKERS.some((marker) => compact.includes(marker))) {
      return false;
    }
    const head = compact.slice(0, 500);
    return TEMU_UNAVAILABLE_PRODUCT_MARKERS.some((marker) => head.includes(marker));
  }

  function collectSimilarItemCandidates(currentHref) {
    const currentKey = canonicalProductKey(currentHref);
    const seen = new Set();
    const candidates = [];
    document.querySelectorAll('a[href*="goods.html"], a[href*="-g-"], a[href*="/goods/"]')
      .forEach((anchor) => {
        if (!isProductUrl(anchor.href) || !visible(anchor)) return;
        const key = canonicalProductKey(anchor.href);
        if (key === currentKey || seen.has(key)) return;
        seen.add(key);
        candidates.push(anchor.href);
      });
    return candidates;
  }

  async function runProduct(job) {
    if (!isProductUrl(location.href)) {
      await send({
        type: "temu-fail",
        code: "temu_wrong_product",
        message: "Temu did not open a product page.",
        retryable: true,
      });
      return;
    }

    await send({
      type: "temu-progress",
      phase: "reading_product",
      message: job.itemType === "dress_m"
        ? "Reading the product, Size M details, price, and every gallery image."
        : job.itemType === "jeans"
          ? "Reading the product, Size L details, price, and every gallery image."
        : "Reading the product, Size S details, price, and every gallery image.",
    });

    let title = "";
    let images = [];
    for (let attempt = 0; attempt < 10; attempt += 1) {
      if (needsManualAttention()) {
        await waitForManualAttention();
        return;
      }
      title = extractTitle();
      images = await collectProductImages(attempt >= 3);
      if (title && images.length >= 6) break;
      if (title && images.length && attempt >= 6) break;
      await sleep(200);
    }
    if (!title || !images.length) {
      // Only treat this as a dead listing once normal reading has already
      // failed a few times over ~2s — checking eagerly on page load caused
      // false positives on live, in-stock products that just hadn't
      // finished rendering yet.
      if (isTemuUnavailableProductText(bodyText())) {
        if (job.soldOutRecovery) {
          await send({
            type: "temu-fail",
            code: "temu_product_unavailable",
            message: "This Temu listing and the similar item Temu suggested are both "
              + "sold out. Paste a different, in-stock product link and retry.",
            retryable: false,
          });
          return;
        }
        const candidates = collectSimilarItemCandidates(location.href);
        if (!candidates.length) {
          await send({
            type: "temu-fail",
            code: "temu_product_unavailable",
            message: "This Temu listing is sold out and Temu did not offer a "
              + "similar in-stock item to fall back to.",
            retryable: false,
          });
          return;
        }
        await send({
          type: "temu-open-product",
          url: candidates[0],
          reason: "sold_out_similar",
        });
        return;
      }
      await send({
        type: "temu-fail",
        code: !title ? "product_title_unavailable" : "product_image_unavailable",
        message: !title
          ? "The Temu product title could not be read."
          : "No original Temu product images could be found.",
        retryable: true,
      });
      return;
    }

    const text = bodyText().slice(0, 250000);
    const price = extractPrice();
    const measurements = await extractSizeSMeasurements(job.itemType);
    await send({
      type: "temu-complete",
      payload: {
        product_url: location.href,
        title,
        image_urls: images,
        price_text: price.price_text,
        price_eur: price.price_eur,
        body_text: text,
        colour: extractLabeledDetail(text, ["Color", "Colour", "Couleur"]),
        material: extractLabeledDetail(text, ["Material", "Materials", "Composition", "Matière"]),
        category: extractCategory(),
        ...measurements,
      },
    });
  }

  async function main() {
    let response = null;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      response = await send({ type: "temu-page-ready" });
      if (response?.ok && response.job) break;
      await sleep(250);
    }
    const job = response?.job;
    if (!job) return;
    if (needsManualAttention()) {
      await waitForManualAttention();
    } else if (job.phase === "search") {
      await runSearch(job);
    } else if (job.phase === "product") {
      await runProduct(job);
    }
  }

  main().catch((error) => {
    send({
      type: "temu-fail",
      code: "temu_reader_error",
      message: error.message || "The Temu page could not be read.",
      retryable: true,
    });
  });
})();
