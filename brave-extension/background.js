const JOB_PREFIX = "temu-job-tab:";
const DOTB_DRAFT_KEY = "dotb-pending-draft";
const DOTB_NEW_ITEM_URL = "https://dotb.io/dashboard/vinted/items/new";
const ALLOWED_API_BASES = new Set([
  "http://127.0.0.1:8000",
  "http://localhost:8000",
]);

function jobKey(tabId) {
  return `${JOB_PREFIX}${tabId}`;
}

function isTemuProductUrl(value) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    const path = decodeURIComponent(url.pathname).toLowerCase();
    return url.protocol === "https:"
      && (host === "temu.com" || host.endsWith(".temu.com"))
      && (
        path.includes("goods.html")
        || path.includes("/goods/")
        || path.includes("-g-")
      );
  } catch {
    return false;
  }
}

function safeText(value, maximum) {
  return String(value || "").trim().slice(0, maximum);
}

function safeLocalImageUrl(value) {
  try {
    const url = new URL(value);
    return ALLOWED_API_BASES.has(url.origin)
      && url.pathname.startsWith("/downloads/")
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function safeApiBase(value) {
  try {
    const origin = new URL(value).origin;
    return ALLOWED_API_BASES.has(origin) ? origin : null;
  } catch {
    return null;
  }
}

function safeProductId(value) {
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

function safeProductUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" ? url.href.slice(0, 2048) : "";
  } catch {
    return "";
  }
}

function sanitizeDotbProducts(products) {
  return products.slice(0, 20).map((product) => ({
    id: safeProductId(product.id),
    sku: safeText(product.sku, 100),
    title: safeText(product.title, 100),
    description: safeText(product.description, 2000),
    price: safeText(product.price, 30),
    itemType: safeText(product.itemType, 30),
    brand: safeText(product.brand, 100),
    colour: safeText(product.colour, 100),
    category: safeText(product.category, 100),
    productUrl: safeProductUrl(product.productUrl),
    images: (Array.isArray(product.images) ? product.images : [])
      .map(safeLocalImageUrl)
      .filter(Boolean)
      .slice(0, 6),
  })).filter((product) => (
    product.sku
    && product.title.length >= 5
    && product.description.length >= 5
    && product.price
  ));
}

async function startDotbDraft(message) {
  const products = sanitizeDotbProducts(
    Array.isArray(message.products) ? message.products : [],
  );
  const apiBase = safeApiBase(message.apiBase);
  if (!products.length) {
    throw new Error("No complete products were supplied for Dotb.");
  }
  if (!apiBase) {
    throw new Error("The local Product Extractor address is not permitted.");
  }
  await chrome.storage.local.set({
    [DOTB_DRAFT_KEY]: {
      products,
      apiBase,
      createdAt: Date.now(),
      currentIndex: 0,
      limit: products.length,
      completedIndices: [],
    },
  });
  const tabs = await chrome.tabs.query({
    url: [
      "https://dotb.io/dashboard/*",
      "https://www.dotb.io/dashboard/*",
    ],
  });
  const tab = tabs[0];
  if (tab?.id) {
    await chrome.tabs.update(tab.id, {
      url: DOTB_NEW_ITEM_URL,
      active: true,
    });
    return { ok: true, tabId: tab.id };
  }
  const created = await chrome.tabs.create({
    url: DOTB_NEW_ITEM_URL,
    active: true,
  });
  return { ok: true, tabId: created.id };
}

async function getDotbDraft() {
  const stored = await chrome.storage.local.get(DOTB_DRAFT_KEY);
  const draft = stored[DOTB_DRAFT_KEY] || null;
  if (draft && Date.now() - Number(draft.createdAt || 0) > 12 * 60 * 60 * 1000) {
    await chrome.storage.local.remove(DOTB_DRAFT_KEY);
    return null;
  }
  return draft;
}

async function updateDotbDraft(message) {
  const draft = await getDotbDraft();
  if (!draft) throw new Error("The Dotb draft session has expired.");
  const products = Array.isArray(message.products)
    ? sanitizeDotbProducts(message.products)
    : draft.products;
  const maximum = products.length;
  if (!maximum) {
    await chrome.storage.local.remove(DOTB_DRAFT_KEY);
    return null;
  }
  const currentIndex = Math.max(
    0,
    Math.min(maximum - 1, Number(message.currentIndex ?? draft.currentIndex ?? 0)),
  );
  const limit = Math.max(
    currentIndex + 1,
    Math.min(maximum, Number(message.limit ?? draft.limit ?? maximum)),
  );
  const completedIndices = [...new Set(
    (Array.isArray(message.completedIndices)
      ? message.completedIndices
      : draft.completedIndices || [])
      .map(Number)
      .filter((index) => Number.isInteger(index) && index >= 0 && index < maximum),
  )];
  const updated = { ...draft, products, currentIndex, limit, completedIndices };
  await chrome.storage.local.set({ [DOTB_DRAFT_KEY]: updated });
  return updated;
}

async function fetchDotbImage(value) {
  const imageUrl = safeLocalImageUrl(value);
  if (!imageUrl) throw new Error("Dotb can only upload locally saved product images.");
  const response = await fetch(imageUrl);
  if (!response.ok) throw new Error("A product image could not be loaded.");
  const buffer = await response.arrayBuffer();
  if (buffer.byteLength > 15 * 1024 * 1024) {
    throw new Error("A product image is larger than 15 MB.");
  }
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return {
    base64: btoa(binary),
    mimeType: response.headers.get("content-type") || "image/jpeg",
    filename: decodeURIComponent(
      new URL(imageUrl).pathname.split("/").pop() || "product.jpg",
    ),
  };
}

async function deleteDotbProduct(message) {
  const draft = await getDotbDraft();
  const apiBase = safeApiBase(message.apiBase || draft?.apiBase);
  const productId = safeProductId(message.productId);
  if (!apiBase) {
    throw new Error("The local Product Extractor address is not permitted.");
  }
  if (!productId) {
    throw new Error("This Dotb product has no local Product Extractor ID.");
  }
  const response = await fetch(`${apiBase}/api/products/${productId}`, {
    method: "DELETE",
  });
  if (response.status === 404) {
    await notifyDotbProductDeleted(productId, message.sku || "");
    return { ok: true, alreadyDeleted: true };
  }
  if (!response.ok) {
    throw new Error(`Product Extractor returned HTTP ${response.status}.`);
  }
  await notifyDotbProductDeleted(productId, message.sku || "");
  return { ok: true };
}

async function notifyDotbProductDeleted(productId, sku) {
  const tabs = await chrome.tabs.query({
    url: [
      "http://127.0.0.1:8000/*",
      "http://localhost:8000/*",
    ],
  });
  await Promise.allSettled(
    tabs
      .filter((tab) => tab.id)
      .map((tab) => chrome.tabs.sendMessage(tab.id, {
        type: "dotb-product-deleted",
        productId,
        sku,
      })),
  );
}

async function api(job, suffix, options = {}) {
  const response = await fetch(
    `${job.apiBase}/api/products/temu-extension/jobs/${job.jobId}${suffix}`,
    {
      method: options.method || "POST",
      headers: { "Content-Type": "application/json" },
      body: options.body ? JSON.stringify(options.body) : undefined,
    },
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = payload.error || {};
    throw new Error(error.message || `Local app returned HTTP ${response.status}.`);
  }
  return payload;
}

async function saveJob(tabId, job) {
  await chrome.storage.session.set({ [jobKey(tabId)]: job });
}

async function getJob(tabId) {
  const stored = await chrome.storage.session.get(jobKey(tabId));
  return stored[jobKey(tabId)] || null;
}

async function removeJob(tabId) {
  await chrome.storage.session.remove(jobKey(tabId));
}

async function findJobTab(jobId) {
  const stored = await chrome.storage.session.get(null);
  for (const [key, job] of Object.entries(stored)) {
    if (key.startsWith(JOB_PREFIX) && job?.jobId === jobId) {
      return Number(key.slice(JOB_PREFIX.length));
    }
  }
  return null;
}

async function sendProgress(job, phase, message) {
  return api(job, "/progress", {
    body: { token: job.token, phase, message },
  });
}

async function failJob(tabId, job, code, message, retryable = false) {
  try {
    await api(job, "/fail", {
      body: { token: job.token, code, message, retryable },
    });
  } finally {
    await removeJob(tabId);
  }
}

async function startJob(message) {
  if (!ALLOWED_API_BASES.has(message.apiBase)) {
    throw new Error("The local Product Extractor address is not permitted.");
  }
  const existingTabId = await findJobTab(message.jobId);
  if (existingTabId !== null) {
    await chrome.tabs.update(existingTabId, { active: false });
    return { ok: true, tabId: existingTabId };
  }

  const seed = {
    jobId: message.jobId,
    apiBase: message.apiBase,
  };
  const claimed = await api(seed, "/claim");
  const job = {
    ...seed,
    token: claimed.token,
    sku: claimed.sku,
    itemType: claimed.item_type,
    phase: claimed.product_url ? "product" : "search",
    createdAt: Date.now(),
  };
  const tab = await chrome.tabs.create({
    url: claimed.product_url || claimed.search_url,
    active: false,
  });
  await saveJob(tab.id, job);
  return { ok: true, tabId: tab.id };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message?.type === "start-dotb-draft") {
      sendResponse(await startDotbDraft(message));
      return;
    }
    if (message?.type === "dotb-get-draft") {
      sendResponse({ ok: true, draft: await getDotbDraft() });
      return;
    }
    if (message?.type === "dotb-fetch-image") {
      sendResponse({ ok: true, image: await fetchDotbImage(message.url) });
      return;
    }
    if (message?.type === "dotb-delete-product") {
      sendResponse(await deleteDotbProduct(message));
      return;
    }
    if (message?.type === "dotb-update-draft") {
      sendResponse({ ok: true, draft: await updateDotbDraft(message) });
      return;
    }
    if (message?.type === "dotb-clear-draft") {
      await chrome.storage.local.remove(DOTB_DRAFT_KEY);
      sendResponse({ ok: true });
      return;
    }
    if (message?.type === "start-temu-job") {
      sendResponse(await startJob(message));
      return;
    }

    const tabId = sender.tab?.id;
    if (!tabId) {
      sendResponse({ ok: false });
      return;
    }
    const job = await getJob(tabId);
    if (!job) {
      sendResponse({ ok: false });
      return;
    }

    if (message.type === "temu-page-ready") {
      sendResponse({ ok: true, job });
      return;
    }
    if (message.type === "temu-progress") {
      await sendProgress(job, message.phase, message.message);
      sendResponse({ ok: true });
      return;
    }
    if (message.type === "temu-needs-attention") {
      await sendProgress(
        job,
        "searching",
        message.message || "Complete the visible Temu check in Brave.",
      );
      await chrome.tabs.update(tabId, { active: true });
      sendResponse({ ok: true });
      return;
    }
    if (message.type === "temu-open-product") {
      const fromSearch = job.phase === "search";
      const fromSoldOut = (
        job.phase === "product"
        && !job.soldOutRecovery
        && message.reason === "sold_out_similar"
      );
      if ((!fromSearch && !fromSoldOut) || !isTemuProductUrl(message.url)) {
        await failJob(
          tabId,
          job,
          "temu_result_ambiguous",
          "Temu did not provide one safe exact product result.",
          false,
        );
        sendResponse({ ok: false });
        return;
      }
      job.phase = "product";
      if (fromSoldOut) job.soldOutRecovery = true;
      await saveJob(tabId, job);
      await sendProgress(
        job,
        "opening_product",
        fromSoldOut
          ? "The first Temu listing was sold out. Opening an in-stock similar item."
          : "Opening the exact Temu product.",
      );
      await chrome.tabs.update(tabId, { url: message.url, active: false });
      sendResponse({ ok: true });
      return;
    }
    if (message.type === "temu-complete") {
      if (job.phase !== "product" || !isTemuProductUrl(message.payload?.product_url)) {
        await failJob(
          tabId,
          job,
          "temu_wrong_product",
          "The opened page was not a valid Temu product.",
          false,
        );
        sendResponse({ ok: false });
        return;
      }
      await sendProgress(job, "downloading_images", "Saving all Temu product images.");
      await api(job, "/complete", {
        body: { token: job.token, ...message.payload },
      });
      await removeJob(tabId);
      sendResponse({ ok: true });
      setTimeout(() => chrome.tabs.remove(tabId).catch(() => {}), 700);
      return;
    }
    if (message.type === "temu-fail") {
      await failJob(
        tabId,
        job,
        message.code || "temu_extraction_failed",
        message.message || "Temu extraction failed.",
        Boolean(message.retryable),
      );
      sendResponse({ ok: true });
      return;
    }
    sendResponse({ ok: false });
  })().catch(async (error) => {
    const tabId = sender.tab?.id;
    const job = tabId ? await getJob(tabId).catch(() => null) : null;
    if (tabId && job) {
      await failJob(
        tabId,
        job,
        "temu_extension_error",
        error.message || "The Brave extension stopped unexpectedly.",
        true,
      ).catch(() => {});
    }
    sendResponse({ ok: false, error: error.message });
  });
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  getJob(tabId).then((job) => {
    if (!job) return;
    return failJob(
      tabId,
      job,
      "temu_tab_closed",
      "The Temu tab was closed before extraction finished.",
      true,
    );
  }).catch(() => {});
});
