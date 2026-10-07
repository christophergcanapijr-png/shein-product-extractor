(() => {
  const START_EVENT = "product-extractor:temu-start";
  const READY_EVENT = "product-extractor:temu-extension-ready";
  const DOTB_START_EVENT = "product-extractor:dotb-start";
  const DOTB_READY_EVENT = "product-extractor:dotb-extension-ready";

  function sendMessage(message) {
    try {
      if (!chrome.runtime?.id) {
        delete document.documentElement.dataset.temuExtractorExtension;
        delete document.documentElement.dataset.dotbExtractorExtension;
        return;
      }
      const pending = chrome.runtime.sendMessage(message);
      if (pending && typeof pending.catch === "function") {
        pending.catch(() => {});
      }
    } catch (_) {
      // Reloading an unpacked extension invalidates scripts already injected
      // into open tabs. The page only needs a refresh to receive the new one.
      delete document.documentElement.dataset.temuExtractorExtension;
      delete document.documentElement.dataset.dotbExtractorExtension;
    }
  }

  document.documentElement.dataset.temuExtractorExtension = "ready";
  document.documentElement.dataset.dotbExtractorExtension = "ready";
  window.dispatchEvent(new CustomEvent(READY_EVENT));
  window.dispatchEvent(new CustomEvent(DOTB_READY_EVENT));

  window.addEventListener(START_EVENT, (event) => {
    const jobId = String(event.detail?.jobId || "");
    const apiBase = String(event.detail?.apiBase || window.location.origin);
    if (!/^[a-f0-9]{32}$/i.test(jobId)) return;
    sendMessage({
      type: "start-temu-job",
      jobId,
      apiBase,
    });
  });

  window.addEventListener(DOTB_START_EVENT, (event) => {
    const products = Array.isArray(event.detail?.products)
      ? event.detail.products
      : [];
    if (!products.length || products.length > 20) return;
    sendMessage({
      type: "start-dotb-draft",
      products,
      apiBase: window.location.origin,
    });
  });

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.type !== "dotb-product-deleted") return;
    window.dispatchEvent(new CustomEvent("product-extractor:dotb-product-deleted", {
      detail: {
        productId: message.productId,
        sku: message.sku || "",
      },
    }));
  });
})();
