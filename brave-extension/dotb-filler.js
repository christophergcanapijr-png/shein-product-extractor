(() => {
  const PANEL_ID = "product-extractor-dotb-panel";
  const send = (message) => chrome.runtime.sendMessage(message);

  function visible(element) {
    if (!element) return false;
    const style = getComputedStyle(element);
    return style.display !== "none"
      && style.visibility !== "hidden"
      && element.getClientRects().length > 0;
  }

  function accountSearchInput() {
    return [...document.querySelectorAll(
      'input[cmdk-input], input[data-slot="command-input"], input[role="combobox"]',
    )].find((input) => (
      visible(input)
      && /search accounts|rechercher.*compte/i.test(
        input.getAttribute("placeholder") || "",
      )
    )) || null;
  }

  function accountControl() {
    const label = [...document.querySelectorAll("span, label")].find((element) => (
      visible(element)
      && /^account$/i.test(String(element.textContent || "").trim())
    ));
    const row = label?.parentElement;
    return row?.querySelector(
      'select, button[role="combobox"], button[data-slot="popover-trigger"], [role="combobox"]',
    ) || null;
  }

  function accountIsSelected() {
    const control = accountControl();
    if (!control) return false;
    const value = control instanceof HTMLSelectElement
      ? control.value
      : String(control.innerText || control.getAttribute("data-value") || "").trim();
    return Boolean(value)
      && !/select.*account|choose.*account|choisir.*compte/i.test(value);
  }

  function accountTrigger() {
    const directControl = accountControl();
    if (directControl && visible(directControl)) return directControl;
    const selectors = [
      'button[data-slot="popover-trigger"]',
      'button[aria-haspopup="dialog"]',
      'button[role="combobox"]',
      '[role="combobox"][aria-expanded="false"]',
    ];
    const candidates = [...document.querySelectorAll(selectors.join(","))]
      .filter(visible);
    const named = candidates.find((element) => /account|compte/i.test([
      element.innerText,
      element.getAttribute("aria-label"),
      element.getAttribute("title"),
      element.getAttribute("data-placeholder"),
    ].filter(Boolean).join(" ")));
    if (named) return named;
    const unopened = candidates.filter(
      (element) => element.getAttribute("aria-expanded") !== "true",
    );
    return unopened.length === 1 ? unopened[0] : null;
  }

  function accountOptionFromEvent(event) {
    const option = event.target.closest(
      '[cmdk-item][role="option"], [data-slot="command-item"][role="option"]',
    );
    if (!option) return null;
    const dialog = option.closest('[role="dialog"], [data-slot="popover-content"]');
    const search = dialog?.querySelector(
      'input[cmdk-input], input[data-slot="command-input"]',
    );
    return /search accounts|rechercher.*compte/i.test(
      search?.getAttribute("placeholder") || "",
    ) ? option : null;
  }

  function fieldHint(element) {
    const labels = element.labels
      ? [...element.labels].map((label) => label.innerText)
      : [];
    return [
      element.getAttribute("name"),
      element.id,
      element.getAttribute("placeholder"),
      element.getAttribute("aria-label"),
      element.getAttribute("data-testid"),
      element.closest("label")?.innerText,
      ...labels,
    ].filter(Boolean).join(" ").toLowerCase();
  }

  function fieldsMatching(
    tokens,
    selector = "input, textarea, [contenteditable='true']",
  ) {
    return [...document.querySelectorAll(selector)].filter((element) => {
      if (element.matches("input[type='file'], input[type='hidden']")) return false;
      if (!visible(element)) return false;
      const hint = fieldHint(element);
      return tokens.some((token) => hint.includes(token));
    });
  }

  function uniqueFields(tokens, fallbackSelector = null) {
    const matched = fieldsMatching(tokens);
    if (matched.length) return [...new Set(matched)];
    return fallbackSelector
      ? [...document.querySelectorAll(fallbackSelector)].filter(visible)
      : [];
  }

  function setFieldValue(element, value, shouldBlur = true) {
    element.focus();
    if (element.isContentEditable) {
      element.textContent = value;
    } else {
      const prototype = element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      if (setter) setter.call(element, value);
      else element.value = value;
    }
    element.dispatchEvent(new InputEvent("input", {
      bubbles: true,
      inputType: "insertText",
      data: value,
    }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    if (shouldBlur) element.blur();
  }

  async function fillSupplierLink(product) {
    const url = String(product?.productUrl || "").trim();
    if (!url) {
      console.warn(
        "[Product Extractor] No supplier link URL on this product — "
        + "product.productUrl was empty.",
        product,
      );
      return false;
    }
    // Dotb's form can still be re-rendering (e.g. right after category
    // selection) when this runs, so give the field a few short chances to
    // mount before giving up.
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const field = discoverFields().supplierLinks[0];
      if (field) {
        setFieldValue(field, url);
        console.info("[Product Extractor] Supplier link filled:", url);
        return true;
      }
      await delay(200);
    }
    console.warn(
      "[Product Extractor] Supplier link field (input[name=\"supplier_link\"]) "
      + "was not found or not visible on Dotb's page after 6 retries.",
    );
    return false;
  }

  function decodeFile(image) {
    const binary = atob(image.base64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return new File([bytes], image.filename, { type: image.mimeType });
  }

  async function uploadImages(input, urls) {
    const transfer = new DataTransfer();
    for (const url of urls) {
      const response = await send({ type: "dotb-fetch-image", url });
      if (!response?.ok || !response.image) {
        throw new Error("One of the saved product images could not be opened.");
      }
      transfer.items.add(decodeFile(response.image));
    }
    input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function discoverFields() {
    return {
      files: [...document.querySelectorAll("input[type='file']")],
      titles: uniqueFields([
        "title",
        "titre",
        "name of item",
        "nom de l'article",
      ]),
      descriptions: uniqueFields(["description"], "textarea"),
      prices: uniqueFields(["price", "prix"]),
      skus: uniqueFields([
        "sku",
        "stock keeping",
        "reference",
        "référence",
      ]),
      supplierLinks: [
        ...document.querySelectorAll('input[name="supplier_link"]'),
        ...uniqueFields(["supplier_link", "supplier link", "lien fournisseur"]),
      ].filter((element, index, items) => (
        visible(element) && items.indexOf(element) === index
      )),
    };
  }

  function categoryButton() {
    const label = [...document.querySelectorAll("span")].find((element) => (
      visible(element)
      && /^category$/i.test(String(element.textContent || "").trim())
    ));
    const button = label?.parentElement?.querySelector("button");
    if (button && visible(button)) return button;
    return [...document.querySelectorAll("button")].find((element) => (
      visible(element)
      && /select a category|choisir.*catégorie/i.test(element.innerText || "")
    )) || null;
  }

  function categoryIsSelected() {
    const button = categoryButton();
    if (!button) return false;
    const text = String(button.innerText || "").trim();
    return Boolean(text)
      && !/select a category|choisir.*catégorie/i.test(text);
  }

  function fieldControl(labelName) {
    const normalizedName = labelName.toLowerCase();
    const label = [...document.querySelectorAll("span, label")].find((element) => (
      visible(element)
      && String(element.textContent || "").trim().toLowerCase() === normalizedName
    ));
    return label?.parentElement?.querySelector('button, [role="combobox"]') || null;
  }

  function normalizedText(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function priceEndingInNinety(value) {
    const normalized = String(value || "")
      .replace(",", ".")
      .replace(/[^0-9.-]/g, "");
    const amount = Number(normalized);
    return Number.isFinite(amount)
      ? `${Math.floor(amount)}.90`
      : String(value || "");
  }

  const delay = (milliseconds) => new Promise(
    (resolve) => setTimeout(resolve, milliseconds),
  );

  async function waitForPopover(trigger, timeout = 2500) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const controlledId = trigger.getAttribute("aria-controls");
      const controlled = controlledId ? document.getElementById(controlledId) : null;
      if (visible(controlled)) return controlled;
      const dialogs = [...document.querySelectorAll(
        '[role="dialog"][data-state="open"], [data-slot="popover-content"][data-state="open"]',
      )].filter(visible);
      if (dialogs.length === 1) return dialogs[0];
      await delay(80);
    }
    return null;
  }

  function choiceCandidates(container) {
    return [...container.querySelectorAll([
      '[cmdk-item][role="option"]',
      '[data-slot="command-item"][role="option"]',
      '[role="option"]',
      "label",
      "button:not([role='checkbox'])",
    ].join(","))].filter(visible);
  }

  function choiceText(candidate) {
    const named = candidate.querySelector(
      'p.font-semibold, [data-slot="command-item"] span, .font-medium, .font-semibold',
    );
    return normalizedText(named?.innerText || candidate.innerText);
  }

  async function selectSingleAttribute(labelName, choices, fallbackChoices = []) {
    const trigger = fieldControl(labelName);
    if (!trigger) return false;
    const normalizedChoices = choices.map(normalizedText).filter(Boolean);
    const current = normalizedText(trigger.innerText);
    if (normalizedChoices.includes(current)) return true;
    trigger.click();
    const popover = await waitForPopover(trigger);
    if (!popover) return false;

    const search = popover.querySelector(
      [
        'input[cmdk-input]',
        'input[data-slot="command-input"]',
        'input[type="search"]',
        'input[role="combobox"]',
        'input[placeholder*="search" i]',
        'input[placeholder*="brand" i]',
      ].join(","),
    );
    if (search && choices[0]) {
      setFieldValue(search, choices[0], false);
      await delay(350);
    }
    let candidates = choiceCandidates(popover);
    let selected = null;
    for (const choice of normalizedChoices) {
      selected = candidates.find((candidate) => choiceText(candidate) === choice)
        || candidates.find((candidate) => choiceText(candidate).startsWith(`${choice} `))
        || candidates.find((candidate) => (
          choiceText(candidate).includes(choice)
          && /\b(add|create|use)\b/.test(choiceText(candidate))
        ));
      if (selected) break;
    }
    if (!selected) {
      for (const fallback of fallbackChoices.map(normalizedText)) {
        if (search) {
          setFieldValue(search, fallback, false);
          await delay(350);
        }
        candidates = choiceCandidates(popover);
        selected = candidates.find((candidate) => choiceText(candidate) === fallback)
          || candidates.find((candidate) => choiceText(candidate).startsWith(`${fallback} `));
        if (selected) break;
      }
    }
    if (!selected) {
      const currentTrigger = fieldControl(labelName);
      if (currentTrigger?.getAttribute("aria-expanded") === "true") {
        currentTrigger.click();
      }
      return false;
    }
    selected.click();
    await delay(350);
    const updated = fieldControl(labelName);
    const updatedText = normalizedText(updated?.innerText);
    return normalizedChoices.some((choice) => updatedText === choice)
      || fallbackChoices.map(normalizedText).some((choice) => updatedText === choice)
      || !/\b(select|choose)\b/.test(updatedText);
  }

  const COLOUR_RULES = [
    ["Black", /\b(black|noir|noire)\b/],
    ["Grey", /\b(grey|gray|gris|grise)\b/],
    ["White", /\b(white|blanc|blanche)\b/],
    ["Cream", /\b(cream|creme|ecru|ivory|ivoire)\b/],
    ["Beige", /\bbeige\b/],
    ["Apricot", /\b(apricot|abricot)\b/],
    ["Orange", /\borange\b/],
    ["Coral", /\b(coral|corail)\b/],
    ["Red", /\b(red|rouge)\b/],
    ["Burgundy", /\b(burgundy|bordeaux)\b/],
    ["Pink", /\b(pink|rose vif|fuchsia)\b/],
    ["Rose", /\b(blush|rose|rose pale|pale pink)\b/],
    ["Purple", /\b(purple|violet|violette)\b/],
    ["Lilac", /\b(lilac|lilas|lavender|lavande)\b/],
    ["Light blue", /\b(light blue|sky blue|bleu clair|bleu ciel)\b/],
    ["Navy", /\b(navy|navy blue|bleu marine)\b/],
    ["Turquoise", /\b(turquoise|teal)\b/],
    ["Blue", /\b(blue|bleu|bleue)\b/],
    ["Mint", /\b(mint|menthe)\b/],
    ["Dark green", /\b(dark green|forest green|vert fonce)\b/],
    ["Green", /\b(green|vert|verte)\b/],
    ["Khaki", /\b(khaki|kaki)\b/],
    ["Brown", /\b(brown|marron|brun|brune)\b/],
    ["Mustard", /\b(mustard|moutarde)\b/],
    ["Yellow", /\b(yellow|jaune)\b/],
    ["Silver", /\b(silver|argente|argent)\b/],
    ["Gold", /\b(gold|golden|dore|or)\b/],
    ["Multi", /\b(multi|multicolor|multicolore)\b/],
    ["Clear", /\b(clear|transparent|translucent)\b/],
  ];

  function inferDotbColours(product) {
    function matchedColours(source) {
      return COLOUR_RULES
        .filter(([, pattern]) => pattern.test(source))
        .map(([name]) => name);
    }
    let colours = matchedColours(normalizedText(product.title));
    if (!colours.length) {
      colours = matchedColours(normalizedText(product.colour));
    }
    if (colours.includes("Light blue") || colours.includes("Navy")) {
      colours = colours.filter((colour) => colour !== "Blue");
    }
    if (colours.includes("Dark green")) {
      colours = colours.filter((colour) => colour !== "Green");
    }
    return colours.slice(0, 2);
  }

  async function selectColours(colours) {
    if (!colours.length) return false;
    const trigger = fieldControl("Colours");
    if (!trigger) return false;
    trigger.click();
    const popover = await waitForPopover(trigger);
    if (!popover) return false;
    let selectedCount = 0;
    for (const colour of colours.slice(0, 2)) {
      const target = [...popover.querySelectorAll("label")].find(
        (label) => normalizedText(label.innerText) === normalizedText(colour),
      );
      const checkbox = target?.querySelector('[role="checkbox"]');
      if (checkbox && checkbox.getAttribute("aria-checked") !== "true") {
        checkbox.click();
        selectedCount += 1;
        await delay(120);
      } else if (checkbox) {
        selectedCount += 1;
      }
    }
    if (trigger.getAttribute("aria-expanded") === "true") trigger.click();
    return selectedCount > 0;
  }

  function autoRestockSwitch() {
    const label = [...document.querySelectorAll("span, p, label")].find((element) => (
      visible(element)
      && normalizedText(element.textContent) === "auto restock"
    ));
    let container = label?.parentElement;
    for (let depth = 0; container && depth < 5; depth += 1) {
      const control = container.querySelector('button[role="switch"]');
      if (control && visible(control)) return control;
      container = container.parentElement;
    }
    return null;
  }

  async function enableAutoRestock() {
    const control = autoRestockSwitch();
    if (!control) return false;
    if (control.getAttribute("aria-checked") !== "true") {
      control.click();
      await delay(250);
    }
    return autoRestockSwitch()?.getAttribute("aria-checked") === "true";
  }

  function actionButton(label) {
    return [...document.querySelectorAll("button")].find((element) => (
      visible(element)
      && String(element.innerText || "").trim() === label
    )) || null;
  }

  function panel() {
    let root = document.getElementById(PANEL_ID);
    if (root) return root;
    root = document.createElement("aside");
    root.id = PANEL_ID;
    root.innerHTML = `
      <strong>Product Extractor → Dotb</strong>
      <label class="pe-dotb-quantity-label">
        Items to prepare
        <input class="pe-dotb-quantity" type="number" min="1" step="1">
      </label>
      <div class="pe-dotb-current" hidden>
        <img class="pe-dotb-current-image" alt="">
        <span class="pe-dotb-current-text"></span>
      </div>
      <p class="pe-dotb-message">Loading your batch…</p>
      <button class="pe-dotb-fill" type="button">Fill current item</button>
      <button class="pe-dotb-details" type="button" hidden>Retry details</button>
      <button class="pe-dotb-next" type="button" hidden disabled>Save draft &amp; next</button>
      <button class="pe-dotb-dismiss" type="button">Hide</button>
      <small>Saving creates Dotb drafts only. Nothing is published to Vinted.</small>`;
    const style = document.createElement("style");
    style.textContent = `
      #${PANEL_ID}{position:fixed;right:18px;bottom:18px;z-index:2147483647;width:320px;padding:16px;border:1px solid #d8d0ff;border-radius:14px;background:#fff;color:#19152b;box-shadow:0 16px 50px rgba(29,20,75,.22);font:13px/1.4 Arial,sans-serif}
      #${PANEL_ID} strong{display:block;margin-bottom:6px;font-size:15px}
      #${PANEL_ID} p{margin:6px 0 12px;color:#5e5875}
      #${PANEL_ID} .pe-dotb-quantity-label{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:10px 0;color:#5e5875;font-weight:700}
      #${PANEL_ID} .pe-dotb-quantity{width:68px;padding:6px 8px;border:1px solid #d9d5e8;border-radius:7px;background:#fff;color:#19152b}
      #${PANEL_ID} .pe-dotb-current{display:grid;grid-template-columns:58px minmax(0,1fr);gap:10px;align-items:center;margin:10px 0;padding:8px;border:1px solid #ebe6f6;border-radius:10px;background:#faf9ff}
      #${PANEL_ID} .pe-dotb-current[hidden]{display:none}
      #${PANEL_ID} .pe-dotb-current-image{width:58px;height:76px;object-fit:cover;border:1px solid #e1dbee;border-radius:8px;background:#f4f1fb}
      #${PANEL_ID} .pe-dotb-current-text{font-weight:800;color:#30284f;overflow-wrap:anywhere}
      #${PANEL_ID} button{margin:0 7px 9px 0;padding:8px 12px;border:1px solid #d9d5e8;border-radius:8px;background:#fff;color:#30284f;cursor:pointer;font-weight:700}
      #${PANEL_ID} .pe-dotb-fill,#${PANEL_ID} .pe-dotb-next{border-color:#6747ef;background:#6747ef;color:#fff}
      #${PANEL_ID} button:disabled{cursor:not-allowed;opacity:.5}
      #${PANEL_ID} small{display:block;color:#746e87;font-size:11px}
      #pe-dotb-product-picker{position:fixed;inset:0;z-index:2147483647;display:flex;align-items:center;justify-content:center;padding:24px;background:rgba(20,16,40,.56);font:14px/1.45 Arial,sans-serif}
      #pe-dotb-product-picker .pe-picker-dialog{width:min(900px,calc(100vw - 32px));max-height:min(760px,calc(100vh - 32px));display:flex;flex-direction:column;border:1px solid #ded8f7;border-radius:18px;background:#fff;color:#19152b;box-shadow:0 24px 80px rgba(20,16,40,.35);overflow:hidden}
      #pe-dotb-product-picker .pe-picker-header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 20px;border-bottom:1px solid #ebe8f5}
      #pe-dotb-product-picker h2{margin:0;font-size:20px}
      #pe-dotb-product-picker .pe-picker-close{width:34px;height:34px;border:1px solid #ded9ed;border-radius:9px;background:#fff;color:#403858;cursor:pointer;font-size:20px}
      #pe-dotb-product-picker .pe-picker-list{display:grid;gap:10px;padding:16px;overflow:auto}
      #pe-dotb-product-picker .pe-picker-product{display:grid;grid-template-columns:86px minmax(0,1fr) auto;grid-template-rows:auto auto;gap:6px 14px;width:100%;padding:12px 14px;border:1px solid #dfdaee;border-radius:12px;background:#fff;color:#19152b;text-align:left;cursor:pointer}
      #pe-dotb-product-picker .pe-picker-product:hover,#pe-dotb-product-picker .pe-picker-product:focus{border-color:#6747ef;background:#f7f5ff;outline:none}
      #pe-dotb-product-picker .pe-picker-product:disabled{border-color:#d9e5dc;background:#f4f8f5;color:#718078;cursor:not-allowed;opacity:.72}
      #pe-dotb-product-picker .pe-picker-thumb{grid-row:1/3;width:86px;height:116px;object-fit:cover;border:1px solid #e6e1f1;border-radius:10px;background:#f7f5fb}
      #pe-dotb-product-picker .pe-picker-title{font-weight:800;overflow-wrap:anywhere}
      #pe-dotb-product-picker .pe-picker-meta{color:#6747ef;font-weight:800;white-space:nowrap}
      #pe-dotb-product-picker .pe-picker-description{grid-column:2/4;display:-webkit-box;overflow:hidden;color:#5e5875;-webkit-box-orient:vertical;-webkit-line-clamp:3}
      #pe-dotb-product-picker .pe-picker-current{border-color:#a995ff;background:#faf9ff}`;
    document.documentElement.append(style, root);
    root.querySelector(".pe-dotb-dismiss").addEventListener("click", () => {
      root.remove();
    });
    return root;
  }

  function openProductPicker(products, currentIndex, completedIndices, onSelect) {
    document.getElementById("pe-dotb-product-picker")?.remove();
    const overlay = document.createElement("div");
    overlay.id = "pe-dotb-product-picker";
    const dialog = document.createElement("section");
    dialog.className = "pe-picker-dialog";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-label", "Choose an extracted product");

    const header = document.createElement("header");
    header.className = "pe-picker-header";
    const heading = document.createElement("h2");
    heading.textContent = "Choose an extracted product";
    const close = document.createElement("button");
    close.className = "pe-picker-close";
    close.type = "button";
    close.setAttribute("aria-label", "Close product picker");
    close.textContent = "×";
    header.append(heading, close);

    const list = document.createElement("div");
    list.className = "pe-picker-list";
    products.forEach((product, index) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = `pe-picker-product${index === currentIndex ? " pe-picker-current" : ""}`;
      const completed = completedIndices.includes(index);
      option.disabled = completed;
      const thumb = document.createElement("img");
      thumb.className = "pe-picker-thumb";
      thumb.alt = product.sku ? `${product.sku} preview` : "Product preview";
      thumb.loading = "lazy";
      thumb.src = product.images?.[0] || "";
      thumb.addEventListener("error", () => {
        thumb.removeAttribute("src");
      });
      const title = document.createElement("span");
      title.className = "pe-picker-title";
      title.textContent = product.title || product.sku;
      const meta = document.createElement("span");
      meta.className = "pe-picker-meta";
      meta.textContent = completed
        ? `Done · ${product.sku}`
        : `${product.sku} · ${priceEndingInNinety(product.price)}`;
      const description = document.createElement("span");
      description.className = "pe-picker-description";
      description.textContent = product.description;
      option.append(thumb, title, meta, description);
      option.addEventListener("click", () => {
        overlay.remove();
        onSelect(index);
      });
      list.append(option);
    });

    const dismiss = () => overlay.remove();
    close.addEventListener("click", dismiss);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) dismiss();
    });
    overlay.addEventListener("keydown", (event) => {
      if (event.key === "Escape") dismiss();
    });
    dialog.append(header, list);
    overlay.append(dialog);
    document.documentElement.append(overlay);
    close.focus();
  }

  async function initialize() {
    const root = panel();
    const message = root.querySelector(".pe-dotb-message");
    const fillButton = root.querySelector(".pe-dotb-fill");
    const detailsButton = root.querySelector(".pe-dotb-details");
    const nextButton = root.querySelector(".pe-dotb-next");
    const quantityInput = root.querySelector(".pe-dotb-quantity");
    const currentPreview = root.querySelector(".pe-dotb-current");
    const currentPreviewImage = root.querySelector(".pe-dotb-current-image");
    const currentPreviewText = root.querySelector(".pe-dotb-current-text");
    const response = await send({ type: "dotb-get-draft" }).catch(() => null);
    const draft = response?.draft;
    let products = draft?.products || [];
    if (!products.length) {
      message.textContent = "No batch is waiting. Start Dotb from Product Extractor.";
      return;
    }

    let currentIndex = Math.max(
      0,
      Math.min(products.length - 1, Number(draft.currentIndex || 0)),
    );
    let limit = Math.max(
      currentIndex + 1,
      Math.min(products.length, Number(draft.limit || products.length)),
    );
    let completedIndices = [...new Set(
      (draft.completedIndices || [])
        .map(Number)
        .filter((index) => Number.isInteger(index) && index >= 0 && index < products.length),
    )];
    let filling = false;
    let filled = false;
    let selectedProduct = null;
    let awaitingCategory = false;
    let attributesFilling = false;
    let attributesFilled = false;
    let attributeFailures = [];
    let waitingForFreshForm = false;
    let accountChosen = currentIndex > 0 || accountIsSelected();
    let accountPickerPrompted = currentIndex > 0;

    quantityInput.max = String(products.length);
    quantityInput.value = String(limit);
    quantityInput.disabled = currentIndex > 0;

    function setMessage(value) {
      if (message.textContent !== value) message.textContent = value;
    }

    function setCurrentPreview(product) {
      if (!product) {
        currentPreview.hidden = true;
        currentPreviewImage.removeAttribute("src");
        currentPreviewImage.alt = "";
        currentPreviewText.textContent = "";
        return;
      }
      currentPreview.hidden = false;
      currentPreviewImage.src = product.images?.[0] || "";
      currentPreviewImage.alt = `${product.sku || "Product"} preview`;
      currentPreviewText.textContent = product.sku || product.title || "Selected product";
    }

    function currentFields() {
      const fields = discoverFields();
      const ready = [
        fields.titles,
        fields.descriptions,
        fields.prices,
        fields.skus,
      ].every((items) => items.length >= 1);
      return { fields, ready };
    }

    async function autoFillAttributes(product) {
      if (attributesFilling || attributesFilled || !product) return;
      attributesFilling = true;
      attributeFailures = [];
      detailsButton.hidden = true;
      setMessage("Category selected. Filling size, condition, brand and colours…");
      try {
        await delay(350);
        const category = normalizedText(categoryButton()?.innerText);
        if (category.includes("long dress")) {
          if (!await selectSingleAttribute("Size", ["S"])) {
            attributeFailures.push("size S");
          }
        }
        if (!await selectSingleAttribute("Condition", ["Very Good", "Very good"])) {
          attributeFailures.push("Very Good condition");
        }
        if (product.brand) {
          if (!await selectSingleAttribute(
            "Brand",
            [product.brand],
            ["Other", "No brand", "Unbranded", "Sans marque"],
          )) attributeFailures.push("brand");
        } else {
          if (!await selectSingleAttribute("Brand", ["Other", "No brand"])) {
            attributeFailures.push("brand");
          }
        }
        const colours = inferDotbColours(product);
        if (colours.length && !await selectColours(colours)) {
          attributeFailures.push("colours");
        }
        const packageSelected = await selectSingleAttribute("Package size", ["Small"])
          || await selectSingleAttribute("Parcel size", ["Small"]);
        if (!packageSelected) attributeFailures.push("Small package size");
        if (!await enableAutoRestock()) attributeFailures.push("Auto-Restock");
        if (!product.productUrl) {
          attributeFailures.push("supplier link (no source URL saved on this product)");
        } else if (!await fillSupplierLink(product)) {
          attributeFailures.push("supplier link (field not found on Dotb's page)");
        }
        attributesFilled = true;
      } finally {
        attributesFilling = false;
        detailsButton.hidden = attributeFailures.length === 0;
        refreshStatus();
      }
    }

    function promptForAccount() {
      if (accountSearchInput()) {
        accountPickerPrompted = true;
        return true;
      }
      if (accountPickerPrompted || accountChosen) return false;
      const trigger = accountTrigger();
      if (!trigger) return false;
      accountPickerPrompted = true;
      trigger.click();
      return true;
    }

    async function fillCurrentItem(productIndex) {
      if (filling || waitingForFreshForm) return;
      const { fields, ready } = currentFields();
      if (!ready) {
        setMessage("Choose a Vinted account and wait for Dotb's item editor, then try again.");
        return;
      }
      currentIndex = Math.max(0, Math.min(products.length - 1, productIndex));
      if (completedIndices.includes(currentIndex)) return;
      quantityInput.value = String(limit);
      await send({
        type: "dotb-update-draft",
        currentIndex,
        limit,
        completedIndices,
      });
      const product = products[currentIndex];
      filling = true;
      filled = false;
      selectedProduct = product;
      setCurrentPreview(product);
      awaitingCategory = false;
      attributesFilling = false;
      attributesFilled = false;
      attributeFailures = [];
      detailsButton.hidden = true;
      fillButton.disabled = true;
      nextButton.hidden = true;
      setMessage(`Filling item ${currentIndex + 1}/${limit}: ${product.sku}`);
      try {
        setFieldValue(fields.titles[0], product.title);
        setFieldValue(fields.descriptions[0], product.description);
        setFieldValue(fields.prices[0], priceEndingInNinety(product.price));
        setFieldValue(fields.skus[0], product.sku);
        await fillSupplierLink(product);
        filled = true;
        awaitingCategory = true;
        fillButton.textContent = "Choose another product";
        nextButton.hidden = false;
        const trigger = categoryButton();
        if (trigger) setTimeout(() => trigger.click(), 50);
      } catch (error) {
        setMessage(
          error.message || "This Dotb item could not be filled. Retry when ready.",
        );
      } finally {
        filling = false;
        refreshStatus();
      }
    }

    function refreshStatus() {
      const { ready } = currentFields();
      if (!accountChosen && !accountPickerPrompted) promptForAccount();
      fillButton.disabled = filling || attributesFilling || waitingForFreshForm;
      nextButton.hidden = !filled;
      nextButton.disabled = (
        !filled
        || filling
        || attributesFilling
        || awaitingCategory
        || !categoryIsSelected()
      );
      const isLastPreparedItem = completedIndices.length + 1 >= limit;
      const intendedLabel = isLastPreparedItem
        ? "Save final draft"
        : `Save draft & next (${Math.min(limit, completedIndices.length + 2)}/${limit})`;
      if (nextButton.textContent !== intendedLabel) {
        nextButton.textContent = intendedLabel;
      }

      if (filling || attributesFilling || waitingForFreshForm) return;
      if (!accountChosen) {
        setMessage("Choose a Vinted account, then click Fill current item to select a product.");
        return;
      }
      if (!ready) {
        setMessage("Waiting for Dotb's item editor…");
        return;
      }
      if (filled) {
        if (awaitingCategory) {
          if (categoryIsSelected()) {
            awaitingCategory = false;
            autoFillAttributes(selectedProduct);
          } else {
            setMessage("Choose this product's Dotb category in the popup. Photos will stay empty for manual upload.");
          }
          return;
        }
        if (attributeFailures.length) {
          setMessage(`Text filled without photos. Retry or review: ${attributeFailures.join(", ")}.`);
          return;
        }
        setMessage(categoryIsSelected()
          ? `Item ${currentIndex + 1}/${limit} is filled without photos. Add its photos manually, review it, then save the draft${currentIndex + 1 < limit ? " and continue" : ""}.`
          : `Item ${currentIndex + 1}/${limit} is filled. Choose its category, then review it.`);
        return;
      }
      setMessage("Click Fill current item and choose an extracted product.");
    }

    function resetFilledState() {
      filled = false;
      selectedProduct = null;
      awaitingCategory = false;
      attributesFilling = false;
      attributesFilled = false;
      attributeFailures = [];
      detailsButton.hidden = true;
      filling = false;
      fillButton.textContent = "Fill current item";
      setCurrentPreview(null);
    }

    async function saveDraftProgress() {
      await send({
        type: "dotb-update-draft",
        products,
        currentIndex,
        limit,
        completedIndices,
      });
    }

    async function deleteLocalProduct(product) {
      if (!product?.id) {
        return "saved, but this old Dotb session has no local product ID to delete.";
      }
      const response = await send({
        type: "dotb-delete-product",
        productId: product.id,
        sku: product.sku || "",
      });
      if (!response?.ok) {
        throw new Error("saved, but Product Extractor could not delete the local copy.");
      }
      return "saved and deleted from Product Extractor.";
    }

    async function completeSavedProduct(savedIndex) {
      const product = products[savedIndex];
      const result = await deleteLocalProduct(product);
      products.splice(savedIndex, 1);
      completedIndices = completedIndices
        .filter((index) => index !== savedIndex)
        .map((index) => index > savedIndex ? index - 1 : index);
      limit = Math.max(0, Math.min(limit - 1, products.length));
      currentIndex = products.length
        ? Math.min(savedIndex, products.length - 1)
        : 0;
      quantityInput.max = String(Math.max(products.length, 1));
      quantityInput.value = String(Math.max(limit, 0));
      await saveDraftProgress();
      return result;
    }

    function firstUncompletedProductIndex() {
      return products.findIndex((_, index) => !completedIndices.includes(index));
    }

    function saveSuccessMessageVisible() {
      const statusText = [...document.querySelectorAll(
        '[role="status"], [data-sonner-toast], [data-slot="toast"], [aria-live]',
      )].filter(visible).map((element) => element.innerText).join(" ");
      return /saved|created|draft|enregistr|cree|créé|brouillon/i.test(statusText);
    }

    function nativeDotbSaveLabel(event) {
      if (!event.isTrusted) return "";
      const button = event.target.closest("button");
      if (!button || root.contains(button) || !visible(button)) return "";
      const label = String(button.innerText || "").replace(/\s+/g, " ").trim();
      return ["Save", "Save & Create Another"].includes(label) ? label : "";
    }

    async function waitForManualSave(previousSkuField, savedIndex, startingUrl) {
      const startingSku = String(previousSkuField?.value || "").trim();
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        await delay(300);
        const { fields, ready } = currentFields();
        const currentSku = String(fields.skus[0]?.value || "").trim();
        const previousFieldStillOpen = previousSkuField
          && document.contains(previousSkuField)
          && visible(previousSkuField);
        const replacedWithNewSku = fields.skus[0]
          && fields.skus[0] !== previousSkuField
          && currentSku !== startingSku;
        const savedOrMovedOn = (
          window.location.href !== startingUrl
          || saveSuccessMessageVisible()
          || (!previousFieldStillOpen && ready && currentSku !== startingSku)
          || replacedWithNewSku
          || (ready && startingSku && !currentSku)
        );
        if (savedOrMovedOn) {
          let result = "";
          try {
            result = await completeSavedProduct(savedIndex);
          } catch (error) {
            result = error.message || "saved, but local delete failed.";
          }
          resetFilledState();
          waitingForFreshForm = false;
          if (!products.length || limit <= 0) {
            await send({ type: "dotb-clear-draft" });
            fillButton.disabled = true;
            nextButton.hidden = true;
            setMessage(`Dotb draft ${result} Batch finished.`);
          } else {
            await saveDraftProgress();
            setMessage(`Dotb draft ${result} Ready for item ${currentIndex + 1}/${limit}.`);
            refreshStatus();
          }
          return;
        }
      }
      waitingForFreshForm = false;
      nextButton.disabled = false;
      setMessage("Dotb did not confirm the save. Local product was not deleted.");
    }

    async function waitForNewForm(previousSkuField, savedIndex) {
      const startingSku = String(previousSkuField?.value || "").trim();
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        await delay(300);
        const { fields, ready } = currentFields();
        const currentSku = String(fields.skus[0]?.value || "").trim();
        const freshForm = (
          ready
          && fields.skus[0]
          && (
            !currentSku
            || (fields.skus[0] !== previousSkuField && currentSku !== startingSku)
          )
        );
        if (freshForm) {
          let result = "";
          try {
            result = await completeSavedProduct(savedIndex);
          } catch (error) {
            result = error.message || "saved, but local delete failed.";
          }
          const nextIndex = firstUncompletedProductIndex();
          currentIndex = nextIndex >= 0 ? nextIndex : currentIndex;
          await saveDraftProgress();
          resetFilledState();
          waitingForFreshForm = false;
          setMessage(`Dotb draft ${result} Ready for item ${currentIndex + 1}/${limit}.`);
          refreshStatus();
          return;
        }
      }
      setMessage("Dotb is still saving. Local product was not deleted yet. When the fresh form appears, click Save draft & next again.");
      waitingForFreshForm = false;
      fillButton.disabled = false;
    }

    async function waitForFinalSave(previousSkuField, savedIndex, startingUrl) {
      const startingSku = String(previousSkuField?.value || "").trim();
      const deadline = Date.now() + 15000;
      while (Date.now() < deadline) {
        await delay(300);
        const { fields, ready } = currentFields();
        const currentSku = String(fields.skus[0]?.value || "").trim();
        const previousFieldStillOpen = previousSkuField
          && document.contains(previousSkuField)
          && visible(previousSkuField);
        const replacedWithNewSku = fields.skus[0]
          && fields.skus[0] !== previousSkuField
          && currentSku !== startingSku;
        const formMovedOn = (
          window.location.href !== startingUrl
          || saveSuccessMessageVisible()
          || (!previousFieldStillOpen && ready && currentSku !== startingSku)
          || replacedWithNewSku
          || (ready && startingSku && !currentSku)
        );
        if (formMovedOn) {
          let result = "";
          try {
            result = await completeSavedProduct(savedIndex);
          } catch (error) {
            result = error.message || "saved, but local delete failed.";
          }
          await send({ type: "dotb-clear-draft" });
          resetFilledState();
          waitingForFreshForm = false;
          fillButton.disabled = true;
          nextButton.hidden = true;
          setMessage(`Final Dotb draft ${result} Batch finished.`);
          return;
        }
      }
      waitingForFreshForm = false;
      nextButton.disabled = false;
      setMessage("Dotb did not confirm the final save. Local product was not deleted.");
    }

    quantityInput.addEventListener("change", async () => {
      limit = Math.max(
        1,
        Math.min(products.length, Number(quantityInput.value || products.length)),
      );
      quantityInput.value = String(limit);
      await send({
        type: "dotb-update-draft",
        currentIndex,
        limit,
        completedIndices,
      });
      refreshStatus();
    });

    document.addEventListener("click", (event) => {
      const saveLabel = nativeDotbSaveLabel(event);
      if (saveLabel && filled && selectedProduct && !waitingForFreshForm) {
        if (!categoryIsSelected()) {
          setMessage("Choose a category before saving this Dotb draft.");
          return;
        }
        const savedIndex = currentIndex;
        const previousSkuField = discoverFields().skus[0];
        const startingUrl = window.location.href;
        waitingForFreshForm = true;
        nextButton.disabled = true;
        setMessage(`Dotb is saving ${selectedProduct.sku || "this item"}; local copy will delete after confirmation...`);
        if (saveLabel === "Save & Create Another") {
          waitForNewForm(previousSkuField, savedIndex);
        } else {
          waitForManualSave(previousSkuField, savedIndex, startingUrl);
        }
      }
      const option = accountOptionFromEvent(event);
      if (option) {
        accountChosen = true;
        setTimeout(refreshStatus, 0);
      }
      if (awaitingCategory && !root.contains(event.target)) {
        setTimeout(() => {
          if (!awaitingCategory || !categoryIsSelected()) return;
          awaitingCategory = false;
          autoFillAttributes(selectedProduct);
        }, 350);
      }
    }, true);

    document.addEventListener("change", (event) => {
      const control = accountControl();
      if (!control || (event.target !== control && !control.contains(event.target))) {
        return;
      }
      accountChosen = accountIsSelected();
      setTimeout(refreshStatus, 0);
    }, true);

    fillButton.addEventListener("click", () => {
      openProductPicker(
        products,
        currentIndex,
        completedIndices,
        fillCurrentItem,
      );
    });

    detailsButton.addEventListener("click", () => {
      attributesFilled = false;
      attributeFailures = [];
      autoFillAttributes(selectedProduct);
    });

    nextButton.addEventListener("click", async () => {
      if (!filled || !categoryIsSelected()) {
        setMessage("Choose a category and review this item first.");
        return;
      }
      const savedIndex = currentIndex;
      const completedAfterSave = [...new Set([...completedIndices, savedIndex])];
      const isLast = completedAfterSave.length >= limit;
      const nativeButton = actionButton(
        isLast ? "Save" : "Save & Create Another",
      );
      if (!nativeButton) {
        setMessage("Dotb's save button could not be found.");
        return;
      }
      nextButton.disabled = true;
      waitingForFreshForm = true;
      const previousSkuField = discoverFields().skus[0];
      if (isLast) {
        const startingUrl = window.location.href;
        setMessage("Saving the final Dotb draft...");
        nativeButton.click();
        waitForFinalSave(previousSkuField, savedIndex, startingUrl);
        return;
      }

      setMessage(`Saving draft for item ${savedIndex + 1}/${limit}...`);
      nativeButton.click();
      waitForNewForm(previousSkuField, savedIndex);
    });

    refreshStatus();
    const observer = new MutationObserver(refreshStatus);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => observer.disconnect(), 30 * 60 * 1000);
  }

  initialize().catch(() => {});
})();
