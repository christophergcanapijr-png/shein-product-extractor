const IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "webp", "avif", "gif", "tif", "tiff", "bmp"]);
const CONCURRENCY = 8;

const $ = (selector, root = document) => root.querySelector(selector);

const elements = {
  dropZone: $("#dropZone"),
  fileInput: $("#metaFileInput"),
  fileCount: $("#fileCount"),
  queuedCount: $("#queuedCount"),
  doneCount: $("#doneCount"),
  removedCount: $("#removedCount"),
  stripIcc: $("#stripIcc"),
  cleanSuffix: $("#cleanSuffix"),
  clearButton: $("#clearMetaButton"),
  saveFolderButton: $("#saveFolderButton"),
  downloadZipButton: $("#downloadZipButton"),
  stripButton: $("#stripButton"),
  queueSection: $("#metaQueueSection"),
  queueList: $("#metaQueueList"),
  queueSummary: $("#metaQueueSummary"),
  progress: $("#metaProgressBar"),
  toast: $("#toast"),
  health: $("#healthStatus"),
};

const state = {
  items: [],
  running: false,
  toastTimer: null,
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

function concatBytes(parts) {
  let total = 0;
  for (const part of parts) total += part.length;
  const out = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function u32be(view, offset) {
  return view.getUint32(offset, false);
}

function u16be(view, offset) {
  return view.getUint16(offset, false);
}

function u32le(view, offset) {
  return view.getUint32(offset, true);
}

function detectFormat(data) {
  if (data.length < 12) return null;
  if (data[0] === 0xff && data[1] === 0xd8) return "jpeg";
  if (
    data[0] === 0x89 && data[1] === 0x50 && data[2] === 0x4e && data[3] === 0x47 &&
    data[4] === 0x0d && data[5] === 0x0a && data[6] === 0x1a && data[7] === 0x0a
  ) {
    return "png";
  }
  if (
    data[0] === 0x52 && data[1] === 0x49 && data[2] === 0x46 && data[3] === 0x46 &&
    data[8] === 0x57 && data[9] === 0x45 && data[10] === 0x42 && data[11] === 0x50
  ) {
    return "webp";
  }
  if (data[4] === 0x66 && data[5] === 0x74 && data[6] === 0x79 && data[7] === 0x70) {
    const brand = String.fromCharCode(data[8], data[9], data[10], data[11]).toLowerCase();
    if (brand === "avif" || brand === "avis") return "avif";
  }
  return null;
}

function shouldStripJpegMarker(marker, stripIcc) {
  if (marker === 0xe1 || marker === 0xed || marker === 0xfe) return true;
  if (marker >= 0xe3 && marker <= 0xef) return true;
  return stripIcc && marker === 0xe2;
}

function stripJpeg(data, stripIcc) {
  const parts = [new Uint8Array([0xff, 0xd8])];
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  let pos = 2;
  const n = data.length;

  while (pos < n - 1) {
    if (data[pos] !== 0xff) {
      parts.push(data.subarray(pos));
      break;
    }
    while (pos < n && data[pos] === 0xff) pos += 1;
    if (pos >= n) break;
    const marker = data[pos];
    pos += 1;

    if (marker === 0xd9) {
      parts.push(new Uint8Array([0xff, 0xd9]));
      break;
    }
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      parts.push(new Uint8Array([0xff, marker]));
      continue;
    }
    if (marker === 0xda) {
      parts.push(new Uint8Array([0xff, 0xda]));
      parts.push(data.subarray(pos));
      break;
    }
    if (pos + 2 > n) break;
    const segLen = u16be(view, pos);
    if (!shouldStripJpegMarker(marker, stripIcc)) {
      parts.push(new Uint8Array([0xff, marker]));
      parts.push(data.subarray(pos, pos + segLen));
    }
    pos += segLen;
  }
  return concatBytes(parts);
}

const PNG_STRIP = new Set(["tEXt", "iTXt", "zTXt", "tIME", "eXIf", "pHYs", "sPLT", "hIST", "caBX"]);

function stripPng(data) {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const parts = [data.subarray(0, 8)];
  let pos = 8;
  while (pos + 12 <= data.length) {
    const chunkLen = u32be(view, pos);
    const type = String.fromCharCode(data[pos + 4], data[pos + 5], data[pos + 6], data[pos + 7]);
    const total = 12 + chunkLen;
    if (!PNG_STRIP.has(type)) parts.push(data.subarray(pos, pos + total));
    pos += total;
    if (type === "IEND") break;
  }
  return concatBytes(parts);
}

function stripWebp(data) {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const chunks = [];
  let pos = 12;
  while (pos + 8 <= data.length) {
    const id = String.fromCharCode(data[pos], data[pos + 1], data[pos + 2], data[pos + 3]);
    const size = u32le(view, pos + 4);
    const total = 8 + size + (size & 1);
    if (id !== "EXIF" && id !== "XMP " && id !== "C2PA" && id !== "c2pa") chunks.push(data.subarray(pos, pos + total));
    pos += total;
  }
  const payload = concatBytes(chunks);
  const out = new Uint8Array(12 + payload.length);
  out.set([0x52, 0x49, 0x46, 0x46], 0);
  const riffSize = 4 + payload.length;
  out[4] = riffSize & 0xff;
  out[5] = (riffSize >>> 8) & 0xff;
  out[6] = (riffSize >>> 16) & 0xff;
  out[7] = (riffSize >>> 24) & 0xff;
  out.set([0x57, 0x45, 0x42, 0x50], 8);
  out.set(payload, 12);
  return out;
}

function mimeFor(format) {
  return {
    jpeg: "image/jpeg",
    png: "image/png",
    webp: "image/webp",
    avif: "image/avif",
  }[format] || "application/octet-stream";
}

function extFor(format, originalName) {
  const original = originalName.split(".").pop()?.toLowerCase();
  if (format === "jpeg") return original === "jpeg" ? "jpeg" : "jpg";
  if (format === "png") return "png";
  if (format === "webp") return "webp";
  if (format === "avif") return "avif";
  return original || "bin";
}

function cleanedName(originalName, format, addSuffix) {
  const base = originalName.replace(/^.*[\\/]/, "");
  const stem = base.replace(/\.[^.]+$/, "") || "image";
  const ext = extFor(format, base);
  return addSuffix ? `${stem}-clean.${ext}` : `${stem}.${ext}`;
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
  const pct = total ? Math.round((done / total) * 100) : 0;
  elements.progress.style.width = `${pct}%`;
}

function updateStats() {
  const queued = state.items.length;
  const done = state.items.filter((item) => item.status === "done").length;
  const failed = state.items.filter((item) => item.status === "error").length;
  const removed = state.items.reduce((sum, item) => sum + (item.strippedBytes || 0), 0);
  elements.queuedCount.textContent = String(queued);
  elements.doneCount.textContent = String(done);
  elements.removedCount.textContent = removed ? formatBytes(removed) : "0";
  elements.fileCount.textContent = `${queued} file${queued === 1 ? "" : "s"}`;
  elements.queueSummary.textContent = failed
    ? `${done} cleaned · ${failed} failed`
    : done
      ? `${done} cleaned`
      : queued
        ? "Ready to strip"
        : "";
  const hasFiles = queued > 0 && !state.running;
  const hasResults = state.items.some((item) => item.status === "done" && item.blob);
  elements.stripButton.disabled = !hasFiles;
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

async function stripViaBackend(file, stripIcc) {
  const typed = file.type?.startsWith("image/")
    ? file
    : new File([file], file.name, { type: "image/avif" });
  const body = new FormData();
  body.append("file", typed);
  const response = await fetch(`/api/metadata/strip?strip_icc=${stripIcc ? "true" : "false"}`, {
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
  const buffer = new Uint8Array(await response.arrayBuffer());
  return {
    bytes: buffer,
    format: response.headers.get("X-Metadata-Format") || detectFormat(buffer) || "unknown",
    method: response.headers.get("X-Metadata-Method") || "pillow",
    originalSize: Number(response.headers.get("X-Metadata-Original-Size") || file.size),
    strippedSize: Number(response.headers.get("X-Metadata-Stripped-Size") || buffer.length),
    elapsedMs: Number(response.headers.get("X-Metadata-Elapsed-Ms") || 0),
  };
}

async function stripOne(item, stripIcc, addSuffix) {
  const started = performance.now();
  const source = new Uint8Array(await item.file.arrayBuffer());
  const format = detectFormat(source);
  let bytes;
  let method = "raw";
  let elapsedMs;

  if (format === "jpeg") {
    bytes = stripJpeg(source, stripIcc);
    elapsedMs = performance.now() - started;
  } else if (format === "png") {
    bytes = stripPng(source);
    elapsedMs = performance.now() - started;
  } else if (format === "webp") {
    bytes = stripWebp(source);
    elapsedMs = performance.now() - started;
  } else {
    const remote = await stripViaBackend(item.file, stripIcc);
    bytes = remote.bytes;
    method = remote.method;
    elapsedMs = remote.elapsedMs || (performance.now() - started);
    item.format = remote.format;
    item.outputName = cleanedName(item.file.name, remote.format, addSuffix);
    item.blob = new Blob([bytes], { type: mimeFor(remote.format) });
    item.strippedBytes = Math.max(0, remote.originalSize - remote.strippedSize);
    item.message = `${formatBytes(item.strippedBytes)} removed · ${elapsedMs.toFixed(1)} ms · ${method}`;
    return;
  }

  const outFormat = format;
  item.format = outFormat;
  item.outputName = cleanedName(item.file.name, outFormat, addSuffix);
  item.blob = new Blob([bytes], { type: mimeFor(outFormat) });
  item.strippedBytes = Math.max(0, source.length - bytes.length);
  item.method = method;
  item.message = item.strippedBytes
    ? `${formatBytes(item.strippedBytes)} removed · ${elapsedMs.toFixed(2)} ms`
    : `Already clean · ${elapsedMs.toFixed(2)} ms`;
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

async function stripAll() {
  if (state.running || !state.items.length) return;
  state.running = true;
  const stripIcc = elements.stripIcc.checked;
  const addSuffix = elements.cleanSuffix.checked;
  elements.stripButton.textContent = "Stripping…";
  updateStats();

  let finished = 0;
  setProgress(0, state.items.length);

  await mapPool(state.items, CONCURRENCY, async (item) => {
    item.status = "working";
    item.statusLabel = "Working";
    item.message = "Stripping metadata…";
    renderQueue();
    try {
      await stripOne(item, stripIcc, addSuffix);
      item.status = "done";
      item.statusLabel = "Clean";
    } catch (error) {
      item.status = "error";
      item.statusLabel = "Failed";
      item.message = error.message || "Could not strip this image.";
      item.blob = null;
    }
    finished += 1;
    setProgress(finished, state.items.length);
    renderQueue();
    updateStats();
  });

  state.running = false;
  elements.stripButton.textContent = "Strip metadata";
  updateStats();

  const cleaned = state.items.filter((item) => item.status === "done");
  if (!cleaned.length) {
    showToast("No images could be cleaned.");
    return;
  }
  if (cleaned.length === 1) {
    triggerDownload(cleaned[0].blob, cleaned[0].outputName);
    showToast("Metadata removed. Download started.");
    return;
  }
  showToast(`${cleaned.length} images cleaned. Download ZIP or save to a folder.`);
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
    const response = await fetch("/api/metadata/pack", { method: "POST", body: form });
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
    showToast(`${written} clean images saved.`);
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
  const files = await filesFromDrop(event);
  addFiles(files);
});
elements.fileInput.addEventListener("change", () => {
  addFiles([...elements.fileInput.files]);
  elements.fileInput.value = "";
});
elements.stripButton.addEventListener("click", stripAll);
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

checkHealth();
updateStats();
