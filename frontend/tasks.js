const $ = (selector, root = document) => root.querySelector(selector);

const STORAGE_KEY = "task-accomplished-rows";
const ASSIGNMENT_KEY = "task-accomplished-assignment";

const PROMPT_OPTIONS = [
  "coat",
  "jacket",
  "long boots",
  "long boots 2",
  "handbag",
  "small plant",
  "plant",
  "shelf",
  "dress",
  "dress m",
  "skirt",
  "jeans",
  "jean",
  "heels",
  "bag",
  "hat",
  "mask",
  "carpet",
  "plastic model",
  "nightstand",
];

const PHOTO_OPTIONS = [
  "coat 1",
  "coat 2",
  "long boot",
  "long boot 1",
  "long boot 2",
  "shoulderbag 1",
  "shoulderbag 2",
  "plant 1",
  "plant 2",
  "shelf 1",
  "shelf 2",
  "hat 3",
  "hat 10",
  "skirt 2",
  "plastic model 1",
  "jean 7",
  "nightstand 1",
];

const ACCOUNT_OPTIONS = [
  "Masteclia1",
  "vanessaafag",
  "zenna027",
  "loucayez",
  "kamelionne",
  "dephila7",
  "minnisa7",
  "guinaless9",
  "guinaal919",
  "Jinnefz89",
  "zasefo",
  "hedinbard",
  "ramdom account",
];

const LINE_RE = /^(tests?)\s+on\s+prompts?\s+(.+?)\s+with\s+(photos?)\s+(.+)$/i;
const SPECIAL_ACCOUNT_RE = /^(ramdom account|random account)\b/i;
const SKU_RE = /\b(?:s[zwmr]\d{8,}|\d{2}[A-Z]\d[\w-]*|[A-Z]{1,4}\d{4,}[A-Z0-9]*|[A-Z0-9]*\d[A-Z0-9-]{5,})\b/gi;

const elements = {
  assignment: $("#taskAssignmentInput"),
  parseStatus: $("#taskParseStatus"),
  parseButton: $("#parseTaskButton"),
  copySkus: $("#copyTaskSkusButton"),
  body: $("#taskTableBody"),
  lineCount: $("#taskLineCount"),
  preview: $("#taskPreview"),
  addRow: $("#addTaskRowButton"),
  clearRows: $("#clearTaskRowsButton"),
  copyReport: $("#copyTaskReportButton"),
  toast: $("#toast"),
  health: $("#healthStatus"),
  promptOptions: $("#taskPromptOptions"),
  photoOptions: $("#taskPhotoOptions"),
  accountOptions: $("#taskAccountOptions"),
};

const state = {
  rows: [],
  toastTimer: null,
};

function emptyRow() {
  return {
    id: crypto.randomUUID(),
    prompt: "",
    photo: "",
    account: "",
    notes: "",
    pluralTest: false,
    pluralPhoto: false,
    accountParen: false,
  };
}

function loadRows() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed) && parsed.length) {
      return parsed.map((row) => ({
        ...emptyRow(),
        ...row,
        id: row.id || crypto.randomUUID(),
      }));
    }
  } catch {
    /* keep defaults */
  }
  return [emptyRow(), emptyRow(), emptyRow()];
}

function saveRows() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.rows));
}

function fillDatalist(list, values) {
  list.innerHTML = [...new Set(values)]
    .map((value) => `<option value="${escapeHtml(value)}"></option>`)
    .join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function cleanSpaces(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function splitPhotoAndAccount(afterPhotoWord) {
  const text = cleanSpaces(afterPhotoWord);
  const onMatch = text.match(/^(.*?)\s+on\s+(.+)$/i);
  if (onMatch) {
    return { photo: onMatch[1].trim(), accountPart: onMatch[2].trim() };
  }
  const parenMatch = text.match(/^(.*?)\s+(\(.+)$/);
  if (parenMatch) {
    return { photo: parenMatch[1].trim(), accountPart: parenMatch[2].trim() };
  }
  return null;
}

function splitAccountNotes(accountPart) {
  let text = cleanSpaces(accountPart);
  let account = "";
  let accountParen = false;

  const paren = text.match(/^\(([^)]+)\)(.*)$/);
  if (paren) {
    account = paren[1].trim();
    accountParen = true;
    text = cleanSpaces(paren[2]);
  } else {
    const special = text.match(SPECIAL_ACCOUNT_RE);
    if (special) {
      account = special[1];
      text = cleanSpaces(text.slice(special[0].length));
    } else {
      const token = text.match(/^(\S+)(.*)$/);
      account = token ? token[1] : "";
      text = token ? cleanSpaces(token[2]) : "";
    }
  }

  if (/^on\s+dotb\b/i.test(text)) {
    text = cleanSpaces(text.replace(/^on\s+dotb\b/i, ""));
  }

  return { account, accountParen, notes: text };
}

function parseAssignmentLine(raw) {
  const line = cleanSpaces(raw);
  if (!line || /^task accomplished/i.test(line)) return null;

  const colonIndex = line.indexOf(":");
  const head = colonIndex === -1 ? line : line.slice(0, colonIndex).trim();
  const afterColon = colonIndex === -1 ? "" : line.slice(colonIndex + 1).trim();
  const match = head.match(LINE_RE);
  if (!match) return null;

  const split = splitPhotoAndAccount(match[4]);
  if (!split) return null;
  const accountBits = splitAccountNotes(split.accountPart);
  const notes = cleanSpaces([accountBits.notes, afterColon].filter(Boolean).join(" "));
  if (!split.photo || !accountBits.account) return null;

  return {
    ...emptyRow(),
    prompt: match[2].trim(),
    photo: split.photo,
    account: accountBits.account,
    notes,
    pluralTest: match[1].toLowerCase() === "tests",
    pluralPhoto: match[3].toLowerCase() === "photos",
    accountParen: accountBits.accountParen,
  };
}

function parseAssignment(text) {
  const rawLines = String(text || "").split(/\r?\n/);
  const rows = [];
  let skipped = 0;
  for (const raw of rawLines) {
    if (!cleanSpaces(raw)) continue;
    const row = parseAssignmentLine(raw);
    if (row) rows.push(row);
    else skipped += 1;
  }
  return { rows, skipped };
}

function lineFor(row) {
  const prompt = row.prompt.trim();
  const photo = row.photo.trim();
  const account = row.account.trim();
  if (!prompt || !photo || !account) return "";
  const verb = row.pluralTest ? "tests" : "test";
  const photoWord = row.pluralPhoto ? "photos" : "photo";
  if (row.accountParen) {
    return `${verb} on prompt ${prompt} with ${photoWord} ${photo} (${account})`;
  }
  return `${verb} on prompt ${prompt} with ${photoWord} ${photo} on ${account}`;
}

function buildReport() {
  const lines = state.rows.map(lineFor).filter(Boolean);
  return ["TASK ACCOMPLISHED:", ...lines].join("\n");
}

function collectSkus() {
  const found = [];
  for (const row of state.rows) {
    const matches = String(row.notes || "").match(SKU_RE) || [];
    for (const sku of matches) {
      if (!found.includes(sku)) found.push(sku);
    }
  }
  return found;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 2200);
}

function refreshPreview() {
  const lines = state.rows.map(lineFor).filter(Boolean);
  elements.lineCount.textContent = String(lines.length);
  elements.preview.textContent = buildReport();
  elements.copyReport.disabled = lines.length === 0;
  elements.copySkus.disabled = collectSkus().length === 0;
}

function render() {
  elements.body.innerHTML = state.rows.map((row) => `
    <tr data-id="${row.id}">
      <td>
        <input type="text" name="prompt" list="taskPromptOptions" value="${escapeHtml(row.prompt)}" placeholder="hat" autocomplete="off" aria-label="Prompt">
      </td>
      <td>
        <input type="text" name="photo" list="taskPhotoOptions" value="${escapeHtml(row.photo)}" placeholder="hat 3" autocomplete="off" aria-label="Photo">
      </td>
      <td>
        <input type="text" name="account" list="taskAccountOptions" value="${escapeHtml(row.account)}" placeholder="guinaless9" autocomplete="off" aria-label="Account">
      </td>
      <td>
        <input type="text" name="notes" value="${escapeHtml(row.notes)}" placeholder="SKUs and notes" autocomplete="off" aria-label="SKUs and notes">
      </td>
      <td class="task-flag">
        <input type="checkbox" name="pluralTest" ${row.pluralTest ? "checked" : ""} aria-label="Use tests">
      </td>
      <td class="task-flag">
        <input type="checkbox" name="pluralPhoto" ${row.pluralPhoto ? "checked" : ""} aria-label="Use photos">
      </td>
      <td class="task-flag">
        <input type="checkbox" name="accountParen" ${row.accountParen ? "checked" : ""} aria-label="Put account in parentheses">
      </td>
      <td class="task-remove-col">
        <button class="button button-ghost task-remove" type="button" data-remove="${row.id}" aria-label="Remove row">✕</button>
      </td>
    </tr>
  `).join("");
  refreshPreview();
}

function updateRow(id, patch) {
  const row = state.rows.find((item) => item.id === id);
  if (!row) return;
  Object.assign(row, patch);
  saveRows();
  refreshPreview();
}

function applyAssignment(text, { silent = false } = {}) {
  localStorage.setItem(ASSIGNMENT_KEY, text);
  const { rows, skipped } = parseAssignment(text);
  if (!rows.length) {
    if (!silent) {
      elements.parseStatus.textContent = skipped
        ? `${skipped} line${skipped === 1 ? "" : "s"} could not be read.`
        : "Waiting for an assignment.";
      if (!silent && cleanSpaces(text)) showToast("No test lines found.");
    }
    return false;
  }
  state.rows = rows;
  saveRows();
  fillDatalist(elements.promptOptions, [...PROMPT_OPTIONS, ...rows.map((row) => row.prompt)]);
  fillDatalist(elements.photoOptions, [...PHOTO_OPTIONS, ...rows.map((row) => row.photo)]);
  fillDatalist(elements.accountOptions, [...ACCOUNT_OPTIONS, ...rows.map((row) => row.account)]);
  render();
  const extra = skipped ? ` Skipped ${skipped} unmatched line${skipped === 1 ? "" : "s"}.` : "";
  elements.parseStatus.textContent = `Read ${rows.length} test${rows.length === 1 ? "" : "s"}.${extra}`;
  if (!silent) showToast(`Parsed ${rows.length} test${rows.length === 1 ? "" : "s"}.`);
  return true;
}

async function copyReport() {
  const report = buildReport();
  if (!state.rows.some(lineFor)) return;
  try {
    await navigator.clipboard.writeText(report);
    showToast("Report copied.");
  } catch {
    showToast("Could not copy. Select the preview and copy it.");
  }
}

async function copySkus() {
  const skus = collectSkus();
  if (!skus.length) return;
  try {
    await navigator.clipboard.writeText(skus.join("\n"));
    showToast(`Copied ${skus.length} SKU${skus.length === 1 ? "" : "s"}.`);
  } catch {
    showToast("Could not copy SKUs.");
  }
}

async function checkHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error("offline");
    elements.health.classList.add("online");
    $("span", elements.health).textContent = "Ready";
  } catch {
    elements.health.classList.add("offline");
    $("span", elements.health).textContent = "Offline";
  }
}

elements.body.addEventListener("input", (event) => {
  const row = event.target.closest("tr");
  if (!row) return;
  const field = event.target.name;
  if (!["prompt", "photo", "account", "notes"].includes(field)) return;
  updateRow(row.dataset.id, { [field]: event.target.value });
});

elements.body.addEventListener("change", (event) => {
  const row = event.target.closest("tr");
  if (!row) return;
  const field = event.target.name;
  if (!["pluralTest", "pluralPhoto", "accountParen"].includes(field)) return;
  updateRow(row.dataset.id, { [field]: event.target.checked });
});

elements.body.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (!button) return;
  state.rows = state.rows.filter((row) => row.id !== button.dataset.remove);
  if (!state.rows.length) state.rows.push(emptyRow());
  saveRows();
  render();
});

elements.addRow.addEventListener("click", () => {
  state.rows.push(emptyRow());
  saveRows();
  render();
  const lastInput = $("tr:last-child input[name='prompt']", elements.body);
  lastInput?.focus();
});

elements.clearRows.addEventListener("click", () => {
  state.rows = [emptyRow(), emptyRow(), emptyRow()];
  elements.assignment.value = "";
  localStorage.removeItem(ASSIGNMENT_KEY);
  elements.parseStatus.textContent = "Waiting for an assignment.";
  saveRows();
  render();
});

elements.copyReport.addEventListener("click", copyReport);
elements.copySkus.addEventListener("click", copySkus);
elements.parseButton.addEventListener("click", () => {
  applyAssignment(elements.assignment.value);
});
elements.assignment.addEventListener("paste", (event) => {
  const text = event.clipboardData?.getData("text") ?? "";
  window.setTimeout(() => applyAssignment(text || elements.assignment.value), 0);
});

fillDatalist(elements.promptOptions, PROMPT_OPTIONS);
fillDatalist(elements.photoOptions, PHOTO_OPTIONS);
fillDatalist(elements.accountOptions, ACCOUNT_OPTIONS);
state.rows = loadRows();
elements.assignment.value = localStorage.getItem(ASSIGNMENT_KEY) || "";
if (elements.assignment.value) applyAssignment(elements.assignment.value, { silent: true });
else render();
checkHealth();
