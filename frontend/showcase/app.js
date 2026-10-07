// Original showcase dashboard, preserved as an alternate presentation.
const state = {
  product: null,
  lastSku: "",
  busy: false,
  toastTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const clipboardImageCache = new Map();

const elements = {
  form: $("#extractForm"),
  sku: $("#skuInput"),
  extractButton: $("#extractButton"),
  status: $("#statusPanel"),
  statusTitle: $("#statusTitle"),
  statusMessage: $("#statusMessage"),
  error: $("#errorPanel"),
  errorTitle: $("#errorTitle"),
  errorMessage: $("#errorMessage"),
  retry: $("#retryButton"),
  candidates: $("#candidateList"),
  productSection: $("#productSection"),
  mainImage: $("#mainImage"),
  generate: $("#generateButton"),
  listingTypePicker: $("#listingTypePicker"),
  fictionalBrand: $("#fictionalBrand"),
  fictionalBrandLabel: $("#fictionalBrandLabel"),
  fictionalBrandName: $("#fictionalBrandName"),
  listingEmpty: $("#listingEmpty"),
  listingColumns: $("#listingColumns"),
  historyGrid: $("#historyGrid"),
  historyEmpty: $("#historyEmpty"),
  deleteAllHistory: $("#deleteAllHistoryButton"),
  toast: $("#toast"),
};

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = String(value ?? "");
  return node.innerHTML;
}

function proxiedImageUrl(url, download = false) {
  if (!url || url.startsWith("/downloads/")) return url;
  const params = new URLSearchParams({ url });
  if (download) params.set("download", "true");
  return `/api/images/proxy?${params}`;
}

function toast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 3200);
}

function setBusy(busy, title = "Searching SHEIN", message = "Searching SHEIN in the background for an exact SKU match…") {
  state.busy = busy;
  elements.extractButton.disabled = busy;
  elements.generate.disabled = busy;
  $("#refreshButton").disabled = busy;
  elements.status.hidden = !busy;
  elements.statusTitle.textContent = title;
  elements.statusMessage.textContent = message;
  if (busy) elements.error.hidden = true;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error || { code: "request_failed", message: "The request failed." };
    const thrown = new Error(error.message);
    Object.assign(thrown, error);
    throw thrown;
  }
  return payload;
}

function showError(error) {
  elements.error.hidden = false;
  elements.errorTitle.textContent = error.code === "captcha_detected" ? "Verification needed" : "Couldn’t complete extraction";
  elements.errorMessage.textContent = error.message || "An unexpected error occurred.";
  elements.retry.hidden = !error.retryable;
  elements.candidates.replaceChildren();
  const candidates = error.details?.possible_matches || [];
  candidates.forEach((candidate) => {
    const row = document.createElement("div");
    row.className = "candidate";
    const image = candidate.image_url
      ? `<img src="${escapeHtml(proxiedImageUrl(candidate.image_url))}" alt="">`
      : `<div></div>`;
    row.innerHTML = `${image}<strong>${escapeHtml(candidate.title)}</strong><button class="button button-secondary" type="button">Choose</button>`;
    $("button", row).addEventListener("click", () => extractSku(state.lastSku, candidate.url));
    elements.candidates.append(row);
  });
  elements.error.scrollIntoView({ behavior: "smooth", block: "center" });
}

function measurementLine(product) {
  return Object.entries(product.measurements || {})
    .map(([label, value]) => `${label}: ${value}`)
    .join(" · ");
}

function renderMeasurements(product) {
  const grid = $("#measurementGrid");
  grid.replaceChildren();
  Object.entries(product.measurements || {}).forEach(([label, value]) => {
    const item = document.createElement("div");
    item.className = "measurement";
    const original = product.measurement_originals?.[label];
    item.innerHTML = `<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${original ? `<small>Original: ${escapeHtml(original)}</small>` : ""}`;
    grid.append(item);
  });
  $("#measurementType").textContent = product.measurement_table_type || "Measurement table";
  $("#measurementNote").textContent = Object.keys(product.measurement_originals || {}).length
    ? "Inch values were converted to centimetres; originals are preserved."
    : "Values shown exactly as verified on the SHEIN size guide.";
}

function setMainImage(url, originalUrl, activeButton = null) {
  elements.mainImage.src = proxiedImageUrl(url);
  elements.mainImage.alt = state.product?.title || "SHEIN product";
  elements.mainImage.dataset.originalUrl = originalUrl || url;
  $$(".thumbnail").forEach((button) => button.classList.toggle("active", button === activeButton));
}

function renderImages(product) {
  setMainImage(product.main_image_url, product.main_image_url);
  $("#imageFallbackBadge").hidden = !product.image_is_screenshot;
  const grid = $("#thumbnailGrid");
  grid.replaceChildren();
  const images = [product.main_image_url, ...(product.additional_image_urls || [])].filter(Boolean);
  $("#imageCount").textContent = `${images.length} image${images.length === 1 ? "" : "s"}`;
  images.forEach((url, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `thumbnail${index === 0 ? " active" : ""}`;
    button.setAttribute("aria-label", `Show image ${index + 1}`);
    const image = document.createElement("img");
    image.src = proxiedImageUrl(url);
    image.alt = "";
    image.loading = "lazy";
    button.append(image);
    button.addEventListener("click", () => setMainImage(url, url, button));
    grid.append(button);
  });
}

function fillListing(language, listing) {
  if (!listing) return;
  const listingType = state.product?.listing_type || state.product?.additional_details?.item_type || "dress";
  const cleanTitle = cleanListingTitle(listing.title || "", listingType);
  $(`#${language}Title`).value = cleanTitle;
  $(`#${language}Description`).value = ensureNegotiableDescription(
    cleanListingDescription(
      listing.description || "",
      listingType,
      language,
      cleanTitle,
    ),
    listingType,
    language,
  );
  $(`#${language}Hashtags`).textContent = (listing.hashtags || []).join(" ");
}

function renderListings(product) {
  const hasListings = product.english_listing && product.french_listing;
  elements.listingEmpty.hidden = hasListings;
  elements.listingColumns.hidden = !hasListings;
  elements.fictionalBrand.hidden = !hasListings || !product.fictional_brand;
  const isStyleToken = ["skirt", "hat", "mask", "jeans", "bag", "coat", "jacket", "plant"].includes(product.listing_type);
  elements.fictionalBrandLabel.textContent = isStyleToken
    ? "Clothing style"
    : "Fictional brand";
  elements.fictionalBrandName.textContent = (
    isStyleToken && product.fictional_brand === "oldmoney"
      ? "old money"
      : isStyleToken && product.fictional_brand === "wildwest"
        ? "Wild West"
      : product.fictional_brand || ""
  );
  const selectedType = product.listing_type || "dress";
  const selectedRadio = $(`input[name="listingType"][value="${selectedType}"]`);
  if (selectedRadio) selectedRadio.checked = true;
  elements.generate.textContent = hasListings ? "Regenerate Both" : "Generate Description";
  if (hasListings) {
    fillListing("english", product.english_listing);
    fillListing("french", product.french_listing);
  }
}

function renderProduct(product) {
  state.product = product;
  elements.productSection.hidden = false;
  $("#productTitle").textContent = product.title || product.sku;
  $("#detailTitle").textContent = product.title || "Product details unavailable";
  $("#productSku").textContent = product.sku;
  $("#openProductButton").textContent = "Open SHEIN Product";
  $("#productUrl").href = product.product_url || "#";
  $("#productColour").textContent = product.colour || "Not listed";
  $("#productMaterial").textContent = product.material || "Not listed";
  $("#productCategory").textContent = product.category || "Not listed";
  renderImages(product);
  renderMeasurements(product);
  renderListings(product);
  elements.productSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function extractSku(
  sku,
  candidateUrl = null,
) {
  const cleaned = sku.trim();
  if (!cleaned) {
    showError({ code: "validation_error", message: "Paste a SHEIN SKU first.", retryable: false });
    elements.sku.focus();
    return;
  }
  state.lastSku = cleaned;
  setBusy(true, candidateUrl ? "Opening selected product" : "Searching SHEIN", candidateUrl
    ? "Checking the product details and reading the Size S guide…"
    : "Chromium will search SHEIN for the exact product code.");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180000);
  try {
    const body = { sku: cleaned };
    if (candidateUrl) body.candidate_url = candidateUrl;
    const product = await api("/api/products/extract", {
      method: "POST",
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    renderProduct(product);
    await loadHistory();
    toast("Product extracted and saved locally.");
  } catch (error) {
    showError(
      error.name === "AbortError"
        ? {
            code: "extraction_timeout",
            message: "Extraction timed out. Please retry.",
            retryable: true,
          }
        : error,
    );
  } finally {
    clearTimeout(timeout);
    setBusy(false);
  }
}

async function refreshProduct() {
  if (!state.product) return;
  setBusy(true, "Refreshing product", "Reopening SHEIN and replacing the stored product data…");
  try {
    const product = await api(`/api/products/${state.product.id}/refresh`, { method: "POST" });
    renderProduct(product);
    await loadHistory();
    toast("Product refreshed.");
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function generateDescription() {
  if (!state.product) return;
  const listingType = $('input[name="listingType"]:checked')?.value || "dress";
  setBusy(true, "Writing both listings", "Gemini is preparing English and French copy from the verified facts…");
  try {
    const product = await api(`/api/products/${state.product.id}/generate-description`, {
      method: "POST",
      body: JSON.stringify({ listing_type: listingType }),
    });
    renderProduct(product);
    await loadHistory();
    toast("English and French listings generated.");
  } catch (error) {
    showError(error);
  } finally {
    setBusy(false);
  }
}

async function copyText(value, successMessage) {
  try {
    await navigator.clipboard.writeText(value);
    toast(successMessage);
  } catch {
    toast("Clipboard access was blocked by your browser.");
  }
}

function clipboardPng(url) {
  if (clipboardImageCache.has(url)) return clipboardImageCache.get(url);
  const pngPromise = fetch(proxiedImageUrl(url), { cache: "force-cache" })
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

async function copyCurrentImage() {
  const original = elements.mainImage.dataset.originalUrl;
  const button = $("#copyImageButton");
  const originalLabel = button.textContent;
  try {
    if (!original) throw new Error("No image is selected");
    if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
      throw new Error("Image clipboard is unavailable");
    }
    button.disabled = true;
    button.textContent = "Copying…";
    await navigator.clipboard.write([
      new ClipboardItem({ "image/png": clipboardPng(original) }),
    ]);
    toast("Image copied to the clipboard.");
  } catch (error) {
    toast(
      error.name === "NotAllowedError"
        ? "Clipboard permission was blocked. Click Copy Image again."
        : error.message || "Image could not be copied.",
    );
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

function downloadCurrentImage() {
  const original = elements.mainImage.dataset.originalUrl;
  const link = document.createElement("a");
  link.href = proxiedImageUrl(original, true);
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

function listingValues(language) {
  return {
    title: $(`#${language}Title`).value.trim(),
    description: $(`#${language}Description`).value.trim(),
    hashtags: $(`#${language}Hashtags`).textContent.trim(),
  };
}

function listingNeedsNegotiable(listingType) {
  return listingType === "dress" || listingType === "dress_m" || listingType === "skirt" || listingType === "hat" || listingType === "mask" || listingType === "jeans" || listingType === "long_boots" || listingType === "heels" || listingType === "coat" || listingType === "jacket" || listingType === "bag" || listingType === "plant" || listingType === "shelf";
}

function cleanListingTitle(title, listingType) {
  let text = String(title || "").trim();
  if (!text || !["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "bag", "plant", "mask", "shelf"].includes(listingType)) return text;
  text = text.replace(/([A-Za-zÀ-ÿ])(?=Taille\s*:)/gi, "$1 ");
  text = text.replace(/\b(?:size|taille)\s*:?\s*(?:XXS|XS|S|M|L|XL|XXL|XXXL|[2-5]XL|\d{2,3})\b/gi, "");
  text = text.replace(/\s*(?:,?\s*with a polished silhouette|,?\s*with a flowing full-length shape|,?\s*with a refined dress shape|,?\s*with an elegant style detail|,?\s*with refined cut details and a chic feminine finish|,?\s*with elegant details and a flattering fitted look|,?\s*avec coupe raffinée et finition chic|,?\s*avec détails élégants et coupe flatteuse|,?\s*for evening and occasion outfits|,?\s*for occasion outfit styling)\s*/gi, " ");
  text = text.replace(/\s*,?\s*avec coupe raffin\S*e et finition chic\s*/gi, " ");
  text = text.replace(/\s*,?\s*avec d\S*tails \S*l\S*gants et coupe flatteuse\s*/gi, " ");
  text = text.replace(/\bstyle\s+elegant\b/gi, "style élégant");
  text = text.replace(/\s+/g, " ").replace(/\s+,/g, ",").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
  const isFrench = /\brobe\b|\bjupe\b|\bjean\b|\bbottes?\b|\bsandales?\b|\bescarpins?\b|\btalons?\b|\bmanteau\b|\bveste\b|\btrench\b|\bsac\b|\bmasque\b|\bétagère\b|\betagere\b|\btaille\b|\bélégant\b|\belegant\b/i.test(text)
    && !/\bdress\b|\bskirt\b|\bjeans\b|\bboots\b|\bheels\b|\bsandals\b|\bcoat\b|\bjacket\b|\bbag\b|\bhandbag\b|\bmask\b|\bshel(?:f|ves)\b/i.test(text);
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
  if (listingType === "bag" || listingType === "plant") {
    text = text.replace(/\b(?:one\s+size\s+fits\s+all|taille\s+unique)\b/gi, "").trim();
    text = text.replace(/\b(?:faux\s*leather|fauxleather|simili\s*cuir|leather|cuir|suede|daim)\b/gi, "").trim();
    text = text.replace(/\s+/g, " ").replace(/^[,;:\s-]+|[,;:\s-]+$/g, "");
    return text.slice(0, 100).replace(/[,;:\s-]+$/g, "").trim();
  }
  const suffix = listingType === "jeans"
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
  if (["english", "french"].includes(language) && ["dress", "dress_m", "skirt", "jeans", "long_boots", "heels", "coat", "jacket", "bag", "plant", "mask", "shelf"].includes(listingType)) {
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

function fullListing(language) {
  const values = listingValues(language);
  const condition = language === "english" ? "Perfect condition." : "Parfait état.";
  const listingType = state.product?.listing_type || state.product?.additional_details?.item_type || "dress";
  const withNegotiable = ensureNegotiableDescription(
    values.description,
    listingType,
    language,
  );
  const description = withNegotiable.includes(condition)
    ? withNegotiable
    : `${withNegotiable}\n\n${condition}`;
  const isStyleToken = ["skirt", "hat", "mask", "jeans", "bag", "coat", "jacket", "plant"].includes(state.product?.listing_type);
  const outputValue = (
    isStyleToken && state.product?.fictional_brand === "oldmoney"
      ? "old money"
      : isStyleToken && state.product?.fictional_brand === "wildwest"
        ? "Wild West"
      : state.product?.fictional_brand
  );
  const brand = outputValue
    ? `${isStyleToken ? "Clothing style" : "Fictional brand"}: ${outputValue}`
    : "";
  return [values.title, description, values.hashtags, brand]
    .filter(Boolean)
    .join("\n\n");
}

function historyImage(product) {
  if (!product.main_image_url) return `<div class="history-card-image placeholder">—</div>`;
  return `<img class="history-card-image" src="${escapeHtml(proxiedImageUrl(product.main_image_url))}" alt="" loading="lazy">`;
}

function renderHistory(items) {
  elements.historyGrid.replaceChildren();
  elements.historyEmpty.hidden = items.length > 0;
  elements.deleteAllHistory.disabled = items.length === 0;
  items.forEach((product) => {
    const card = document.createElement("article");
    card.className = "history-card";
    const date = new Date(product.last_updated_date).toLocaleDateString(undefined, {
      month: "short", day: "numeric", year: "numeric",
    });
    card.innerHTML = `
      ${historyImage(product)}
      <div class="history-card-body">
        <span class="history-status ${product.extraction_status === "success" ? "" : "failed"}">${escapeHtml(product.extraction_status)}</span>
        <h3>${escapeHtml(product.title || product.sku)}</h3>
        <p>${escapeHtml(product.sku)} · ${escapeHtml(date)}</p>
        <div class="history-card-actions">
          <button class="open-history" type="button">Open</button>
          <button class="refresh-history" type="button">Refresh</button>
          <button class="delete-history" type="button">Delete</button>
        </div>
      </div>`;
    $(".open-history", card).addEventListener("click", () => {
      if (product.extraction_status === "success") renderProduct(product);
      else extractSku(product.sku);
    });
    $(".refresh-history", card).addEventListener("click", async () => {
      state.product = product;
      await refreshProduct();
    });
    $(".delete-history", card).addEventListener("click", async () => {
      if (!window.confirm(`Delete ${product.sku} from local history?`)) return;
      try {
        await api(`/api/products/${product.id}`, { method: "DELETE" });
        if (state.product?.id === product.id) {
          state.product = null;
          elements.productSection.hidden = true;
        }
        await loadHistory();
        toast("Product and saved screenshots deleted.");
      } catch (error) {
        showError(error);
      }
    });
    elements.historyGrid.append(card);
  });
}

async function loadHistory() {
  try {
    const response = await api("/api/products?limit=50");
    renderHistory(response.items);
  } catch {
    toast("Local history could not be loaded.");
  }
}

async function deleteAllProducts() {
  if (!window.confirm("Delete every saved product? This cannot be undone.")) return;
  elements.deleteAllHistory.disabled = true;
  try {
    await api("/api/products", { method: "DELETE" });
    state.product = null;
    elements.productSection.hidden = true;
    await loadHistory();
    toast("All products and saved screenshots were deleted.");
  } catch (error) {
    elements.deleteAllHistory.disabled = false;
    toast(error.message || "Products could not be deleted.");
  }
}

async function checkHealth() {
  const pill = $("#healthPill");
  try {
    await api("/health");
    pill.classList.add("online");
    $("#healthText").textContent = "Server ready";
  } catch {
    pill.classList.add("offline");
    $("#healthText").textContent = "Server offline";
  }
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  extractSku(elements.sku.value);
});
elements.retry.addEventListener("click", () => extractSku(state.lastSku));
$("#refreshButton").addEventListener("click", refreshProduct);
elements.generate.addEventListener("click", generateDescription);
$$(".regenerate-button").forEach((button) => button.addEventListener("click", generateDescription));
$("#copyImageButton").addEventListener("click", copyCurrentImage);
$("#downloadImageButton").addEventListener("click", downloadCurrentImage);
$("#copyImageUrlButton").addEventListener("click", () => copyText(elements.mainImage.dataset.originalUrl, "Image URL copied."));
$("#openImageButton").addEventListener("click", () => window.open(elements.mainImage.dataset.originalUrl, "_blank", "noopener"));
$("#openProductButton").addEventListener("click", () => window.open(state.product?.product_url, "_blank", "noopener"));
$("#reloadHistoryButton").addEventListener("click", loadHistory);
elements.deleteAllHistory.addEventListener("click", deleteAllProducts);

$$(".language-panel").forEach((panel) => {
  const language = panel.dataset.language;
  $(".copy-title", panel).addEventListener("click", () => copyText(listingValues(language).title, "Title copied."));
  $(".copy-description", panel).addEventListener("click", () => copyText(listingValues(language).description, "Description copied."));
  $(".copy-full", panel).addEventListener("click", () => copyText(fullListing(language), "Full listing copied."));
});

checkHealth();
loadHistory();
