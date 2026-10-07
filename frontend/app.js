const state = {
  running: false,
  batchGenerating: false,
  products: new Map(),
  queue: new Map(),
  toastTimer: null,
  previewImages: [],
  previewIndex: 0,
  selectedHistoryIds: new Set(),
  currentBatch: 1,
  activeBatch: null,
  batchCounts: new Map(),
  batchNames: new Map(),
  historyItems: [],
  temuExtensionReady: (
    document.documentElement.dataset.temuExtractorExtension === "ready"
  ),
  dotbExtensionReady: (
    document.documentElement.dataset.dotbExtractorExtension === "ready"
  ),
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const clipboardImageCache = new Map();

const priceCategoryOptions = [
  ["mask", "Mask"],
  ["nightstand", "Nightstand"],
  ["plant", "Plant"],
  ["carpet", "Carpet"],
  ["cushion", "Cushion"],
  ["curtains", "Curtains"],
  ["baggy_jeans", "Baggy jeans"],
  ["sets", "Sets"],
  ["pants", "Pants"],
  ["heeled_sandal", "Heeled sandal"],
  ["long_boots", "Long boots"],
  ["coats", "Coats"],
  ["dress", "Dress"],
  ["expensive_dress", "Expensive dress"],
  ["shorts", "Shorts"],
  ["tops", "Tops / tank tops"],
  ["earrings", "Earrings"],
  ["jackets", "Jackets"],
  ["long_shorts", "Long shorts"],
  ["bracelets", "Bracelets"],
  ["bed_linens", "Bed linens"],
  ["shoulder_bag", "Shoulder bag"],
  ["handbag", "Handbag"],
  ["hats", "Hats"],
  ["watch", "Watch"],
  ["shelf", "Shelf"],
  ["cape_coats", "Cape coats"],
  ["lace_gloves", "Lace gloves"],
  ["skirt", "Skirt"],
  ["necklace", "Necklace"],
  ["cat_toy", "Cat toy"],
  ["belt", "Belt"],
  ["mirror", "Mirror"],
  ["suspended_decorations", "Suspended decorations"],
  ["lamp", "Lamp"],
  ["organizer", "Organizer"],
  ["sculpture", "Sculpture"],
  ["lace_umbrella", "Lace umbrella"],
  ["jewelry_box", "Jewelry box"],
  ["faux_fur_leg_warmer", "Faux fur leg warmer"],
];

const elements = {
  form: $("#batchForm"),
  input: $("#skuInput"),
  count: $("#skuCount"),
  extract: $("#extractButton"),
  clear: $("#clearButton"),
  queueSection: $("#queueSection"),
  queueList: $("#queueList"),
  queueSummary: $("#queueSummary"),
  progress: $("#progressBar"),
  resultsSection: $("#resultsSection"),
  resultsGrid: $("#resultsGrid"),
  openAllProducts: $("#openAllProductsButton"),
  batchTabs: $("#batchTabs"),
  generateBatchDescriptions: $("#generateBatchDescriptionsButton"),
  openDotbBatch: $("#openDotbBatchButton"),
  renameBatch: $("#renameBatchButton"),
  historyList: $("#historyList"),
  historyEmpty: $("#historyEmpty"),
  historyEmptyMessage: $("#historyEmptyMessage"),
  openSelectedProducts: $("#openSelectedProductsButton"),
  moveToBatchSelect: $("#moveToBatchSelect"),
  moveToBatchButton: $("#moveToBatchButton"),
  deleteActiveBatch: $("#deleteActiveBatchButton"),
  deleteAllHistory: $("#deleteAllHistoryButton"),
  savedCount: $("#savedCount"),
  currentBatchLabel: $("#currentBatchLabel"),
  currentBatchCount: $("#currentBatchCount"),
  startNextBatch: $("#startNextBatchButton"),
  finishCurrentBatch: $("#finishCurrentBatchButton"),
  imagePreview: $("#imagePreview"),
  closeImagePreview: $("#closeImagePreview"),
  previewImage: $("#previewImage"),
  previewImageCount: $("#previewImageCount"),
  previousPreviewImage: $("#previousPreviewImage"),
  nextPreviewImage: $("#nextPreviewImage"),
  toast: $("#toast"),
  temuExtensionNotice: $("#temuExtensionNotice"),
  manualPriceForm: $("#manualPriceForm"),
  manualPriceCategory: $("#manualPriceCategory"),
  manualPriceCategoryOptions: $("#manualPriceCategoryOptions"),
  manualPriceInput: $("#manualPriceInput"),
  manualPriceButton: $("#manualPriceButton"),
  manualPriceResult: $("#manualPriceResult"),
  imageGenerateModal: $("#imageGenerateModal"),
  closeImageGenerate: $("#closeImageGenerate"),
  imagePrompt: $("#imagePrompt"),
  imagePromptStrength: $("#imagePromptStrength"),
  imagePromptStrengthValue: $("#imagePromptStrengthValue"),
  imageNegativePrompt: $("#imageNegativePrompt"),
  imageSourcePanel: $("#imageSourcePanel"),
  imageSourcePreview: $("#imageSourcePreview"),
  imageSourceThumbs: $("#imageSourceThumbs"),
  imagePromptPreset: $("#imagePromptPreset"),
  imagePresetHint: $("#imagePresetHint"),
  imageOptions: $("#imageOptions"),
  imageReferenceInput: $("#imageReferenceInput"),
  imageReferencePreview: $("#imageReferencePreview"),
  imageReferencePreviewImg: $("#imageReferencePreviewImg"),
  clearImageReference: $("#clearImageReference"),
  imageGuideInput: $("#imageGuideInput"),
  imageGuidePreview: $("#imageGuidePreview"),
  imageGuidePreviewImg: $("#imageGuidePreviewImg"),
  clearImageGuide: $("#clearImageGuide"),
  imageExampleInput: $("#imageExampleInput"),
  imageExamplePreview: $("#imageExamplePreview"),
  imageExamplePreviewImg: $("#imageExamplePreviewImg"),
  clearImageExample: $("#clearImageExample"),
  imageConversationLog: $("#imageConversationLog"),
  generateImageButton: $("#generateImageButton"),
  retryImageGeneration: $("#retryImageGeneration"),
  resetImageConversation: $("#resetImageConversation"),
  generatedImageLink: $("#generatedImageLink"),
};

let currentProductIdForImage = null;
let currentProductImageUrlForImage = null;
let currentReferencePreviewUrl = null;
let currentGuidePreviewUrl = null;
let currentExamplePreviewUrl = null;
let currentGeneratedImageBlob = null;
let currentGeneratedImageUrl = null;
let lastImageRequest = null;

const imagePromptPresets = {
  mannequinFront: `Mannequin front
Place the selected dress on the plastic mannequin using the attached background. Do not add, remove, zoom, crop, resize, blur, or change anything in the background. Keep the same image size, framing, lighting, and perspective. The dress must look natural and realistic. Make sure the hem is not touching the ground or the floor`,
  sideView: `Side view
Create a right-side view of the same mannequin wearing the selected dress in the same room. Keep the background, image size, framing, lighting, and perspective unchanged. Do not zoom, crop, resize, or alter the background. Make the mannequin literally facing right side. Use the optional guide image only for the correct dress length. Do not use the guide image as the background.`,
  backView: `Back view
Create a back view of the same mannequin wearing the selected dress in the same room. Keep the background, image size, framing, lighting, and perspective unchanged. Do not zoom, crop, resize, or alter the background. Use the optional guide image only for the correct dress length. Do not use the guide image as the background.`,
  modelReplace: `Replace the clothes of the girl with the selected dress without adding or changing anything in the background. The picture must look natural and realistic. Do not change or modify the shoes of the model. Do not change the hair. Do not add anything else in the background. Only replace the clothes of the girl.`,
  floorFront: `Floor front
Place the selected dress completely flat on the floor with the front facing up. The chest area must lie flat and smooth, with no bumps, raised areas, mannequin shape, or body form underneath the dress. Use the attached background exactly as it is. Do not spread the dress. Do not add, remove, zoom, crop, resize, blur, or change anything in the background. Make the dress a bit skinny and not too fat. Use the optional guide image only for dress width, length, and positioning. Do not use the guide image as the background.`,
  floorBack: `Floor back
Place the selected dress flat on the floor, back facing up. Use the attached background exactly as it is. Do not add, remove, zoom, crop, resize, blur, or change anything in the background. Use the optional guide image only for dress length and positioning. Keep lace and back details accurate. Do not use the guide image as the background; use the main attached background image only.`
};

window.addEventListener("product-extractor:temu-extension-ready", () => {
  state.temuExtensionReady = true;
  elements.temuExtensionNotice?.classList.add("connected");
});

window.addEventListener("product-extractor:dotb-extension-ready", () => {
  state.dotbExtensionReady = true;
});

window.addEventListener("product-extractor:dotb-product-deleted", async (event) => {
  const productId = Number(event.detail?.productId || 0);
  const sku = String(event.detail?.sku || "").trim();
  if (!productId) return;
  state.products.delete(productId);
  state.selectedHistoryIds.delete(productId);
  elements.resultsGrid
    ?.querySelector(`[data-product-id="${productId}"]`)
    ?.remove();
  if (elements.resultsGrid && !elements.resultsGrid.children.length) {
    elements.resultsSection.hidden = true;
  }
  await loadHistory();
  showToast(`${sku || "Product"} was saved in Dotb and deleted locally.`);
});

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function looksLikeSku(token) {
  const value = String(token || "").trim();
  return value.length >= 5
    && value.length <= 100
    && /[0-9]/.test(value)
    && /^[A-Za-z0-9._-]+$/.test(value);
}

// Lets a pasted full product link stand in for a SKU. Temu often blocks the
// automated searcher with a false "sold out" page; opening an exact link
// through the trusted Brave extension (for Temu) or directly (for SHEIN)
// sidesteps that search step entirely.
function parseProductLink(token) {
  const raw = String(token || "").trim();
  if (!/^https:\/\//i.test(raw)) return null;
  let url;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  const host = url.hostname.toLowerCase();
  const path = decodeURIComponent(url.pathname).toLowerCase();
  if (host === "temu.com" || host.endsWith(".temu.com")) {
    if (!(path.includes("goods.html") || path.includes("/goods/") || path.includes("-g-"))) {
      return null;
    }
    const goodsId = url.searchParams.get("goods_id") || path.match(/-g-(\d+)/)?.[1];
    return { store: "temu", url: raw, sku: goodsId ? `TEMU-${goodsId}` : `TEMU-${Date.now()}` };
  }
  if (host === "shein.com" || host.endsWith(".shein.com")) {
    const productId = url.searchParams.get("goods_id")
      || path.match(/-p-(\d+)/)?.[1]
      || path.match(/\/(\d{5,})\.html/)?.[1];
    return { store: "shein", url: raw, sku: productId ? `SHEIN-${productId}` : `SHEIN-${Date.now()}` };
  }
  return null;
}

function parseSkuEntries(value = elements.input.value) {
  const chunks = String(value || "")
    .split(/[\n,;]+/)
    .map((chunk) => chunk.trim())
    .filter(Boolean);
  const entries = [];
  const seen = new Set();
  for (const chunk of chunks) {
    const parts = chunk.split(/\s+/).filter(Boolean);
    if (!parts.length) continue;
    const first = parts[0];
    const rest = parts.slice(1);
    const link = parseProductLink(first);
    if (link) {
      const key = link.sku.toLowerCase();
      if (!seen.has(key)) {
        seen.add(key);
        entries.push({ sku: link.sku, notes: rest.join(" ").trim(), url: link.url, store: link.store });
        if (entries.length >= 20) return entries;
      }
      continue;
    }
    const restAreSkus = rest.length > 0 && rest.every(looksLikeSku) && looksLikeSku(first);
    const skus = restAreSkus ? [first, ...rest] : [first];
    const notes = restAreSkus ? "" : rest.join(" ").trim();
    for (const sku of skus) {
      const key = sku.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      entries.push({ sku, notes });
      if (entries.length >= 20) return entries;
    }
  }
  return entries;
}

function parseSkus(value = elements.input.value) {
  return parseSkuEntries(value).map((entry) => entry.sku);
}

function productNotes(product) {
  return String(product?.additional_details?.notes || "").trim();
}

function selectedItemType() {
  return $('input[name="itemType"]:checked')?.value || "dress";
}

function extractedSizeLabel(itemType) {
  if (itemType === "dress_m") return "Size M";
  if (itemType === "jeans") return "Size L";
  if (["dress", "skirt", "coat", "jacket"].includes(itemType)) return "Size S";
  return "";
}

function selectedStore() {
  return $('input[name="store"]:checked')?.value || "shein";
}

function updateStoreHelp() {
  if (!elements.temuExtensionNotice) return;
  elements.temuExtensionNotice.hidden = selectedStore() !== "temu";
  elements.temuExtensionNotice.classList.toggle(
    "connected",
    state.temuExtensionReady
      || document.documentElement.dataset.temuExtractorExtension === "ready",
  );
}

function productStore(product) {
  return product.additional_details?.store === "temu" ? "temu" : "shein";
}

function proxyImage(url, download = false) {
  if (!url || url.startsWith("/downloads/")) return url;
  const params = new URLSearchParams({ url });
  if (download) params.set("download", "true");
  return `/api/images/proxy?${params}`;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 3000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error || { code: "request_failed", message: "Request failed." };
    const thrown = new Error(error.message);
    Object.assign(thrown, error);
    throw thrown;
  }
  return payload;
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function extractTemuWithExtension(
  sku,
  itemType,
  refreshProductId = null,
  onProgress = null,
  batchNumber = state.currentBatch,
  notes = "",
  productUrl = null,
) {
  const requestBody = {
    sku,
    item_type: itemType,
    batch_number: batchNumber,
  };
  if (refreshProductId) requestBody.refresh_product_id = refreshProductId;
  if (notes) requestBody.notes = notes;
  if (productUrl) requestBody.product_url = productUrl;
  const job = await api("/api/products/temu-extension/jobs", {
    method: "POST",
    body: JSON.stringify(requestBody),
  });

  state.temuExtensionReady = (
    state.temuExtensionReady
    || document.documentElement.dataset.temuExtractorExtension === "ready"
  );
  if (!state.temuExtensionReady) {
    const error = new Error(
      "Install and enable the Brave extension from the app's brave-extension folder, then reload this page.",
    );
    error.code = "temu_extension_missing";
    error.retryable = true;
    error.details = {};
    throw error;
  }

  window.dispatchEvent(new CustomEvent("product-extractor:temu-start", {
    detail: { jobId: job.job_id, apiBase: window.location.origin },
  }));

  const startedAt = Date.now();
  let previousMessage = "";
  while (Date.now() - startedAt < 180000) {
    await wait(750);
    const current = await api(
      `/api/products/temu-extension/jobs/${job.job_id}`,
    );
    if (current.message && current.message !== previousMessage) {
      previousMessage = current.message;
      onProgress?.(current.message, current.phase);
    }
    if (current.status === "done") return current.product;
    if (current.status === "error") {
      const details = current.error || {};
      const error = new Error(
        details.message || current.message || "Temu extraction failed.",
      );
      Object.assign(error, details);
      throw error;
    }
  }
  const error = new Error(
    "The Brave extension took too long. Check the Temu tab, then retry.",
  );
  error.code = "temu_extension_timeout";
  error.retryable = true;
  throw error;
}

function updateInputState() {
  const skus = parseSkus();
  elements.count.textContent = `${skus.length} / 20`;
  elements.extract.textContent = skus.length > 1 ? `Extract ${skus.length} products` : "Extract product";
  elements.extract.disabled = state.running || skus.length === 0;
}

function queueRow(sku, itemType = selectedItemType(), store = selectedStore(), notes = "") {
  let row = state.queue.get(sku)?.element;
  if (row) return row;
  row = document.createElement("div");
  row.className = "queue-item";
  row.dataset.sku = sku;
  row.innerHTML = `
    <code>${escapeHtml(sku)}</code>
    <span class="queue-message">${notes ? escapeHtml(notes) : "Waiting in queue"}</span>
    <span class="queue-status">Waiting</span>
    <div class="candidate-buttons" hidden></div>`;
  elements.queueList.append(row);
  state.queue.set(sku, { status: "waiting", element: row, itemType, store, notes });
  return row;
}

function setQueueStatus(sku, status, message, error = null, itemType = null, store = null) {
  const resolvedItemType = itemType || state.queue.get(sku)?.itemType || selectedItemType();
  const resolvedStore = store || state.queue.get(sku)?.store || selectedStore();
  const row = queueRow(sku, resolvedItemType, resolvedStore);
  row.className = `queue-item ${status}`;
  $(".queue-message", row).textContent = message;
  $(".queue-status", row).textContent = status === "working" ? "Working" : status;
  const candidates = $(".candidate-buttons", row);
  candidates.replaceChildren();
  candidates.hidden = true;
  state.queue.set(sku, {
    status,
    element: row,
    error,
    itemType: resolvedItemType,
    store: resolvedStore,
    notes: state.queue.get(sku)?.notes || "",
  });

  if (error?.details?.possible_matches?.length) {
    candidates.hidden = false;
    error.details.possible_matches.slice(0, 5).forEach((candidate) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button button-ghost";
      button.textContent = candidate.title || "Possible match";
      button.title = candidate.url;
      button.addEventListener("click", () => extractOne(
        sku,
        candidate.url,
        resolvedItemType,
        resolvedStore,
      ));
      candidates.append(button);
    });
  } else if (error?.code === "temu_manual_search_required") {
    const pasteLink = document.createElement("button");
    pasteLink.type = "button";
    pasteLink.className = "button button-primary";
    pasteLink.textContent = "Paste product link";
    pasteLink.addEventListener("click", () => {
      const productUrl = window.prompt(
        "In Brave, open the exact Temu product, copy its full address, then paste it here:",
      );
      if (productUrl?.trim()) {
        extractOne(
          sku,
          productUrl.trim(),
          resolvedItemType,
          resolvedStore,
        );
      }
    });
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button button-ghost";
    retry.textContent = "Retry search";
    retry.addEventListener("click", () => extractOne(
      sku,
      null,
      resolvedItemType,
      resolvedStore,
    ));
    candidates.hidden = false;
    candidates.append(pasteLink, retry);
  } else if (error?.retryable) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button button-ghost";
    retry.textContent = "Retry";
    retry.addEventListener("click", () => extractOne(
      sku,
      null,
      resolvedItemType,
      resolvedStore,
    ));
    candidates.hidden = false;
    candidates.append(retry);
  }
  updateQueueProgress();
}

function updateQueueProgress() {
  const entries = [...state.queue.values()];
  const complete = entries.filter((item) => ["done", "error"].includes(item.status)).length;
  elements.queueSummary.textContent = `${complete} of ${entries.length} complete`;
  elements.progress.style.width = entries.length ? `${(complete / entries.length) * 100}%` : "0";
}

function measurementText(product) {
  return Object.entries(product.measurements || {}).map(([label, value]) => `${label}: ${value}`).join(" · ");
}

function measurementsMarkup(product) {
  const measurements = Object.entries(product.measurements || {});
  const allMeasurements = measurementText(product);
  return `
    <div class="measurements-head">
      <span>Measurements</span>
      <div class="measurements-head-actions">
        ${measurements.length ? `<button class="copy-all-measurements copy-value" type="button" data-copy-value="${escapeHtml(allMeasurements)}" data-copy-label="All measurements">Copy all sizes</button>` : ""}
        <button class="button button-ghost extract-measurements" type="button" title="Read measurements from the currently shown photo">📏 Extract from photo</button>
      </div>
    </div>
    <div class="measurements">
      ${measurements.map(([label, value]) => `<div class="measurement"><span>${escapeHtml(label)}</span><button class="copy-measurement copy-value" type="button" data-copy-value="${escapeHtml(value)}" data-copy-label="${escapeHtml(label)}" title="Tap to copy ${escapeHtml(label)}">${escapeHtml(value)}<small>Tap to copy</small></button></div>`).join("")}
    </div>`;
}

function renderMeasurements(card, product) {
  const section = $(".measurements-section", card);
  if (!section) return;
  section.innerHTML = measurementsMarkup(product);
  $$(".copy-value", section).forEach((button) => {
    button.addEventListener("click", () => {
      copyText(
        button.dataset.copyValue || "",
        `${button.dataset.copyLabel || "Value"} copied.`,
      );
    });
  });
  $(".extract-measurements", section).addEventListener("click", () => extractMeasurements(product.id, card));
}

function formatEuro(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return new Intl.NumberFormat("fr-FR", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
  }).format(Number(value));
}

function priceCalculatorMarkup(product) {
  const storeLabel = productStore(product) === "temu" ? "Temu" : "SHEIN";
  const price = product.additional_details?.price_calculator || {};
  const selected = price.category || "";
  const selectedLabel = priceCategoryOptions.find(([value]) => value === selected)?.[1] || "";
  const hasSourcePrice = price.source_price_eur !== null && price.source_price_eur !== undefined;
  const hasRange = price.vinted_price_min_eur !== null && price.vinted_price_max_eur !== null;
  const range = hasRange
    ? `${formatEuro(price.vinted_price_min_eur)} – ${formatEuro(price.vinted_price_max_eur)}`
    : "No range";
  const hasRecommended = price.recommended_price_eur !== null
    && price.recommended_price_eur !== undefined;
  const result = hasRecommended ? formatEuro(price.recommended_price_eur) : "No price";
  const sourcePrice = formatEuro(price.source_price_eur);
  const sourcePriceMarkup = hasSourcePrice
    ? `<button class="copy-value copy-price" type="button" data-copy-value="${escapeHtml(sourcePrice)}" data-copy-label="${storeLabel} price">${escapeHtml(sourcePrice)}<small>Tap to copy</small></button>`
    : `<strong>${escapeHtml(sourcePrice)}</strong>`;
  const vintedPriceMarkup = hasRecommended
    ? `<button class="copy-value copy-price" type="button" data-copy-value="${escapeHtml(result)}" data-copy-label="Vinted price">${escapeHtml(result)}<small>Tap to copy</small></button>`
    : `<strong>${escapeHtml(result)}</strong>`;
  const headlinePriceMarkup = hasRecommended
    ? `<button class="copy-value copy-headline-price" type="button" data-copy-value="${escapeHtml(result)}" data-copy-label="Vinted price" title="Tap to copy Vinted price">${escapeHtml(result)}<small>Tap to copy</small></button>`
    : `<strong>${escapeHtml(price.message || `Refresh to read the ${storeLabel} price.`)}</strong>`;
  const hasPurchaseBracket = price.purchase_bracket_min_eur !== null
    && price.purchase_bracket_min_eur !== undefined
    && price.purchase_bracket_max_eur !== null
    && price.purchase_bracket_max_eur !== undefined;
  const bracket = hasPurchaseBracket
    ? `${formatEuro(price.purchase_bracket_min_eur)} â€“ ${formatEuro(price.purchase_bracket_max_eur)}`
    : "";
  return `
    <section class="price-calculator" aria-label="Vinted price calculator">
      <div class="price-calculator-head">
        <div>
          <span class="price-eyebrow">Price calculator</span>
          ${headlinePriceMarkup}
        </div>
        <label class="category-combobox" data-value="${escapeHtml(selected)}">
          <span>Category</span>
          <div class="combobox">
            <input
              type="text"
              class="price-category-input"
              placeholder="Type to search category…"
              autocomplete="off"
              value="${escapeHtml(selectedLabel)}"
            >
            <div class="combobox-options" hidden></div>
          </div>
        </label>
      </div>
      <div class="price-flow">
        <div><span>${storeLabel}</span>${sourcePriceMarkup}</div>
        <i>→</i>
        <div class="${hasRange ? "price-result" : "price-result unavailable"}">
          <span>Vinted price</span>${vintedPriceMarkup}
          ${hasRange ? `<small>${hasPurchaseBracket ? `Buy bracket ${bracket} â†’ ` : ""}Vinted range ${range}</small>` : ""}
        </div>
      </div>
      ${hasRange ? "" : `<p>${escapeHtml(price.message || `The current ${storeLabel} price is unavailable.`)}</p>`}
    </section>`;
}

function currentImage(card) {
  return $(".product-main-image", card)?.dataset.original || "";
}

function productImages(product) {
  return [product?.main_image_url, ...(product?.additional_image_urls || [])].filter(Boolean);
}

function setCardImage(card, url, button) {
  const image = $(".product-main-image", card);
  image.src = proxyImage(url);
  image.dataset.original = url;
  $$(".mini-thumb", card).forEach((item) => item.classList.toggle("active", item === button));
}

function setModalSourceImage(url) {
  currentProductImageUrlForImage = url || null;
  if (!url) {
    elements.imageSourcePanel.hidden = true;
    elements.imageSourcePreview.removeAttribute("src");
    elements.imageSourceThumbs.replaceChildren();
    return;
  }
  elements.imageSourcePreview.src = proxyImage(url);
  elements.imageSourcePanel.hidden = false;
  $$(".image-source-thumbs button", elements.imageSourceThumbs).forEach((button) => {
    button.classList.toggle("active", button.dataset.imageUrl === url);
  });
}

function renderModalSourceImages(productId, selectedImageUrl = null) {
  const product = state.products.get(productId);
  const images = productImages(product);
  elements.imageSourceThumbs.replaceChildren();
  if (!images.length) {
    setModalSourceImage(selectedImageUrl);
    return;
  }
  const selectedUrl = images.includes(selectedImageUrl) ? selectedImageUrl : images[0];
  images.forEach((url, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.imageUrl = url;
    button.setAttribute("aria-label", `Use product image ${index + 1} for generation`);
    const image = document.createElement("img");
    image.src = proxyImage(url);
    image.alt = "";
    image.loading = "lazy";
    button.append(image);
    button.addEventListener("click", () => setModalSourceImage(url));
    elements.imageSourceThumbs.append(button);
  });
  setModalSourceImage(selectedUrl);
}

function renderImagePreview() {
  const total = state.previewImages.length;
  if (!total) return;
  const url = state.previewImages[state.previewIndex];
  elements.previewImage.src = proxyImage(url);
  elements.previewImageCount.textContent = `Image ${state.previewIndex + 1} of ${total}`;
  elements.previousPreviewImage.disabled = total < 2;
  elements.nextPreviewImage.disabled = total < 2;
}

function openImagePreview(images, selectedUrl) {
  if (!images.length) return;
  state.previewImages = images;
  const selectedIndex = images.indexOf(selectedUrl);
  state.previewIndex = selectedIndex >= 0 ? selectedIndex : 0;
  renderImagePreview();
  elements.imagePreview.hidden = false;
  document.body.classList.add("preview-open");
  elements.closeImagePreview.focus();
}

function closeImagePreview() {
  elements.imagePreview.hidden = true;
  elements.previewImage.removeAttribute("src");
  document.body.classList.remove("preview-open");
}

function moveImagePreview(direction) {
  const total = state.previewImages.length;
  if (total < 2) return;
  state.previewIndex = (state.previewIndex + direction + total) % total;
  renderImagePreview();
}

function renderListing(card, product) {
  const area = $(".listing-area", card);
  const english = product.english_listing;
  const french = product.french_listing;
  const isStyleToken = ["skirt", "hat", "mask", "jeans", "bag", "coat", "jacket", "plant", "organizer", "mirror", "sculpture", "curtain", "leg_warmer", "jewelry_box", "lace_umbrella", "belt"].includes(product.listing_type);
  const listingType = product.listing_type || product.additional_details?.item_type || "dress";
  const outputLabel = isStyleToken ? "Clothing style" : "Fictional brand";
  const outputValue = (
    isStyleToken && product.fictional_brand === "oldmoney"
      ? "old money"
      : isStyleToken && product.fictional_brand === "wildwest"
        ? "Wild West"
      : product.fictional_brand
  );
  if (!english || !french) {
    area.innerHTML = `<div class="listing-state">Generate bilingual listing copy when needed.</div>`;
    return;
  }
  area.innerHTML = `
    <div class="listing-grid">
      ${listingLanguage("english", "English", english, listingType)}
      ${listingLanguage("french", "Français", french, listingType)}
    </div>
    ${outputValue ? `<div class="fictional-brand"><span>${outputLabel}</span><strong>${escapeHtml(outputValue)}</strong></div>` : ""}`;
  $$(".listing-language", area).forEach((language) => {
    const languageKey = language.dataset.language || "english";
    const descriptionArea = $(".listing-description", language);
    descriptionArea.value = ensureNegotiableDescription(
      descriptionArea.value,
      listingType,
      languageKey,
    );
    $(".listing-title", language).addEventListener("blur", () => saveListingEdit(product.id, card));
    descriptionArea.addEventListener("blur", () => saveListingEdit(product.id, card));
    $(".hashtags", language).addEventListener("blur", () => saveListingEdit(product.id, card));
  });
  $$(".copy-listing", area).forEach((button) => {
    button.addEventListener("click", () => {
      const language = button.closest(".listing-language");
      const languageKey = language.dataset.language || "english";
      const title = $(".listing-title", language).value.trim();
      const description = ensureNegotiableDescription(
        $(".listing-description", language).value.trim(),
        listingType,
        languageKey,
      );
      const hashtags = $(".hashtags", language).textContent.trim();
      const brand = outputValue ? `${outputLabel}: ${outputValue}` : "";
      copyText([title, description, hashtags, brand].filter(Boolean).join("\n\n"), "Full listing copied.");
    });
  });
}

function listingNeedsNegotiable(listingType) {
  return listingType === "dress" || listingType === "dress_m" || listingType === "skirt" || listingType === "hat" || listingType === "beanie" || listingType === "mask" || listingType === "jeans" || listingType === "long_boots" || listingType === "heels" || listingType === "coat" || listingType === "jacket" || listingType === "bag" || listingType === "plant" || listingType === "shelf" || listingType === "organizer" || listingType === "leg_warmer" || listingType === "lace_umbrella";
}

function cleanListingTitle(title, listingType) {
  let text = String(title || "").trim();
  if (!text || !["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "bag", "plant", "mask", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"].includes(listingType)) return text;
  text = text.replace(/([A-Za-zÀ-ÿ])(?=Taille\s*:)/gi, "$1 ");
  text = text.replace(/\b(?:size|taille)\s*:?\s*(?:XXS|XS|S|M|L|XL|XXL|XXXL|[2-5]XL|\d{2,3})\b/gi, "");
  text = text.replace(/\s*(?:,?\s*with a polished silhouette|,?\s*with a flowing full-length shape|,?\s*with a refined dress shape|,?\s*with an elegant style detail|,?\s*with refined cut details and a chic feminine finish|,?\s*with elegant details and a flattering fitted look|,?\s*avec coupe raffinée et finition chic|,?\s*avec détails élégants et coupe flatteuse|,?\s*for evening and occasion outfits|,?\s*for occasion outfit styling)\s*/gi, " ");
  text = text.replace(/\s*,?\s*avec coupe raffin\S*e et finition chic\s*/gi, " ");
  text = text.replace(/\s*,?\s*avec d\S*tails \S*l\S*gants et coupe flatteuse\s*/gi, " ");
  text = text.replace(/\bstyle\s+elegant\b/gi, "style élégant");
  text = text.replace(/\s+/g, " ").replace(/\s+,/g, ",").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  const isFrench = /\brobe\b|\bjupe\b|\bjean\b|\bbottes?\b|\bsandales?\b|\bescarpins?\b|\btalons?\b|\bmanteau\b|\bveste\b|\btrench\b|\bsac\b|\bmasque\b|\bétagère\b|\betagere\b|\blampe\b|\bmiroir\b|\bsculpture\b|\bstatue\b|\bstatuette\b|\btapis\b|\bcoussin\b|\brideau\b|\bpanneau\b|\bcravate\b|\bjambi[eè]res?\b|\bcoffret\b|\bombrelle\b|\bceintures?\b|\blustres?\b|\bsuspensions?\b|\bplafonniers?\b|\bbonnets?\b|\btaille\b|\bélégant\b|\belegant\b/i.test(text)
    && !/\bdress\b|\bskirt\b|\bjeans\b|\bboots\b|\bheels\b|\bsandals\b|\bcoat\b|\bjacket\b|\bbag\b|\bhandbag\b|\bmask\b|\bshel(?:f|ves)\b|\blamps?\b|\bmirrors?\b|\bsculptures?\b|\bstatues?\b|\bstatuettes?\b|\bcarpets?\b|\brugs?\b|\bcushions?\b|\bpillows?\b|\bcurtains?\b|\bdrapes?\b|\bneckties?\b|\bties?\b|\bleg\s*warmers?\b|\bjewelry\s*box(?:es)?\b|\bparasols?\b|\bumbrellas?\b|\bbelts?\b|\bchandeliers?\b|\bpendant\s*lights?\b|\bceiling\s*lights?\b|\bbeanies?\b/i.test(text);
  if (isFrench) {
    const colourMap = [
      ["dark red", "rouge fonc?"], ["dark green", "vert fonc?"],
      ["navy blue", "bleu marine"], ["sky blue", "bleu ciel"],
      ["soft pink", "rose poudr?"], ["light pink", "rose clair"],
      ["cream white", "blanc cr?me"], ["off white", "blanc cass?"],
      ["multicolour", "multicolore"], ["multicolor", "multicolore"],
      ["black", "noir"], ["white", "blanc"], ["cream", "cr?me"],
      ["brown", "marron"], ["red", "rouge"], ["pink", "rose"],
      ["blue", "bleu"], ["green", "vert"], ["yellow", "jaune"],
      ["gold", "dor?"], ["silver", "argent?"], ["grey", "gris"],
      ["gray", "gris"], ["purple", "violet"], ["burgundy", "bordeaux"],
      ["navy", "bleu marine"], ["khaki", "kaki"],
    ];
    for (const [english, french] of colourMap) {
      text = text.replace(new RegExp(`\b${english}\b`, "gi"), french);
    }
  }
  if (listingType === "jacket") {
    text = text.replace(/\b(?:faux\s*leather|fauxleather|simili\s*cuir|leather|cuir|suede|daim|wool|laine)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  }
  if (listingType === "organizer") {
    text = text.replace(/\b(?:metal|plastic|wooden|wood|wire|steel|iron|metallique|plastique|bois|acier|fer)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  }
  if (listingType === "mirror") {
    text = text.replace(/\b(?:wood|wooden|walnut|resin|metal|glass|rattan|bois|noyer|resine|metallique|verre|rotin)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  }
  if (listingType === "sculpture") {
    text = text.replace(/\b(?:resin|ceramic|wood|wooden|metal|plaster|stone|resine|ceramique|bois|metallique|platre|pierre)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  }
  if (listingType === "curtain") {
    text = text.replace(/\b(?:linen|cotton|polyester|velvet|silk|voile|lin|coton|velours|soie)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  }
  if (listingType === "leg_warmer") {
    text = text.replace(/\b(?:faux\s*fur|fur|polyester|acrylic|wool|fausse\s*fourrure|fourrure|acrylique|laine)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  }
  if (listingType === "jewelry_box") {
    text = text.replace(/\b(?:leather|faux\s*leather|velvet|suede|cuir|simili\s*cuir|velours|daim)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  }
  if (listingType === "lace_umbrella") {
    text = text.replace(/\b(?:satin|polyester|cotton|nylon|coton)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  }
  if (listingType === "belt") {
    text = text.replace(/\b(?:faux\s*leather|fauxleather|simili\s*cuir|leather|cuir|suede|daim|metal|m[ée]tal(?:lique)?)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  }
  if (listingType === "mask") {
    text = text.replace(/\bhalloween\s+costume\s+style\b/gi, "");
    text = text.replace(/\b(?:faux\s*leather|fauxleather|simili\s*cuir|leather|cuir)\b/gi, "");
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
    const suffix = isFrench ? "taille unique" : "one size fits all";
    return `${text.slice(0, 100 - suffix.length - 1).replace(/[,;:\s-]+$/g, "")} ${suffix}`.trim();
  }
  if (listingType === "shelf") {
    text = text.replace(/\bvintage[- ]style\b/gi, ", style vintage");
    text = text.replace(/\b(?:wood|wooden|mdf|laminate|metal|bois|metallique)\b/gi, "");
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|one\s+size|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "").replace(/\s+,/g, ",");
    const suffix = isFrench ? "taille unique" : "one size";
    return `${text.slice(0, 100 - suffix.length - 2).replace(/[,;:\s-]+$/g, "")}, ${suffix}`.trim();
  }
  if (listingType === "lamp") {
    text = text.replace(/\bvintage[- ]style\b/gi, ", style vintage");
    text = text.replace(/\bmodern[- ]style\b/gi, ", style modern");
    text = text.replace(/\b(?:glass|wood|wooden|resin|metal|ceramic|acrylic|verre|bois|resine|metallique|ceramique|acrylique)\b/gi, "");
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|one\s+size|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
    const suffix = isFrench ? "taille unique" : "one size fits all";
    return `${text.slice(0, 100 - suffix.length - 1).replace(/[,;:\s-]+$/g, "")} ${suffix}`.trim();
  }
  if (listingType === "chandelier") {
    text = text.replace(/\bvintage[- ]style\b/gi, ", style vintage");
    text = text.replace(/\bmodern[- ]style\b/gi, ", style modern");
    text = text.replace(/\bdecorative[- ]style\b/gi, ", style decorative");
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|one\s+size|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
    const suffix = isFrench ? "taille unique" : "one size fits all";
    return `${text.slice(0, 100 - suffix.length - 1).replace(/[,;:\s-]+$/g, "")} ${suffix}`.trim();
  }
  if (listingType === "beanie") {
    text = text.replace(/\by2k[- ]style\b/gi, ", style y2k");
    text = text.replace(/\bcute[- ]style\b/gi, ", style y2k");
    text = text.replace(/\b(?:wool|acrylic|polyester|fleece|laine|acrylique|polaire)\b/gi, "");
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|one\s+size|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
    const suffix = isFrench ? "taille unique" : "one size fits all";
    return `${text.slice(0, 100 - suffix.length - 1).replace(/[,;:\s-]+$/g, "")} ${suffix}`.trim();
  }
  if (listingType === "carpet") {
    text = text.replace(/\bvintage[- ]style\b/gi, ", style vintage");
    text = text.replace(/\bdecorative[- ]style\b/gi, ", style decorative");
    text = text.replace(/\b(?:wool|cotton|polyester|jute|nylon|polypropylene|laine|coton|polyester|jute|nylon|polypropylene)\b/gi, "");
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|one\s+size|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
    const suffix = isFrench ? "taille unique" : "one size fits all";
    return `${text.slice(0, 100 - suffix.length - 1).replace(/[,;:\s-]+$/g, "")} ${suffix}`.trim();
  }
  if (listingType === "cushion") {
    text = text.replace(/\bdecorative[- ]style\b/gi, ", style decorative");
    text = text.replace(/\bboho[- ]style\b/gi, ", style boho");
    text = text.replace(/\b(?:linen|cotton|velvet|polyester|silk|suede|leather|lin|coton|velours|polyester|soie|daim|cuir)\b/gi, "");
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|one\s+size|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
    const suffix = isFrench ? "taille unique" : "one size fits all";
    return `${text.slice(0, 100 - suffix.length - 1).replace(/[,;:\s-]+$/g, "")} ${suffix}`.trim();
  }
  if (listingType === "bag" || listingType === "plant") {
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\b(?:faux\s*leather|fauxleather|simili\s*cuir|leather|cuir|suede|daim)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
    return text.slice(0, 100).replace(/[,;:\s-]+$/g, "").trim();
  }
  if (listingType === "necktie") {
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\b(?:silk|polyester|cotton|wool|nylon|soie|coton|laine)\b/gi, "").trim();
    text = text.replace(/\b(?:size|taille)\s*(?:XXS|XS|S|M|L|XL|XXL|XXXL|[2-5]XL|\d{2,3})\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
    return text.slice(0, 100).replace(/[,;:\s-]+$/g, "").trim();
  }
  const suffix = listingType === "jeans" || listingType === "jewelry_box"
    ? (isFrench ? "taille L" : "size L")
    : listingType === "long_boots" || listingType === "heels"
      ? (isFrench ? "taille 38" : "size 38")
      : listingType === "dress_m"
        ? (isFrench ? "taille M" : "size M")
        : (isFrench ? "taille S" : "size S");
  text = text.replace(/\bwith an [SL]-size belt\b/gi, "");
  text = text.replace(/\bavec une ceinture taille [SL]\b/gi, "");
  text = text.replace(/\bin an?\s+[\w\s]+?\s+style\b/gi, "");
  text = text.replace(/\bdans un style\s+[\w\s]+/gi, "");
  text = text.replace(/\b(?:size|taille)\s*(?:[SML]|38)\b/gi, "").trim();
  return `${text.slice(0, 100 - suffix.length - 1).replace(/[,;:\s-]+$/g, "")} ${suffix}`.trim();
}

function cleanListingDescription(description, listingType, language, fallbackTitle) {
  const text = String(description || "").trim();
  if (!text) return text;
  const blocks = text.replace(/\r/g, "").split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  if (!blocks.length) return text;
  if (["english", "french"].includes(language) && ["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "bag", "plant", "mask", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"].includes(listingType)) {
    blocks[0] = cleanListingTitle(blocks[0] || fallbackTitle, listingType);
    while (blocks.length > 1 && blocks[0].toLowerCase() === blocks[1].toLowerCase()) {
      blocks.splice(1, 1);
    }
  }
  return blocks.join("\n\n");
}

function ensureNegotiableDescription(description, listingType, language = "english") {
  let text = String(description || "").trim();
  if (!text || !listingNeedsNegotiable(listingType)) return text;
  const englishLine = "prices are negotiable :)";
  const frenchLine = "prix négociable :)";
  const wantedLine = language === "french" ? frenchLine : englishLine;
  const englishPattern = /\bprices?\s+are\s+negotiable\s*:\)/i;
  const frenchPattern = /\bprix\s+n[ée]gociable\s*:\)/i;
  if (language === "french") {
    if (frenchPattern.test(text)) return text.replace(englishPattern, frenchLine);
    text = text.replace(englishPattern, "").replace(/\n{3,}/g, "\n\n").trim();
  } else {
    if (englishPattern.test(text)) return text.replace(frenchPattern, englishLine);
    text = text.replace(frenchPattern, "").replace(/\n{3,}/g, "\n\n").trim();
  }
  const conditionLines = language === "french"
    ? ["Parfait \u00e9tat.", "\u00c9tat parfait"]
    : ["Perfect condition."];
  for (const condition of conditionLines) {
    const index = text.lastIndexOf(condition);
    if (index >= 0) {
      const before = text.slice(0, index).trim();
      const after = text.slice(index).trim();
      return [before, wantedLine, after].filter(Boolean).join("\n\n");
    }
  }
  return `${text}\n\n${wantedLine}`;
}

function listingLanguage(key, label, listing, listingType = "dress") {
  const cleanTitle = cleanListingTitle(listing.title, listingType);
  const cleanDescription = cleanListingDescription(
    listing.description,
    listingType,
    key,
    cleanTitle,
  );
  return `
    <section class="listing-language" data-language="${key}">
      <div class="listing-head">
        <strong>${label}</strong>
        <button class="button button-ghost copy-listing" type="button">Copy full</button>
      </div>
      <textarea class="listing-title" rows="2" maxlength="100" aria-label="${label} title">${escapeHtml(cleanTitle)}</textarea>
      <textarea class="listing-description" rows="7" aria-label="${label} description">${escapeHtml(cleanDescription)}</textarea>
      <div class="hashtags" contenteditable="true" role="textbox" aria-label="${label} hashtags">${escapeHtml((listing.hashtags || []).join(" "))}</div>
    </section>`;
}

function productCard(product) {
  const card = document.createElement("article");
  card.className = "product-card";
  card.dataset.productId = product.id;
  card.dataset.batchNumber = product.batch_number || 1;
  const facts = [product.colour, product.material, product.category].filter(Boolean);
  const measurements = Object.entries(product.measurements || {});
  const itemType = product.listing_type || product.additional_details?.item_type || "dress";
  const savedSkirtTitles = [
    product.english_listing?.title,
    product.french_listing?.title,
  ].filter(Boolean).join(" ");
  const skirtLength = /\bmidi\b/i.test(savedSkirtTitles) ? "midi" : "long";
  const storeLabel = productStore(product) === "temu" ? "Temu" : "SHEIN";
  card.innerHTML = `
    <div class="product-summary">
      <div class="product-media">
        <img class="product-main-image" alt="${escapeHtml(product.title || product.sku)}">
        <div class="mini-gallery"></div>
      </div>
      <div class="product-content">
        <div class="product-head">
          <h3>${escapeHtml(product.title || "Untitled product")}</h3>
          <div class="product-head-tools">
            <button class="sku copy-value" type="button" data-copy-value="${escapeHtml(product.sku)}" data-copy-label="SKU" title="Tap to copy SKU">${escapeHtml(product.sku)}</button>
            <button class="delete-product-button" type="button" title="Delete this product" aria-label="Delete ${escapeHtml(product.sku)}">Delete</button>
          </div>
        </div>
        <div class="facts"><span class="fact">Batch ${product.batch_number || 1}</span>${facts.map((fact) => `<span class="fact">${escapeHtml(fact)}</span>`).join("")}${measurements.length && extractedSizeLabel(itemType) ? `<button class="fact copy-fact copy-value" type="button" data-copy-value="${extractedSizeLabel(itemType)}" data-copy-label="Size">${extractedSizeLabel(itemType)}</button>` : ""}</div>
        <div class="measurements-section">${measurementsMarkup(product)}</div>
        <label class="sku-notes">
          <span>SKU notes</span>
          <textarea class="sku-notes-input" rows="2" maxlength="400" placeholder="Size 38 · 4 pictures not 2 · grey version">${escapeHtml(productNotes(product))}</textarea>
        </label>
        ${priceCalculatorMarkup(product)}
        <fieldset class="listing-type-picker">
          <legend>Description type</legend>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="dress" ${itemType === "dress" ? "checked" : ""}>
            <span>Dress</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="dress_m" ${itemType === "dress_m" ? "checked" : ""}>
            <span>Dress M</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="skirt" ${itemType === "skirt" ? "checked" : ""}>
            <span>Skirt</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="jeans" ${itemType === "jeans" ? "checked" : ""}>
            <span>Jeans</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="long_boots" ${itemType === "long_boots" ? "checked" : ""}>
            <span>Long boots</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="heels" ${itemType === "heels" ? "checked" : ""}>
            <span>Heels</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="coat" ${itemType === "coat" ? "checked" : ""}>
            <span>Coat</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="jacket" ${itemType === "jacket" ? "checked" : ""}>
            <span>Jacket</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="earrings" ${itemType === "earrings" ? "checked" : ""}>
            <span>Earrings</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="bag" ${itemType === "bag" ? "checked" : ""}>
            <span>Bag</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="hat" ${itemType === "hat" ? "checked" : ""}>
            <span>Hat</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="beanie" ${itemType === "beanie" ? "checked" : ""}>
            <span>Beanie</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="mask" ${itemType === "mask" ? "checked" : ""}>
            <span>Mask</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="plant" ${itemType === "plant" ? "checked" : ""}>
            <span>Plant</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="shelf" ${itemType === "shelf" ? "checked" : ""}>
            <span>Shelf</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="organizer" ${itemType === "organizer" ? "checked" : ""}>
            <span>Organizer</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="lamp" ${itemType === "lamp" ? "checked" : ""}>
            <span>Lamp</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="mirror" ${itemType === "mirror" ? "checked" : ""}>
            <span>Mirror</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="sculpture" ${itemType === "sculpture" ? "checked" : ""}>
            <span>Sculpture</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="carpet" ${itemType === "carpet" ? "checked" : ""}>
            <span>Carpet</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="cushion" ${itemType === "cushion" ? "checked" : ""}>
            <span>Cushion</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="curtain" ${itemType === "curtain" ? "checked" : ""}>
            <span>Curtain</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="necktie" ${itemType === "necktie" ? "checked" : ""}>
            <span>Necktie</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="leg_warmer" ${itemType === "leg_warmer" ? "checked" : ""}>
            <span>Leg warmer</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="jewelry_box" ${itemType === "jewelry_box" ? "checked" : ""}>
            <span>Jewelry box</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="lace_umbrella" ${itemType === "lace_umbrella" ? "checked" : ""}>
            <span>Lace umbrella</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="belt" ${itemType === "belt" ? "checked" : ""}>
            <span>Belt</span>
          </label>
          <label>
            <input type="radio" name="listing-type-${product.id}" value="chandelier" ${itemType === "chandelier" ? "checked" : ""}>
            <span>Chandelier</span>
          </label>
        </fieldset>
        <fieldset class="listing-type-picker skirt-length-picker" ${itemType === "skirt" ? "" : "hidden"}>
          <legend>Skirt length</legend>
          <label>
            <input type="radio" name="skirt-length-${product.id}" value="midi" ${skirtLength === "midi" ? "checked" : ""}>
            <span>Midi</span>
          </label>
          <label>
            <input type="radio" name="skirt-length-${product.id}" value="long" ${skirtLength === "long" ? "checked" : ""}>
            <span>Long</span>
          </label>
        </fieldset>
        <div class="product-actions">
          <button class="button button-primary generate" type="button">${product.english_listing ? "Regenerate" : "Generate listing"}</button>
          <button class="button button-ghost undo-listing" type="button" title="Go back to the previous description" ${product.has_previous_listing ? "" : "disabled"}>↩ Undo</button>
          <button class="button button-ghost copy-image" type="button">Copy image</button>
          <button class="button button-ghost refresh-product" type="button">Refresh</button>
          <a class="button button-ghost" href="${escapeHtml(product.product_url)}" target="_blank" rel="noopener noreferrer">${storeLabel} ↗</a>
          <a class="button button-vinteo" href="https://vinteo.xyz/upload" target="_blank" rel="noopener noreferrer">Open in Vinteo ↗</a>
        </div>
      </div>
    </div>
    <div class="listing-area"></div>`;

  const images = productImages(product);
  const gallery = $(".mini-gallery", card);
  images.forEach((url, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `mini-thumb${index === 0 ? " active" : ""}`;
    button.setAttribute("aria-label", `Show product image ${index + 1}`);
    const image = document.createElement("img");
    image.src = proxyImage(url);
    image.alt = "";
    image.loading = "lazy";
    button.append(image);
    button.addEventListener("click", () => setCardImage(card, url, button));
    gallery.append(button);
  });
  if (images[0]) {
    setCardImage(card, images[0], $(".mini-thumb", card));
    const mainImage = $(".product-main-image", card);
    mainImage.tabIndex = 0;
    mainImage.setAttribute("role", "button");
    mainImage.setAttribute("aria-label", "Open full-size product image");
    mainImage.addEventListener("click", () => openImagePreview(images, currentImage(card)));
    mainImage.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openImagePreview(images, currentImage(card));
      }
    });
  }

  $(".generate", card).addEventListener("click", () => generateListing(product.id, card));
  $(".undo-listing", card).addEventListener("click", () => undoListing(product.id, card));
  $(".extract-measurements", card).addEventListener("click", () => extractMeasurements(product.id, card));
  $$('input[name^="listing-type-"]', card).forEach((input) => {
    input.addEventListener("change", () => {
      $(".skirt-length-picker", card).hidden = input.value !== "skirt";
    });
  });
  initCategoryCombobox(card, product);
  $(".sku-notes-input", card)?.addEventListener("blur", async (event) => {
    const notes = event.target.value.trim();
    if (notes === productNotes(product)) return;
    try {
      const updated = await api(`/api/products/${product.id}/notes`, {
        method: "PATCH",
        body: JSON.stringify({ notes }),
      });
      state.products.set(updated.id, updated);
      const historyItem = state.historyItems.find((item) => item.id === updated.id);
      if (historyItem) historyItem.additional_details = updated.additional_details;
      showToast("SKU notes saved.");
    } catch (error) {
      showToast(error.message || "Notes could not be saved.");
    }
  });
  $$(".copy-value", card).forEach((button) => {
    button.addEventListener("click", () => {
      copyText(
        button.dataset.copyValue || "",
        `${button.dataset.copyLabel || "Value"} copied.`,
      );
    });
  });
  $(".copy-image", card).addEventListener("click", (event) => {
    copyImage(currentImage(card), event.currentTarget);
  });
  $(".refresh-product", card).addEventListener("click", () => refreshProduct(product.id, card));
  $(".delete-product-button", card).addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (!window.confirm(
      `Permanently delete ${product.sku} and its saved screenshots?`,
    )) return;
    button.disabled = true;
    button.textContent = "Deleting…";
    try {
      await api(`/api/products/${product.id}`, { method: "DELETE" });
      state.products.delete(product.id);
      state.selectedHistoryIds.delete(product.id);
      card.remove();
      if (!elements.resultsGrid.children.length) {
        elements.resultsSection.hidden = true;
      }
      await loadHistory();
      showToast(`${product.sku} and its saved screenshots were deleted.`);
    } catch (error) {
      button.disabled = false;
      button.textContent = "Delete";
      showToast(error.message || "Product could not be deleted.");
    }
  });
  renderListing(card, product);
  return card;
}

function showProduct(product, replaceCard = null) {
  state.products.set(product.id, product);
  elements.resultsSection.hidden = false;
  const card = productCard(product);
  if (replaceCard) {
    replaceCard.replaceWith(card);
  } else {
    const existing = elements.resultsGrid.querySelector(`[data-product-id="${product.id}"]`);
    if (existing) existing.replaceWith(card);
    else elements.resultsGrid.prepend(card);
  }
}

async function extractOne(
  sku,
  candidateUrl = null,
  itemType = selectedItemType(),
  store = selectedStore(),
  batchNumber = state.currentBatch,
  notes = null,
) {
  const savedNotes = notes ?? state.queue.get(sku)?.notes ?? "";
  const storeLabel = store === "temu" ? "Temu" : "SHEIN";
  elements.queueSection.hidden = false;
  setQueueStatus(
    sku,
    "working",
    candidateUrl ? "Opening pasted product link..." : `Searching ${storeLabel} as ${itemType}...`,
    null,
    itemType,
    store,
  );
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180000);
  try {
    let product;
    if (store === "temu") {
      // Always go through the trusted Brave extension for Temu, even with a
      // pasted link: the automated (Playwright) browser is what Temu
      // sometimes blocks with a false "sold out" page. The extension reuses
      // the user's real, signed-in Brave session instead.
      product = await extractTemuWithExtension(
        sku,
        itemType,
        null,
        (message) => setQueueStatus(
          sku,
          "working",
          message,
          null,
          itemType,
          store,
        ),
        batchNumber,
        savedNotes,
        candidateUrl,
      );
    } else {
      const body = {
        sku,
        item_type: itemType,
        store,
        batch_number: batchNumber,
      };
      if (candidateUrl) body.candidate_url = candidateUrl;
      if (savedNotes) body.notes = savedNotes;
      product = await api("/api/products/extract", {
        method: "POST",
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    }
    setQueueStatus(
      sku,
      "done",
      product.title || "Product extracted",
      null,
      itemType,
      store,
    );
    showProduct(product);
    await loadHistory();
    return product;
  } catch (error) {
    const message = error.name === "AbortError"
      ? "Extraction timed out. Please retry."
      : error.message || "Extraction failed";
    setQueueStatus(sku, "error", message, error, itemType, store);
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

async function runBatch(entries, itemType, store) {
  const skus = entries.map((entry) => entry.sku);
  const notesBySku = Object.fromEntries(
    entries.map((entry) => [entry.sku, entry.notes || ""]),
  );
  const urlBySku = Object.fromEntries(
    entries.map((entry) => [entry.sku, entry.url || null]),
  );
  const storeBySku = Object.fromEntries(
    entries.map((entry) => [entry.sku, entry.store || store]),
  );
  const batchNumber = state.currentBatch;
  state.activeBatch = batchNumber;
  state.running = true;
  elements.extract.disabled = true;
  updateBatchControls();
  elements.queueSection.hidden = false;
  elements.queueList.replaceChildren();
  state.queue.clear();
  skus.forEach((sku) => queueRow(sku, itemType, storeBySku[sku], notesBySku[sku]));
  updateQueueProgress();
  for (const sku of skus) {
    await extractOne(sku, urlBySku[sku], itemType, storeBySku[sku], batchNumber, notesBySku[sku]);
  }
  state.running = false;
  updateInputState();
  updateBatchControls();
  const succeeded = [...state.queue.values()].filter((item) => item.status === "done").length;
  showToast(`${succeeded} of ${skus.length} products extracted into Batch ${batchNumber}.`);
}

async function generateListing(productId, card) {
  const button = $(".generate", card);
  const listingType = $('input[name^="listing-type-"]:checked', card)?.value || "dress";
  const skirtLength = listingType === "skirt"
    ? $('input[name^="skirt-length-"]:checked', card)?.value || "long"
    : null;
  button.disabled = true;
  button.textContent = "Generating...";
  try {
    const product = await api(`/api/products/${productId}/generate-description`, {
      method: "POST",
      body: JSON.stringify({
        listing_type: listingType,
        skirt_length: skirtLength,
        selected_image_url: currentImage(card) || null,
      }),
    });
    state.products.set(product.id, product);
    renderListing(card, product);
    button.textContent = "Regenerate";
    const undoButton = $(".undo-listing", card);
    if (undoButton) undoButton.disabled = !product.has_previous_listing;
    showToast("Bilingual listing generated.");
    await loadHistory();
  } catch (error) {
    button.textContent = "Generate listing";
    showToast(error.message || "Listing generation failed.");
  } finally {
    button.disabled = false;
  }
}

async function undoListing(productId, card) {
  const button = $(".undo-listing", card);
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "Restoring…";
  try {
    const product = await api(`/api/products/${productId}/restore-previous-listing`, {
      method: "POST",
    });
    state.products.set(product.id, product);
    renderListing(card, product);
    showToast("Restored the previous description.");
    await loadHistory();
  } catch (error) {
    showToast(error.message || "Nothing to go back to.");
  } finally {
    button.textContent = originalLabel;
    const current = state.products.get(productId);
    button.disabled = !(current && current.has_previous_listing);
  }
}

async function extractMeasurements(productId, card) {
  const imageUrl = currentImage(card);
  if (!imageUrl) {
    showToast("Select a product photo first.");
    return;
  }
  const button = $(".extract-measurements", card);
  button.disabled = true;
  const originalLabel = button.textContent;
  button.textContent = "Reading photo…";
  try {
    const product = await api(`/api/products/${productId}/extract-measurements`, {
      method: "POST",
      body: JSON.stringify({ image_url: imageUrl }),
    });
    state.products.set(product.id, product);
    renderMeasurements(card, product);
    showToast("Measurements read from the photo.");
    await loadHistory();
  } catch (error) {
    showToast(error.message || "Could not read measurements from that photo.");
  } finally {
    const stillThere = $(".extract-measurements", card);
    if (stillThere) {
      stillThere.textContent = originalLabel;
      stillThere.disabled = false;
    }
  }
}

function currentListingFields(area) {
  return {
    title: $(".listing-title", area).value.trim(),
    description: $(".listing-description", area).value.trim(),
    hashtags: $(".hashtags", area).textContent.trim().split(/\s+/).filter(Boolean),
  };
}

function listingFieldsEqual(a, b) {
  if (!a || !b) return false;
  return (
    a.title === b.title
    && a.description === b.description
    && (a.hashtags || []).join(" ") === (b.hashtags || []).join(" ")
  );
}

async function saveListingEdit(productId, card) {
  const englishArea = $('.listing-language[data-language="english"]', card);
  const frenchArea = $('.listing-language[data-language="french"]', card);
  if (!englishArea || !frenchArea) return;
  const product = state.products.get(productId);
  if (!product) return;
  const english = currentListingFields(englishArea);
  const french = currentListingFields(frenchArea);
  if (!english.title || !french.title) return;
  if (
    listingFieldsEqual(english, product.english_listing)
    && listingFieldsEqual(french, product.french_listing)
  ) {
    return;
  }
  try {
    const updated = await api(`/api/products/${productId}/listing`, {
      method: "PATCH",
      body: JSON.stringify({ english, french }),
    });
    state.products.set(updated.id, updated);
    const undoButton = $(".undo-listing", card);
    if (undoButton) undoButton.disabled = !updated.has_previous_listing;
    showToast("Listing edits saved.");
  } catch (error) {
    showToast(error.message || "Could not save your edits.");
  }
}

async function refreshProduct(productId, card) {
  const button = $(".refresh-product", card);
  button.disabled = true;
  button.textContent = "Refreshing…";
  try {
    const current = state.products.get(productId);
    let product;
    if (current && productStore(current) === "temu") {
      const itemType = current.additional_details?.item_type
        || current.listing_type
        || "dress";
      product = await extractTemuWithExtension(
        current.sku,
        itemType,
        productId,
        (message) => {
          button.textContent = message.toLowerCase().includes("image")
            ? "Saving images..."
            : "Reading Temu...";
        },
        current.batch_number || 1,
        productNotes(current),
      );
    } else {
      product = await api(
        `/api/products/${productId}/refresh`,
        { method: "POST" },
      );
    }
    showProduct(product, card);
    showToast("Product refreshed.");
    await loadHistory();
  } catch (error) {
    button.textContent = "Refresh";
    button.disabled = false;
    showToast(error.message || "Refresh failed.");
  }
}

async function calculatePrice(productId, category, card) {
  if (!category) return;
  const input = $(".price-category-input", card);
  if (input) input.disabled = true;
  try {
    const product = await api(`/api/products/${productId}/calculate-price`, {
      method: "POST",
      body: JSON.stringify({ category }),
    });
    showProduct(product, card);
    showToast("Vinted price calculated by interpolation.");
    await loadHistory();
  } catch (error) {
    if (input) input.disabled = false;
    showToast(error.message || "Price calculation failed.");
  }
}

function attachCategoryCombobox(input, dropdown, getOptions, getCurrentValue, onSelect) {
  const valueForLabel = (label) => getOptions().find(([, text]) => text === label)?.[0] || "";
  const labelForValue = (value) => getOptions().find(([v]) => v === value)?.[1] || "";

  const closeDropdown = () => {
    dropdown.hidden = true;
    dropdown.replaceChildren();
  };

  const commitSelection = (value, label) => {
    input.value = label;
    closeDropdown();
    onSelect(value, label);
  };

  const renderOptions = (query) => {
    const needle = query.trim().toLowerCase();
    const options = getOptions();
    const matches = needle
      ? options.filter(([, label]) => label.toLowerCase().includes(needle))
      : options;
    if (!matches.length) {
      dropdown.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "combobox-empty";
      empty.textContent = "No matching category";
      dropdown.append(empty);
      dropdown.hidden = false;
      return;
    }
    dropdown.replaceChildren(
      ...matches.map(([value, label]) => {
        const option = document.createElement("button");
        option.type = "button";
        option.className = "combobox-option";
        option.textContent = label;
        option.dataset.value = value;
        option.addEventListener("mousedown", (event) => {
          event.preventDefault();
          commitSelection(value, label);
        });
        return option;
      }),
    );
    dropdown.hidden = false;
  };

  input.addEventListener("focus", () => {
    input.select();
    renderOptions("");
  });
  input.addEventListener("input", () => renderOptions(input.value));
  input.addEventListener("blur", () => {
    setTimeout(() => {
      closeDropdown();
      const typedValue = valueForLabel(input.value.trim());
      if (!typedValue) {
        input.value = labelForValue(getCurrentValue() || "");
      }
    }, 120);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      input.value = labelForValue(getCurrentValue() || "");
      closeDropdown();
      input.blur();
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const [firstVisible] = $$(".combobox-option", dropdown);
      if (firstVisible) {
        commitSelection(firstVisible.dataset.value, firstVisible.textContent);
      }
    }
  });
}

function initCategoryCombobox(card, product) {
  const field = $(".category-combobox", card);
  if (!field) return;
  const input = $(".price-category-input", field);
  const dropdown = $(".combobox-options", field);
  attachCategoryCombobox(
    input,
    dropdown,
    () => priceCategoryOptions,
    () => field.dataset.value,
    (value) => {
      field.dataset.value = value;
      calculatePrice(product.id, value, card);
    },
  );
}

async function copyText(value, message) {
  try {
    await navigator.clipboard.writeText(value);
    showToast(message);
  } catch {
    showToast("Clipboard access was blocked.");
  }
}

function clipboardPng(url) {
  if (clipboardImageCache.has(url)) return clipboardImageCache.get(url);
  const pngPromise = fetch(proxyImage(url), { cache: "force-cache" })
    .then(async (response) => {
      if (!response.ok) throw new Error("Image fetch failed");
      const sourceBlob = await response.blob();
      if (sourceBlob.type === "image/png") return sourceBlob;
      const bitmap = await createImageBitmap(sourceBlob);
      try {
        const canvas = document.createElement("canvas");
        canvas.width = bitmap.width;
        canvas.height = bitmap.height;
        canvas.getContext("2d").drawImage(bitmap, 0, 0);
        return await new Promise((resolve, reject) => {
          canvas.toBlob(
            (blob) => blob
              ? resolve(blob)
              : reject(new Error("PNG conversion failed")),
            "image/png",
          );
        });
      } finally {
        bitmap.close();
      }
    });
  clipboardImageCache.set(url, pngPromise);
  pngPromise.catch(() => clipboardImageCache.delete(url));
  while (clipboardImageCache.size > 6) {
    clipboardImageCache.delete(clipboardImageCache.keys().next().value);
  }
  return pngPromise;
}

async function copyImage(url, button = null) {
  const originalLabel = button?.textContent || "Copy image";
  try {
    if (!url) throw new Error("No image is selected");
    if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
      throw new Error("Image clipboard is unavailable");
    }
    if (button) {
      button.disabled = true;
      button.textContent = "Copying…";
    }
    await navigator.clipboard.write([
      new ClipboardItem({ "image/png": clipboardPng(url) }),
    ]);
    showToast("Image copied.");
  } catch (error) {
    showToast(
      error.name === "NotAllowedError"
        ? "Clipboard permission was blocked. Click Copy image again."
        : error.message || "Image could not be copied.",
    );
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalLabel;
    }
  }
}

function downloadImage(url) {
  const link = document.createElement("a");
  link.href = proxyImage(url, true);
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

function batchName(batchNumber) {
  return state.batchNames.get(batchNumber) || `Batch ${batchNumber}`;
}

function updateBatchControls() {
  const count = state.batchCounts.get(state.currentBatch) || 0;
  const activeSuccessfulCount = state.historyItems.filter(
    (product) => (
      (product.batch_number || 1) === state.activeBatch
      && product.extraction_status === "success"
    ),
  ).length;
  const currentName = batchName(state.currentBatch);
  elements.currentBatchLabel.textContent = currentName;
  elements.currentBatchCount.textContent = `${currentName === `Batch ${state.currentBatch}` ? "" : `Batch ${state.currentBatch} · `}${count} product${count === 1 ? "" : "s"}`;
  elements.startNextBatch.textContent = `Start Batch ${state.currentBatch + 1}`;
  elements.startNextBatch.disabled = state.running || state.batchGenerating || count === 0;
  elements.startNextBatch.title = count === 0
    ? `Add a product to Batch ${state.currentBatch} first.`
    : `Keep Batch ${state.currentBatch} and start Batch ${state.currentBatch + 1}.`;
  elements.finishCurrentBatch.textContent = `Done & delete Batch ${state.currentBatch}`;
  elements.finishCurrentBatch.disabled = state.running || state.batchGenerating || !state.currentBatch;
  elements.deleteActiveBatch.textContent = `Done & delete Batch ${state.activeBatch || state.currentBatch}`;
  elements.deleteActiveBatch.disabled = state.running || state.batchGenerating || !state.activeBatch;
  elements.renameBatch.disabled = state.running || state.batchGenerating || !state.activeBatch;
  elements.generateBatchDescriptions.disabled = (
    state.running || state.batchGenerating || activeSuccessfulCount === 0
  );
  elements.openDotbBatch.disabled = (
    state.running || state.batchGenerating || activeSuccessfulCount === 0
  );
  if (!state.batchGenerating) {
    elements.generateBatchDescriptions.textContent = "Generate all descriptions";
  }
}

function renderBatchState(payload) {
  state.currentBatch = payload.current_batch_number || 1;
  state.batchCounts = new Map(
    (payload.batches || []).map((batch) => [
      batch.batch_number,
      batch.product_count,
    ]),
  );
  state.batchNames = new Map(
    (payload.batches || []).map((batch) => [
      batch.batch_number,
      batch.name || `Batch ${batch.batch_number}`,
    ]),
  );
  const availableBatches = new Set([
    state.currentBatch,
    ...state.batchCounts.keys(),
  ]);
  if (!state.activeBatch || !availableBatches.has(state.activeBatch)) {
    state.activeBatch = state.currentBatch;
  }
  updateBatchControls();
}

function renderBatchTabs() {
  const batchNumbers = [...new Set([
    state.currentBatch,
    ...state.batchCounts.keys(),
  ])].sort((left, right) => left - right);
  elements.batchTabs.replaceChildren();
  batchNumbers.forEach((batchNumber) => {
    const count = state.batchCounts.get(batchNumber) || 0;
    const tab = document.createElement("button");
    tab.type = "button";
    tab.className = "batch-tab";
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(batchNumber === state.activeBatch));
    tab.disabled = state.batchGenerating;
    const name = batchName(batchNumber);
    tab.innerHTML = `
      <strong>${escapeHtml(name)}${batchNumber === state.currentBatch ? " · Current" : ""}</strong>
      <small>${name === `Batch ${batchNumber}` ? "" : `Batch ${batchNumber} · `}${count} product${count === 1 ? "" : "s"}</small>`;
    tab.addEventListener("click", () => {
      state.activeBatch = batchNumber;
      state.selectedHistoryIds.clear();
      renderHistory(state.historyItems);
      elements.resultsGrid.replaceChildren();
      elements.resultsSection.hidden = true;
    });
    elements.batchTabs.append(tab);
  });
}

function historyRow(product) {
  const row = document.createElement("div");
  row.className = "history-item";
  const image = product.main_image_url
    ? `<img class="history-image" src="${escapeHtml(proxyImage(product.main_image_url))}" alt="">`
    : `<div class="history-image"></div>`;
  row.innerHTML = `
    <input
      class="history-checkbox"
      type="checkbox"
      aria-label="Select ${escapeHtml(product.sku)}"
      ${state.selectedHistoryIds.has(product.id) ? "checked" : ""}
      ${product.extraction_status === "success" ? "" : "disabled"}
    >
    <div class="history-main">
      ${image}
      <div class="history-copy">
        <strong>${escapeHtml(product.title || product.sku)}</strong>
        <span>${productStore(product) === "temu" ? "Temu" : "SHEIN"} · ${escapeHtml(product.sku)} · ${escapeHtml(product.extraction_status)}</span>
        ${productNotes(product) ? `<small class="history-notes">${escapeHtml(productNotes(product))}</small>` : ""}
      </div>
    </div>
    <div class="inline-actions">
      <button class="button button-ghost open-history" type="button">Open</button>
      <button class="button button-danger delete-history" type="button">Delete</button>
    </div>`;
  $(".history-checkbox", row).addEventListener("change", (event) => {
    if (event.target.checked) state.selectedHistoryIds.add(product.id);
    else state.selectedHistoryIds.delete(product.id);
    updateHistorySelection();
  });
  $(".open-history", row).addEventListener("click", () => {
    if (product.extraction_status === "success") {
      showProduct(product);
      elements.resultsSection.scrollIntoView({ behavior: "smooth" });
    } else {
      elements.input.value = product.sku;
      updateInputState();
      elements.input.focus();
    }
  });
  $(".delete-history", row).addEventListener("click", async () => {
    if (!window.confirm(`Delete ${product.sku} from local history?`)) return;
    try {
      await api(`/api/products/${product.id}`, { method: "DELETE" });
      state.products.delete(product.id);
      state.selectedHistoryIds.delete(product.id);
      elements.resultsGrid.querySelector(`[data-product-id="${product.id}"]`)?.remove();
      await loadHistory();
      showToast("Product and saved screenshots deleted.");
    } catch (error) {
      showToast(error.message || "Delete failed.");
    }
  });
  return row;
}

function renderHistory(items) {
  state.historyItems = items;
  const visibleItems = items.filter(
    (product) => (product.batch_number || 1) === state.activeBatch,
  );
  const availableIds = new Set(
    visibleItems
      .filter((product) => product.extraction_status === "success")
      .map((product) => product.id),
  );
  state.selectedHistoryIds = new Set(
    [...state.selectedHistoryIds].filter((id) => availableIds.has(id)),
  );
  elements.savedCount.textContent = items.length;
  elements.historyEmpty.hidden = visibleItems.length > 0;
  elements.historyList.hidden = visibleItems.length === 0;
  elements.historyEmptyMessage.textContent = `No products saved in ${batchName(state.activeBatch)} yet.`;
  elements.deleteAllHistory.disabled = items.length === 0;
  elements.openAllProducts.textContent = `Open ${batchName(state.activeBatch)}`;
  elements.openAllProducts.disabled = !visibleItems.some(
    (product) => product.extraction_status === "success",
  );
  elements.deleteActiveBatch.textContent = `Done & delete Batch ${state.activeBatch}`;
  elements.deleteActiveBatch.disabled = state.running || state.batchGenerating || !state.activeBatch;
  elements.historyList.replaceChildren();
  visibleItems.forEach((product) => {
    elements.historyList.append(historyRow(product));
  });
  renderBatchTabs();
  renderMoveBatchOptions();
  updateHistorySelection();
  updateBatchControls();
}

function renderMoveBatchOptions() {
  const select = elements.moveToBatchSelect;
  if (!select) return;
  const previousValue = select.value;
  const batchNumbers = [...new Set([
    state.currentBatch,
    ...state.batchCounts.keys(),
  ])]
    .filter((batchNumber) => batchNumber !== state.activeBatch)
    .sort((left, right) => left - right);
  const nextBatchNumber = Math.max(state.currentBatch, ...batchNumbers, 0) + 1;
  select.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Move to batch…";
  select.append(placeholder);
  batchNumbers.forEach((batchNumber) => {
    const option = document.createElement("option");
    option.value = String(batchNumber);
    option.textContent = batchName(batchNumber);
    select.append(option);
  });
  const newOption = document.createElement("option");
  newOption.value = "new";
  newOption.textContent = `New batch (Batch ${nextBatchNumber})`;
  select.append(newOption);
  if ([...select.options].some((option) => option.value === previousValue)) {
    select.value = previousValue;
  }
}

function updateHistorySelection() {
  const count = state.selectedHistoryIds.size;
  elements.openSelectedProducts.disabled = count === 0;
  elements.openSelectedProducts.textContent = count
    ? `Open selected (${count})`
    : "Open selected";
  if (elements.moveToBatchSelect) elements.moveToBatchSelect.disabled = count === 0;
  if (elements.moveToBatchButton) elements.moveToBatchButton.disabled = count === 0;
}

async function moveSelectedToBatch() {
  const select = elements.moveToBatchSelect;
  const ids = [...state.selectedHistoryIds];
  if (!select || !ids.length) return;
  const rawValue = select.value;
  if (!rawValue) {
    showToast("Choose a batch to move the selected products into.");
    return;
  }
  const batchNumbers = [...state.batchCounts.keys(), state.currentBatch];
  const targetBatch = rawValue === "new"
    ? Math.max(state.currentBatch, ...batchNumbers, 0) + 1
    : Number(rawValue);
  if (!Number.isInteger(targetBatch) || targetBatch < 1) {
    showToast("Choose a valid batch.");
    return;
  }
  elements.moveToBatchButton.disabled = true;
  elements.moveToBatchSelect.disabled = true;
  try {
    const result = await api("/api/products/batches/move", {
      method: "POST",
      body: JSON.stringify({ product_ids: ids, batch_number: targetBatch }),
    });
    state.selectedHistoryIds.clear();
    state.activeBatch = targetBatch;
    await loadHistory();
    showToast(
      `${result.moved} product${result.moved === 1 ? "" : "s"} moved to ${batchName(targetBatch)}.`,
    );
  } catch (error) {
    showToast(error.message || "Products could not be moved.");
    updateHistorySelection();
  }
}

async function loadHistory() {
  try {
    const [response, batchState] = await Promise.all([
      api("/api/products?limit=100"),
      api("/api/products/batches"),
    ]);
    renderBatchState(batchState);
    renderHistory(response.items);
  } catch {
    showToast("History could not be loaded.");
  }
}

async function renameActiveBatch() {
  const batchNumber = state.activeBatch;
  if (!batchNumber) return;
  const requestedName = window.prompt(
    `Rename Batch ${batchNumber}:`,
    batchName(batchNumber),
  );
  if (requestedName === null) return;
  const name = requestedName.trim().replace(/\s+/g, " ");
  if (!name) {
    showToast("Enter a batch name.");
    return;
  }
  if (name.length > 40) {
    showToast("Batch names can contain up to 40 characters.");
    return;
  }
  elements.renameBatch.disabled = true;
  try {
    await api(`/api/products/batches/${batchNumber}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await loadHistory();
    showToast(`Batch ${batchNumber} renamed to ${name}.`);
  } catch (error) {
    showToast(error.message || "Batch could not be renamed.");
  } finally {
    updateBatchControls();
  }
}

function batchDescriptionOptions(product) {
  const supportedTypes = new Set(["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "earrings", "bag", "hat", "mask", "plant", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"]);
  const savedType = product.listing_type;
  const extractedType = product.additional_details?.item_type;
  const listingType = supportedTypes.has(savedType)
    ? savedType
    : supportedTypes.has(extractedType) ? extractedType : "dress";
  if (listingType !== "skirt") {
    return { listing_type: listingType, skirt_length: null };
  }
  const savedTitles = [
    product.english_listing?.title,
    product.french_listing?.title,
    product.title,
  ].filter(Boolean).join(" ");
  return {
    listing_type: "skirt",
    skirt_length: /\bmidi\b/i.test(savedTitles) ? "midi" : "long",
  };
}

async function generateAllBatchDescriptions() {
  const batchNumber = state.activeBatch;
  const products = state.historyItems.filter(
    (product) => (
      (product.batch_number || 1) === batchNumber
      && product.extraction_status === "success"
    ),
  );
  if (!products.length) {
    showToast(`${batchName(batchNumber)} has no successful products.`);
    return;
  }
  if (!window.confirm(
    `Generate or regenerate descriptions for all ${products.length} product${products.length === 1 ? "" : "s"} in ${batchName(batchNumber)}?`,
  )) return;

  state.batchGenerating = true;
  updateBatchControls();
  let succeeded = 0;
  const failedSkus = [];
  for (let index = 0; index < products.length; index += 1) {
    const product = products[index];
    elements.generateBatchDescriptions.textContent = `Generating ${index + 1}/${products.length}…`;
    try {
      const updated = await api(
        `/api/products/${product.id}/generate-description`,
        {
          method: "POST",
          body: JSON.stringify(batchDescriptionOptions(product)),
        },
      );
      state.products.set(updated.id, updated);
      const openCard = elements.resultsGrid.querySelector(
        `[data-product-id="${updated.id}"]`,
      );
      if (openCard) showProduct(updated, openCard);
      succeeded += 1;
    } catch {
      failedSkus.push(product.sku);
    }
  }
  state.batchGenerating = false;
  await loadHistory();
  updateBatchControls();
  if (failedSkus.length) {
    showToast(`${succeeded}/${products.length} descriptions generated. Failed: ${failedSkus.join(", ")}`);
  } else {
    showToast(`All ${succeeded} descriptions generated for ${batchName(batchNumber)}.`);
  }
}

function dotbProductPayload(product) {
  const card = elements.resultsGrid.querySelector(
    `[data-product-id="${product.id}"]`,
  );
  const frenchArea = card?.querySelector(
    '.listing-language[data-language="french"]',
  );
  const title = frenchArea
    ? $(".listing-title", frenchArea).value.trim()
    : product.french_listing?.title?.trim();
  const description = frenchArea
    ? $(".listing-description", frenchArea).value.trim()
    : product.french_listing?.description?.trim();
  const itemType = product.listing_type
    || product.additional_details?.item_type
    || "dress";
  const safeDescription = ensureNegotiableDescription(
    description || "",
    itemType,
    "french",
  );
  const hashtags = frenchArea
    ? $(".hashtags", frenchArea).textContent.trim()
    : (product.french_listing?.hashtags || []).join(" ");
  const calculation = product.additional_details?.price_calculator || {};
  const recommendedPrice = Number(calculation.recommended_price_eur);
  const dotbPrice = Number.isFinite(recommendedPrice)
    ? Math.floor(recommendedPrice) + 0.9
    : null;
  const allImages = [
    product.main_image_url,
    ...(product.additional_image_urls || []),
  ].filter((url) => String(url || "").startsWith("/downloads/"));
  const selectedImage = card ? currentImage(card) : null;
  const orderedImages = [
    selectedImage,
    ...allImages,
  ].filter((url, index, items) => url && items.indexOf(url) === index);
  return {
    id: product.id,
    sku: product.sku,
    title: title || "",
    description: [safeDescription, hashtags].filter(Boolean).join("\n\n"),
    price: Number.isFinite(dotbPrice)
      ? dotbPrice.toFixed(2)
      : "",
    itemType,
    brand: product.fictional_brand || "",
    colour: product.colour || product.additional_details?.colour || "",
    category: product.category || "",
    images: orderedImages.slice(0, 6).map(
      (url) => new URL(url, window.location.origin).href,
    ),
    productUrl: product.product_url || "",
  };
}

function openBatchInDotb() {
  const products = state.historyItems.filter(
    (product) => (
      (product.batch_number || 1) === state.activeBatch
      && product.extraction_status === "success"
    ),
  );
  if (!products.length) {
    showToast(`${batchName(state.activeBatch)} has no successful products.`);
    return;
  }
  if (!state.dotbExtensionReady) {
    showToast("Reload the Product Extractor extension in Brave, then refresh this page.");
    return;
  }
  const payloads = products.map(dotbProductPayload);
  const incomplete = payloads.filter((product) => (
    !product.title
    || !product.description
    || !product.price
  ));
  if (incomplete.length) {
    showToast(
      `Prepare descriptions and prices first. Missing: ${incomplete.map((item) => item.sku).join(", ")}`,
    );
    return;
  }
  window.dispatchEvent(new CustomEvent("product-extractor:dotb-start", {
    detail: { products: payloads },
  }));
  showToast(`${products.length} products sent to the Dotb review workflow.`);
}

async function openBatchProducts(batchNumber) {
  try {
    const response = await api("/api/products?limit=100");
    const products = response.items.filter(
      (product) => (
        (product.batch_number || 1) === batchNumber
        && product.extraction_status === "success"
      ),
    );
    if (!products.length) {
      showToast(`Batch ${batchNumber} has no successful products to open.`);
      return;
    }
    elements.resultsGrid.replaceChildren();
    [...products].reverse().forEach((product) => showProduct(product));
    elements.resultsSection.scrollIntoView({ behavior: "smooth" });
    showToast(`Opened ${products.length} products from Batch ${batchNumber}.`);
  } catch (error) {
    showToast(error.message || `Batch ${batchNumber} could not be opened.`);
  }
}

async function startNextBatch() {
  elements.startNextBatch.disabled = true;
  try {
    const response = await api("/api/products/batches/next", {
      method: "POST",
    });
    const nextBatch = response.current_batch_number;
    state.currentBatch = nextBatch;
    state.activeBatch = nextBatch;
    state.queue.clear();
    elements.queueList.replaceChildren();
    elements.queueSection.hidden = true;
    elements.resultsGrid.replaceChildren();
    elements.resultsSection.hidden = true;
    elements.input.value = "";
    updateInputState();
    await loadHistory();
    elements.input.focus();
    showToast(`Batch ${nextBatch} started. Earlier batches are still saved.`);
  } catch (error) {
    updateBatchControls();
    showToast(error.message || "A new batch could not be started.");
  }
}

async function completeBatch(batchNumber) {
  const count = state.batchCounts.get(batchNumber) || 0;
  const name = batchName(batchNumber);
  const confirmMessage = count === 0
    ? `Delete empty ${name}?`
    : `Mark ${name} done and permanently delete its ${count} product${count === 1 ? "" : "s"} and saved images?`;
  if (!window.confirm(confirmMessage)) return;
  elements.finishCurrentBatch.disabled = true;
  try {
    const response = await api(`/api/products/batches/${batchNumber}`, {
      method: "DELETE",
    });
    [...state.products.entries()].forEach(([productId, product]) => {
      if ((product.batch_number || 1) === batchNumber) {
        state.products.delete(productId);
        state.selectedHistoryIds.delete(productId);
      }
    });
    $$(`[data-batch-number="${batchNumber}"]`, elements.resultsGrid)
      .forEach((card) => card.remove());
    if (!elements.resultsGrid.children.length) {
      elements.resultsSection.hidden = true;
    }
    if (batchNumber === state.currentBatch) {
      state.queue.clear();
      elements.queueList.replaceChildren();
      elements.queueSection.hidden = true;
      elements.input.value = "";
      updateInputState();
    }
    if (state.activeBatch === batchNumber) {
      state.activeBatch = response.current_batch_number;
    }
    await loadHistory();
    const stillCurrent = response.current_batch_number === batchNumber;
    showToast(
      stillCurrent
        ? `${name} was cleared.`
        : `${name} deleted. ${batchName(response.current_batch_number)} is current.`,
    );
  } catch (error) {
    updateBatchControls();
    showToast(error.message || `Batch ${batchNumber} could not be deleted.`);
  }
}

async function openAllProducts() {
  elements.openAllProducts.disabled = true;
  try {
    const response = await api("/api/products?limit=100");
    const products = response.items.filter(
      (product) => (
        (product.batch_number || 1) === state.activeBatch
        && product.extraction_status === "success"
      ),
    );
    if (!products.length) {
      showToast(`Batch ${state.activeBatch} has no successful products to open.`);
      return;
    }
    elements.resultsGrid.replaceChildren();
    [...products].reverse().forEach((product) => showProduct(product));
    elements.resultsSection.scrollIntoView({ behavior: "smooth" });
    showToast(`Opened ${products.length} products from Batch ${state.activeBatch}.`);
  } catch (error) {
    showToast(error.message || "Products could not be opened.");
  } finally {
    elements.openAllProducts.disabled = false;
  }
}

async function openSelectedProducts() {
  const selectedIds = new Set(state.selectedHistoryIds);
  if (!selectedIds.size) return;
  elements.openSelectedProducts.disabled = true;
  try {
    const response = await api("/api/products?limit=100");
    const products = response.items.filter(
      (product) => (
        selectedIds.has(product.id)
        && (product.batch_number || 1) === state.activeBatch
        && product.extraction_status === "success"
      ),
    );
    if (!products.length) {
      showToast("The selected products are no longer available.");
      await loadHistory();
      return;
    }
    elements.resultsGrid.replaceChildren();
    [...products].reverse().forEach((product) => showProduct(product));
    elements.resultsSection.scrollIntoView({ behavior: "smooth" });
    showToast(`Opened ${products.length} selected product${products.length === 1 ? "" : "s"}.`);
  } catch (error) {
    showToast(error.message || "Selected products could not be opened.");
  } finally {
    updateHistorySelection();
  }
}

async function deleteAllProducts() {
  if (!window.confirm("Delete every saved product? This cannot be undone.")) return;
  elements.deleteAllHistory.disabled = true;
  try {
    await api("/api/products", { method: "DELETE" });
    state.products.clear();
    state.selectedHistoryIds.clear();
    elements.resultsGrid.replaceChildren();
    elements.resultsSection.hidden = true;
    await loadHistory();
    showToast("All products and saved screenshots were deleted.");
  } catch (error) {
    elements.deleteAllHistory.disabled = false;
    showToast(error.message || "Products could not be deleted.");
  }
}

async function checkHealth() {
  const status = $("#healthStatus");
  try {
    await api("/health");
    status.classList.add("online");
    $("span", status).textContent = "Ready";
  } catch {
    status.classList.add("offline");
    $("span", status).textContent = "Offline";
  }
}

elements.input.addEventListener("input", updateInputState);
$$('input[name="store"]').forEach((input) => {
  input.addEventListener("change", updateStoreHelp);
});
elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const entries = parseSkuEntries();
  if (!state.running && entries.length) {
    runBatch(entries, selectedItemType(), selectedStore());
  }
});

let manualPriceCategoryOptions = [];
let manualPriceSelectedCategory = "";

async function loadManualPriceCategories() {
  if (!elements.manualPriceCategory) return;
  try {
    const categories = await api("/api/products/price-categories");
    manualPriceCategoryOptions = categories.map(({ value, label }) => [value, label]);
    attachCategoryCombobox(
      elements.manualPriceCategory,
      elements.manualPriceCategoryOptions,
      () => manualPriceCategoryOptions,
      () => manualPriceSelectedCategory,
      (value) => {
        manualPriceSelectedCategory = value;
      },
    );
  } catch (error) {
    showToast(error.message || "Could not load price categories.");
  }
}

elements.manualPriceForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const category = manualPriceSelectedCategory;
  const sourcePrice = Number(elements.manualPriceInput.value);
  if (!category || !Number.isFinite(sourcePrice) || sourcePrice < 0) {
    showToast("Choose a category and a valid purchase price.");
    return;
  }
  elements.manualPriceButton.disabled = true;
  elements.manualPriceButton.textContent = "Calculating...";
  try {
    const result = await api("/api/products/price-calculator", {
      method: "POST",
      body: JSON.stringify({ category, source_price_eur: sourcePrice }),
    });
    elements.manualPriceResult.hidden = false;
    elements.manualPriceResult.classList.toggle("unavailable", !result.eligible);
    elements.manualPriceResult.innerHTML = result.eligible
      ? `
        <strong>${escapeHtml(formatEuro(result.recommended_price_eur))}</strong>
        <span>Vinted range ${escapeHtml(formatEuro(result.vinted_price_min_eur))} – ${escapeHtml(formatEuro(result.vinted_price_max_eur))}</span>
      `
      : `<span>${escapeHtml(result.message || "No price available for this amount.")}</span>`;
  } catch (error) {
    elements.manualPriceResult.hidden = false;
    elements.manualPriceResult.classList.add("unavailable");
    elements.manualPriceResult.innerHTML = `<span>${escapeHtml(error.message || "Calculation failed.")}</span>`;
  } finally {
    elements.manualPriceButton.disabled = false;
    elements.manualPriceButton.textContent = "Calculate";
  }
});

loadManualPriceCategories();
elements.clear.addEventListener("click", () => {
  elements.input.value = "";
  updateInputState();
  elements.input.focus();
});
$("#clearResultsButton").addEventListener("click", () => {
  elements.resultsGrid.replaceChildren();
  elements.resultsSection.hidden = true;
});
elements.openAllProducts.addEventListener("click", openAllProducts);
elements.openSelectedProducts.addEventListener("click", openSelectedProducts);
elements.moveToBatchButton?.addEventListener("click", moveSelectedToBatch);
elements.generateBatchDescriptions.addEventListener("click", generateAllBatchDescriptions);
elements.openDotbBatch.addEventListener("click", openBatchInDotb);
elements.renameBatch.addEventListener("click", renameActiveBatch);
elements.startNextBatch.addEventListener("click", startNextBatch);
elements.finishCurrentBatch.addEventListener("click", () => {
  completeBatch(state.currentBatch);
});
elements.deleteActiveBatch.addEventListener("click", () => {
  completeBatch(state.activeBatch);
});
$("#refreshHistoryButton").addEventListener("click", loadHistory);
elements.deleteAllHistory.addEventListener("click", deleteAllProducts);
elements.closeImagePreview.addEventListener("click", closeImagePreview);
elements.previousPreviewImage.addEventListener("click", () => moveImagePreview(-1));
elements.nextPreviewImage.addEventListener("click", () => moveImagePreview(1));
elements.imagePreview.addEventListener("click", (event) => {
  if (event.target === elements.imagePreview) closeImagePreview();
});
document.addEventListener("keydown", (event) => {
  if (elements.imagePreview.hidden) return;
  if (event.key === "Escape") closeImagePreview();
  if (event.key === "ArrowLeft") moveImagePreview(-1);
  if (event.key === "ArrowRight") moveImagePreview(1);
});

updateStoreHelp();
updateInputState();
checkHealth();
loadHistory();

function clearImageReference() {
  elements.imageReferenceInput.value = "";
  elements.imageReferencePreview.hidden = true;
  elements.imageReferencePreviewImg.removeAttribute("src");
  if (currentReferencePreviewUrl) URL.revokeObjectURL(currentReferencePreviewUrl);
  currentReferencePreviewUrl = null;
}

function clearImageGuide() {
  elements.imageGuideInput.value = "";
  elements.imageGuidePreview.hidden = true;
  elements.imageGuidePreviewImg.removeAttribute("src");
  if (currentGuidePreviewUrl) URL.revokeObjectURL(currentGuidePreviewUrl);
  currentGuidePreviewUrl = null;
}

function clearImageExample() {
  elements.imageExampleInput.value = "";
  elements.imageExamplePreview.hidden = true;
  elements.imageExamplePreviewImg.removeAttribute("src");
  if (currentExamplePreviewUrl) URL.revokeObjectURL(currentExamplePreviewUrl);
  currentExamplePreviewUrl = null;
}

function resetImageConversation() {
  if (currentGeneratedImageUrl) URL.revokeObjectURL(currentGeneratedImageUrl);
  currentGeneratedImageBlob = null;
  currentGeneratedImageUrl = null;
  lastImageRequest = null;
  elements.imageConversationLog.innerHTML = `
    <div class="image-chat-empty">No messages yet. Send a prompt like you would in Gemini.</div>
  `;
  elements.generatedImageLink.hidden = true;
  elements.generatedImageLink.removeAttribute("href");
  elements.generatedImageLink.innerHTML = "";
  elements.generateImageButton.textContent = "Send to Vertex";
  elements.retryImageGeneration.hidden = true;
}

function appendImageChatMessage(role, content, imageUrl = null) {
  $(".image-chat-empty", elements.imageConversationLog)?.remove();
  const message = document.createElement("div");
  message.className = `image-chat-message ${role}`;
  if (content) {
    const bubble = document.createElement("div");
    bubble.className = "image-chat-bubble";
    bubble.textContent = content;
    message.append(bubble);
  }
  if (imageUrl) {
    const link = document.createElement("a");
    link.href = imageUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = "Generated edit";
    link.append(image);
    message.append(link);
  }
  elements.imageConversationLog.append(message);
  elements.imageConversationLog.scrollTop = elements.imageConversationLog.scrollHeight;
}

function previewUploadedImage(input, preview, image, currentUrl, emptyHandler) {
  const file = input.files?.[0];
  if (!file) {
    emptyHandler();
    return null;
  }
  if (!file.type.startsWith("image/")) {
    showToast("Upload an image file.");
    emptyHandler();
    return null;
  }
  if (currentUrl) URL.revokeObjectURL(currentUrl);
  const url = URL.createObjectURL(file);
  image.src = url;
  preview.hidden = false;
  return url;
}

function updateImageStyleVisibility() {
  const hasPreset = Boolean(elements.imagePromptPreset.value);
  const hasSceneReference = Boolean(elements.imageReferenceInput.files?.[0]);
  const shouldHide = hasPreset || hasSceneReference;
  elements.imageOptions.hidden = shouldHide;
}

function updateImagePresetHint() {
  const value = elements.imagePromptPreset.value;
  const needsAngleGuide = value === "backView" || value === "sideView";
  if (!needsAngleGuide) {
    elements.imagePresetHint.hidden = true;
    elements.imagePresetHint.textContent = "";
    return;
  }
  const hasGuide = Boolean(elements.imageGuideInput.files?.[0]);
  elements.imagePresetHint.hidden = false;
  elements.imagePresetHint.textContent = hasGuide
    ? "Angle guide added. Vertex will use it to force the side/back viewpoint."
    : "Tip: side/back view is much more reliable if you upload a side/back guide image. Without it, Vertex may keep generating front view.";
}

function updateImagePromptStrengthLabel() {
  if (!elements.imagePromptStrength || !elements.imagePromptStrengthValue) return;
  elements.imagePromptStrengthValue.textContent = `${elements.imagePromptStrength.value}%`;
}

async function openImageGenerateModal(productId, selectedImageUrl = null) {
  currentProductIdForImage = productId;
  renderModalSourceImages(productId, selectedImageUrl);
  elements.imagePromptPreset.value = "";
  elements.imagePrompt.value = "";
  if (elements.imagePromptStrength) {
    elements.imagePromptStrength.value = "95";
    updateImagePromptStrengthLabel();
  }
  clearImageReference();
  clearImageGuide();
  clearImageExample();
  updateImageStyleVisibility();
  updateImagePresetHint();
  resetImageConversation();
  elements.imageGenerateModal.hidden = false;
  document.body.classList.add("preview-open");
  elements.imagePrompt.focus();
}

async function generateProductImage(options = {}) {
  const retrying = Boolean(options.retry);
  const button = elements.generateImageButton;
  const retryButton = elements.retryImageGeneration;
  const prompt = retrying ? (lastImageRequest?.prompt || "") : elements.imagePrompt.value.trim();
  if (!prompt) {
    showToast("Enter a description for the image.");
    return;
  }
  
  button.disabled = true;
  retryButton.disabled = true;
  button.textContent = "Generating...";
  elements.generatedImageLink.hidden = true;
  
  try {
    const conversationImageForRequest = retrying
      ? lastImageRequest?.conversationImageBlob || null
      : currentGeneratedImageBlob;
    const body = new FormData();
    body.append("prompt", prompt);
    body.append("mode", "conversation");
    if (elements.imagePromptStrength) {
      body.append("prompt_strength", elements.imagePromptStrength.value || "95");
    }
    if (elements.imageNegativePrompt?.value?.trim()) {
      body.append("negative_prompt", elements.imageNegativePrompt.value.trim());
    }
    if (currentProductIdForImage) {
      body.append("product_id", String(currentProductIdForImage));
    }
    const hasPreset = Boolean(elements.imagePromptPreset.value);
    const hasSceneReference = Boolean(elements.imageReferenceInput.files?.[0]);
    body.append(
      "style",
      hasPreset || hasSceneReference
        ? "reference"
        : $('input[name="imageStyle"]:checked')?.value || "mockup",
    );
    if (elements.imagePromptPreset.value) {
      body.append("prompt_preset", elements.imagePromptPreset.value);
    }
    if (currentProductImageUrlForImage) {
      body.append("selected_image_url", currentProductImageUrlForImage);
    }
    const referenceFile = elements.imageReferenceInput.files?.[0];
    if (referenceFile) {
      body.append("reference_image", referenceFile);
    }
    const guideFile = elements.imageGuideInput.files?.[0];
    if (guideFile) {
      body.append("guide_image", guideFile);
    }
    const exampleFile = elements.imageExampleInput.files?.[0];
    if (exampleFile) {
      body.append("example_image", exampleFile);
    }
    if (conversationImageForRequest) {
      body.append(
        "conversation_image",
        conversationImageForRequest,
        "previous-generated-image.png",
      );
    }
    lastImageRequest = {
      prompt,
      conversationImageBlob: conversationImageForRequest,
    };
    appendImageChatMessage("user", retrying ? `Retry: ${prompt}` : prompt);
    const response = await fetch("/api/images/generate", {
      method: "POST",
      body,
    });
    
    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: "Image generation failed" }));
      throw new Error(error.message || error.error?.message || "Image generation failed");
    }
    
    const blob = await response.blob();
    if (currentGeneratedImageUrl) URL.revokeObjectURL(currentGeneratedImageUrl);
    currentGeneratedImageBlob = blob;
    const url = URL.createObjectURL(blob);
    currentGeneratedImageUrl = url;
    
    elements.generatedImageLink.href = url;
    elements.generatedImageLink.hidden = false;
    elements.generatedImageLink.innerHTML = `
      <img src="${url}" alt="Generated image">
      <div>
        <strong>Generated Image</strong>
        <small>Open full size in a new tab</small>
      </div>
    `;
    appendImageChatMessage("assistant", "Generated image", url);
    elements.imagePrompt.value = "";
    elements.generateImageButton.textContent = "Send follow-up";
    elements.retryImageGeneration.hidden = false;
    
    showToast("Image generated successfully!");
  } catch (error) {
    showToast(error.message || "Image generation failed.");
  } finally {
    button.disabled = false;
    retryButton.disabled = false;
    button.textContent = currentGeneratedImageBlob ? "Send follow-up" : "Send to Vertex";
  }
}

elements.closeImageGenerate.addEventListener("click", () => {
  elements.imageGenerateModal.hidden = true;
  document.body.classList.remove("preview-open");
  currentProductIdForImage = null;
  currentProductImageUrlForImage = null;
  clearImageReference();
  clearImageGuide();
  clearImageExample();
  resetImageConversation();
});

elements.imageGenerateModal.addEventListener("click", (event) => {
  if (event.target === elements.imageGenerateModal) {
    elements.imageGenerateModal.hidden = true;
    document.body.classList.remove("preview-open");
    currentProductIdForImage = null;
    currentProductImageUrlForImage = null;
    clearImageReference();
    clearImageGuide();
    clearImageExample();
    resetImageConversation();
  }
});

elements.imageReferenceInput.addEventListener("change", () => {
  currentReferencePreviewUrl = previewUploadedImage(
    elements.imageReferenceInput,
    elements.imageReferencePreview,
    elements.imageReferencePreviewImg,
    currentReferencePreviewUrl,
    clearImageReference,
  );
  updateImageStyleVisibility();
});

elements.clearImageReference.addEventListener("click", clearImageReference);
elements.clearImageReference.addEventListener("click", updateImageStyleVisibility);
elements.imageGuideInput.addEventListener("change", () => {
  currentGuidePreviewUrl = previewUploadedImage(
    elements.imageGuideInput,
    elements.imageGuidePreview,
    elements.imageGuidePreviewImg,
    currentGuidePreviewUrl,
    clearImageGuide,
  );
  updateImagePresetHint();
});
elements.clearImageGuide.addEventListener("click", () => {
  clearImageGuide();
  updateImagePresetHint();
});
elements.imageExampleInput.addEventListener("change", () => {
  currentExamplePreviewUrl = previewUploadedImage(
    elements.imageExampleInput,
    elements.imageExamplePreview,
    elements.imageExamplePreviewImg,
    currentExamplePreviewUrl,
    clearImageExample,
  );
});
elements.clearImageExample.addEventListener("click", clearImageExample);
elements.resetImageConversation.addEventListener("click", resetImageConversation);
elements.retryImageGeneration.addEventListener("click", () => generateProductImage({ retry: true }));
elements.imagePromptPreset.addEventListener("change", () => {
  const value = elements.imagePromptPreset.value;
  if (value) {
    elements.imagePrompt.value = imagePromptPresets[value] || "";
    elements.imagePrompt.focus();
  }
  updateImageStyleVisibility();
  updateImagePresetHint();
});
elements.imagePromptStrength?.addEventListener("input", updateImagePromptStrengthLabel);

elements.generateImageButton.addEventListener("click", generateProductImage);
