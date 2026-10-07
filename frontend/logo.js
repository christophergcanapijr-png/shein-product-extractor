const IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "webp", "gif", "tif", "tiff", "bmp"]);
const CONCURRENCY = 8;

const $ = (selector, root = document) => root.querySelector(selector);

const elements = {
  dropZone: $("#dropZone"),
  fileInput: $("#logoFileInput"),
  fileCount: $("#fileCount"),
  queuedCount: $("#queuedCount"),
  doneCount: $("#doneCount"),
  totalCount: $("#totalCount"),
  removedCount: $("#removedCount"),
  aggressive: $("#aggressiveMode"),
  stripIcc: $("#stripIcc"),
  compactSize: $("#compactSize"),
  cleanSuffix: $("#cleanSuffix"),
  clearButton: $("#clearLogoButton"),
  saveFolderButton: $("#saveFolderButton"),
  downloadZipButton: $("#downloadZipButton"),
  removeButton: $("#removeButton"),
  queueSection: $("#logoQueueSection"),
  queueList: $("#logoQueueList"),
  queueSummary: $("#logoQueueSummary"),
  progress: $("#logoProgressBar"),
  toast: $("#toast"),
  health: $("#healthStatus"),
};

const state = {
  items: [],
  running: false,
  toastTimer: null,
  totalProcessed: 0,
};

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let crc = i;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = crc & 1 ? (0xedb88320 ^ (crc >>> 1)) : crc >>> 1;
    }
    table[i] = crc >>> 0;
  }
  return table;
})();

function crc32(bytes) {
  let crc = 0xffffffff;
  for (let i = 0, n = bytes.length; i < n; i += 1) {
    crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function isImageFile(file) {
  if (file.type?.startsWith("image/")) return true;
  const ext = file.name.split(".").pop()?.toLowerCase();
  return IMAGE_EXTS.has(ext);
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => elements.toast.classList.remove("show"), 2800);
}

function setProgress(done, total) {
  elements.progress.style.width = `${total ? Math.round((done / total) * 100) : 0}%`;
}

function extensionForType(contentType, fallbackName) {
  if (contentType === "image/jpeg") return ".jpg";
  if (contentType === "image/png") return ".png";
  const ext = fallbackName.split(".").pop()?.toLowerCase();
  if (ext === "jpg" || ext === "jpeg") return ".jpg";
  if (ext === "png") return ".png";
  return ".jpg";
}

function cleanedName(originalName, contentType) {
  const base = originalName.replace(/^.*[\\/]/, "");
  const ext = extensionForType(contentType, base);
  const stem = base.replace(/\.[^.]+$/, "") || "image";
  if (!elements.cleanSuffix.checked) return `${stem}${ext}`;
  return `${stem}-clean${ext}`;
}

function updateStats() {
  const queued = state.items.length;
  const done = state.items.filter((item) => item.status === "done").length;
  const failed = state.items.filter((item) => item.status === "error").length;
  const found = state.items.filter((item) => item.found).length;
  const metaBytes = state.items.reduce((sum, item) => sum + (item.strippedBytes || 0), 0);
  const compactBytes = state.items.reduce((sum, item) => sum + (item.compactSaved || 0), 0);
  elements.queuedCount.textContent = String(queued);
  elements.doneCount.textContent = String(done);
  elements.totalCount.textContent = String(state.totalProcessed);
  elements.removedCount.textContent = String(found);
  elements.fileCount.textContent = `${queued} file${queued === 1 ? "" : "s"}`;
  const metaNote = metaBytes ? ` · ${formatBytes(metaBytes)} metadata` : "";
  const compactNote = compactBytes ? ` · ${formatBytes(compactBytes)} lighter` : "";
  elements.queueSummary.textContent = failed
    ? `${done} cleaned${metaNote}${compactNote} · ${failed} failed`
    : done
      ? `${done} cleaned${found ? ` · ${found} logo${found === 1 ? "" : "s"}` : ""}${metaNote}${compactNote}`
      : queued
        ? "Ready to clean"
        : "";
  const hasFiles = queued > 0 && !state.running;
  const hasResults = state.items.some((item) => item.status === "done" && item.blob);
  elements.removeButton.disabled = !hasFiles;
  elements.clearButton.disabled = queued === 0 || state.running;
  elements.downloadZipButton.disabled = !hasResults || state.running;
  elements.saveFolderButton.disabled = !hasResults || state.running || !window.showDirectoryPicker;
}

function renderQueue() {
  elements.queueSection.hidden = state.items.length === 0;
  elements.queueList.replaceChildren(
    ...state.items.map((item) => {
      const row = document.createElement("div");
      row.className = `queue-item meta-queue-item ${item.status}`;
      const thumb = item.previewUrl
        ? `<img src="${item.previewUrl}" alt="" class="meta-thumb">`
        : `<span class="meta-thumb meta-thumb-empty"></span>`;
      row.innerHTML = `
        ${thumb}
        <code title="${item.file.name}">${item.file.name}</code>
        <span class="queue-message">${item.message}</span>
        <span class="queue-status">${item.statusLabel}</span>
      `;
      return row;
    }),
  );
}

function addFiles(files) {
  const existing = new Set(state.items.map((item) => `${item.file.name}:${item.file.size}:${item.file.lastModified}`));
  let added = 0;
  for (const file of files) {
    if (!isImageFile(file)) continue;
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (existing.has(key)) continue;
    existing.add(key);
    state.items.push({
      file,
      previewUrl: URL.createObjectURL(file),
      status: "queued",
      statusLabel: "Queued",
      message: formatBytes(file.size),
      blob: null,
      outputName: file.name,
      found: false,
      strippedBytes: 0,
    });
    added += 1;
  }
  if (added) {
    renderQueue();
    updateStats();
    showToast(`${added} image${added === 1 ? "" : "s"} added.`);
  } else {
    showToast("No new image files found.");
  }
}

function entryToFile(entry) {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

function readAllEntries(reader) {
  return new Promise((resolve, reject) => {
    const all = [];
    const read = () => {
      reader.readEntries((batch) => {
        if (!batch.length) {
          resolve(all);
          return;
        }
        all.push(...batch);
        read();
      }, reject);
    };
    read();
  });
}

async function collectFromEntry(entry, files) {
  if (entry.isFile) {
    files.push(await entryToFile(entry));
    return;
  }
  if (!entry.isDirectory) return;
  const children = await readAllEntries(entry.createReader());
  await Promise.all(children.map((child) => collectFromEntry(child, files)));
}

async function filesFromDrop(event) {
  const items = [...(event.dataTransfer?.items || [])];
  const entries = items.map((item) => item.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.length) return [...(event.dataTransfer?.files || [])];
  const files = [];
  await Promise.all(entries.map((entry) => collectFromEntry(entry, files)));
  return files;
}

async function mapPool(items, limit, worker) {
  let index = 0;
  const run = async () => {
    while (index < items.length) {
      const current = index;
      index += 1;
      await worker(items[current], current);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, run));
}

async function removeOne(item, aggressive, stripIcc, compact) {
  const typed = item.file.type?.startsWith("image/")
    ? item.file
    : new File([item.file], item.file.name, { type: "image/png" });
  const body = new FormData();
  body.append("file", typed);
  const params = new URLSearchParams({
    aggressive: aggressive ? "true" : "false",
    strip_icc: stripIcc ? "true" : "false",
    compact: compact ? "true" : "false",
  });
  const response = await fetch(`/api/logo/remove?${params}`, {
    method: "POST",
    body,
  });
  if (!response.ok) {
    let message = "The image could not be cleaned.";
    try {
      const payload = await response.json();
      message = payload?.error?.message || message;
    } catch {
      /* keep default */
    }
    throw new Error(message);
  }
  const blob = await response.blob();
  const found = response.headers.get("X-Logo-Found") === "1";
  const corner = response.headers.get("X-Logo-Corner") || "";
  const elapsed = Number(response.headers.get("X-Clean-Elapsed-Ms") || 0);
  const strippedBytes = Number(response.headers.get("X-Metadata-Stripped-Bytes") || 0);
  const compactSaved = Number(response.headers.get("X-Compact-Saved-Bytes") || 0);
  const totalProcessed = Number(response.headers.get("X-Logo-Total-Processed") || 0);
  if (totalProcessed > state.totalProcessed) state.totalProcessed = totalProcessed;
  item.blob = blob;
  item.found = found;
  item.strippedBytes = strippedBytes;
  item.compactSaved = compactSaved;
  item.outputName = cleanedName(item.file.name, blob.type);
  const parts = [];
  if (found) parts.push(`sparkle removed (${corner || "corner"})`);
  parts.push(strippedBytes ? `${formatBytes(strippedBytes)} metadata` : "metadata clean");
  if (compactSaved) parts.push(`${formatBytes(compactSaved)} lighter`);
  parts.push(formatBytes(blob.size));
  item.message = `${parts.join(" · ")} · ${elapsed.toFixed(1)} ms`;
}

function putUint32(view, offset, value, littleEndian) {
  view.setUint32(offset, value >>> 0, littleEndian);
}

function putUint16(view, offset, value, littleEndian) {
  view.setUint16(offset, value & 0xffff, littleEndian);
}

function buildZip(files) {
  const encoder = new TextEncoder();
  const parts = [];
  const centrals = [];
  let offset = 0;

  for (const file of files) {
    const nameBytes = encoder.encode(file.name);
    const data = file.bytes;
    const crc = crc32(data);
    const size = data.length;
    const local = new ArrayBuffer(30);
    const localView = new DataView(local);
    putUint32(localView, 0, 0x04034b50, true);
    putUint16(localView, 4, 20, true);
    putUint16(localView, 6, 0x0800, true);
    putUint16(localView, 8, 0, true);
    putUint16(localView, 10, 0, true);
    putUint16(localView, 12, 0, true);
    putUint32(localView, 14, crc, true);
    putUint32(localView, 18, size, true);
    putUint32(localView, 22, size, true);
    putUint16(localView, 26, nameBytes.length, true);
    putUint16(localView, 28, 0, true);
    parts.push(new Uint8Array(local), nameBytes, data);

    const central = new ArrayBuffer(46);
    const centralView = new DataView(central);
    putUint32(centralView, 0, 0x02014b50, true);
    putUint16(centralView, 4, 20, true);
    putUint16(centralView, 6, 20, true);
    putUint16(centralView, 8, 0x0800, true);
    putUint16(centralView, 10, 0, true);
    putUint16(centralView, 12, 0, true);
    putUint16(centralView, 14, 0, true);
    putUint32(centralView, 16, crc, true);
    putUint32(centralView, 18, size, true);
    putUint32(centralView, 22, size, true);
    putUint16(centralView, 26, nameBytes.length, true);
    putUint16(centralView, 28, 0, true);
    putUint16(centralView, 30, 0, true);
    putUint16(centralView, 32, 0, true);
    putUint16(centralView, 34, 0, true);
    putUint32(centralView, 36, 0, true);
    putUint32(centralView, 42, offset, true);
    centrals.push(new Uint8Array(central), nameBytes);
    offset += 30 + nameBytes.length + size;
  }

  const centralSize = centrals.reduce((sum, part) => sum + part.length, 0);
  const end = new ArrayBuffer(22);
  const endView = new DataView(end);
  putUint32(endView, 0, 0x06054b50, true);
  putUint16(endView, 4, 0, true);
  putUint16(endView, 6, 0, true);
  putUint16(endView, 8, files.length, true);
  putUint16(endView, 10, files.length, true);
  putUint32(endView, 12, centralSize, true);
  putUint32(endView, 16, offset, true);
  putUint16(endView, 20, 0, true);
  return new Blob([...parts, ...centrals, new Uint8Array(end)], { type: "application/zip" });
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function uniqueOutputName(name, used) {
  if (!used.has(name)) {
    used.add(name);
    return name;
  }
  const stem = name.replace(/\.[^.]+$/, "");
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".")) : "";
  let index = 2;
  let candidate = `${stem}-${index}${ext}`;
  while (used.has(candidate)) {
    index += 1;
    candidate = `${stem}-${index}${ext}`;
  }
  used.add(candidate);
  return candidate;
}

async function removeAll() {
  if (state.running || !state.items.length) return;
  state.running = true;
  const aggressive = elements.aggressive.checked;
  const stripIcc = elements.stripIcc?.checked || false;
  const compact = elements.compactSize?.checked !== false;
  elements.removeButton.textContent = "Cleaning…";
  updateStats();
  let finished = 0;
  setProgress(0, state.items.length);

  await mapPool(state.items, CONCURRENCY, async (item) => {
    item.status = "working";
    item.statusLabel = "Working";
    item.message = "Removing logo, metadata, and extra weight…";
    renderQueue();
    try {
      await removeOne(item, aggressive, stripIcc, compact);
      item.status = "done";
      item.statusLabel = "Clean";
    } catch (error) {
      item.status = "error";
      item.statusLabel = "Failed";
      item.message = error.message || "Could not process this image.";
      item.blob = null;
    }
    finished += 1;
    setProgress(finished, state.items.length);
    renderQueue();
    updateStats();
  });

  state.running = false;
  elements.removeButton.textContent = "Clean images";
  updateStats();

  const cleaned = state.items.filter((item) => item.status === "done");
  const found = cleaned.filter((item) => item.found);
  if (!cleaned.length) {
    showToast("No images could be processed.");
    return;
  }
  if (cleaned.length === 1) {
    triggerDownload(cleaned[0].blob, cleaned[0].outputName);
    showToast("Image cleaned. Download started.");
    return;
  }
  showToast(
    `${cleaned.length} images cleaned${found.length ? ` · ${found.length} logo${found.length === 1 ? "" : "s"} removed` : ""}. Download ZIP or save to a folder.`,
  );
}

async function downloadZip() {
  const cleaned = state.items.filter((item) => item.status === "done" && item.blob);
  if (!cleaned.length) return;
  elements.downloadZipButton.disabled = true;
  elements.downloadZipButton.textContent = "Building ZIP…";
  try {
    const used = new Set();
    const form = new FormData();
    for (const item of cleaned) {
      form.append("files", item.blob, uniqueOutputName(item.outputName, used));
    }
    const response = await fetch("/api/logo/pack", { method: "POST", body: form });
    if (!response.ok) {
      throw new Error("The ZIP could not be created.");
    }
    const zip = await response.blob();
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    triggerDownload(zip, `cleaned-images-${stamp}.zip`);
    showToast("ZIP download started.");
  } catch (error) {
    showToast(error.message || "The ZIP could not be created.");
  } finally {
    elements.downloadZipButton.textContent = "Download ZIP";
    updateStats();
  }
}

async function saveToFolder() {
  const cleaned = state.items.filter((item) => item.status === "done" && item.blob);
  if (!cleaned.length || !window.showDirectoryPicker) return;
  let directory;
  try {
    directory = await window.showDirectoryPicker({ mode: "readwrite" });
  } catch (error) {
    if (error?.name === "AbortError") return;
    showToast("Could not open that folder.");
    return;
  }
  elements.saveFolderButton.disabled = true;
  const used = new Set();
  let written = 0;
  try {
    for (const item of cleaned) {
      const name = uniqueOutputName(item.outputName, used);
      const handle = await directory.getFileHandle(name, { create: true });
      const writable = await handle.createWritable();
      await writable.write(item.blob);
      await writable.close();
      written += 1;
    }
    showToast(`${written} images saved.`);
  } catch (error) {
    showToast(error.message || "Could not save the images.");
  } finally {
    updateStats();
  }
}

function clearAll() {
  for (const item of state.items) {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
  }
  state.items = [];
  elements.fileInput.value = "";
  setProgress(0, 0);
  renderQueue();
  updateStats();
}

function setDropActive(active) {
  elements.dropZone.classList.toggle("is-active", active);
}

elements.dropZone.addEventListener("dragenter", (event) => {
  event.preventDefault();
  setDropActive(true);
});
elements.dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  setDropActive(true);
});
elements.dropZone.addEventListener("dragleave", (event) => {
  if (!elements.dropZone.contains(event.relatedTarget)) setDropActive(false);
});
elements.dropZone.addEventListener("drop", async (event) => {
  event.preventDefault();
  setDropActive(false);
  addFiles(await filesFromDrop(event));
});
elements.fileInput.addEventListener("change", () => {
  addFiles([...elements.fileInput.files]);
  elements.fileInput.value = "";
});
elements.removeButton.addEventListener("click", removeAll);
elements.downloadZipButton.addEventListener("click", downloadZip);
elements.saveFolderButton.addEventListener("click", saveToFolder);
elements.clearButton.addEventListener("click", clearAll);
document.addEventListener("paste", (event) => {
  const files = [...(event.clipboardData?.files || [])];
  if (files.length) addFiles(files);
});

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

if (!window.showDirectoryPicker) {
  elements.saveFolderButton.title = "Folder save is available in Brave and Chrome.";
}

async function loadTotalProcessed() {
  try {
    const response = await fetch("/api/logo/stats");
    if (!response.ok) return;
    const payload = await response.json();
    const total = Number(payload?.total_processed || 0);
    if (total > state.totalProcessed) state.totalProcessed = total;
    updateStats();
  } catch {
    /* keep the current total */
  }
}

checkHealth();
loadTotalProcessed();
updateStats();
