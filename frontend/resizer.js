const IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "webp", "gif", "tif", "tiff", "bmp"]);
const CONCURRENCY = 4;

const $ = (selector, root = document) => root.querySelector(selector);

const elements = {
  dropZone: $("#dropZone"),
  fileInput: $("#resizerFileInput"),
  fileCount: $("#fileCount"),
  queuedCount: $("#queuedCount"),
  doneCount: $("#doneCount"),
  totalCount: $("#totalCount"),
  targetWidth: $("#targetWidth"),
  targetHeight: $("#targetHeight"),
  resizeMode: $("#resizeMode"),
  resizeSuffix: $("#resizeSuffix"),
  clearButton: $("#clearResizerButton"),
  saveFolderButton: $("#saveFolderButton"),
  downloadZipButton: $("#downloadZipButton"),
  resizeButton: $("#resizeButton"),
  queueSection: $("#resizerQueueSection"),
  queueList: $("#resizerQueueList"),
  queueSummary: $("#resizerQueueSummary"),
  progress: $("#resizerProgressBar"),
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

function resizedName(originalName, contentType) {
  const base = originalName.replace(/^.*[\\/]/, "");
  const ext = extensionForType(contentType, base);
  const stem = base.replace(/\.[^.]+$/, "") || "image";
  if (!elements.resizeSuffix.checked) return `${stem}${ext}`;
  return `${stem}-resized${ext}`;
}

function updateStats() {
  const queued = state.items.length;
  const done = state.items.filter((item) => item.status === "done").length;
  const failed = state.items.filter((item) => item.status === "error").length;
  elements.queuedCount.textContent = String(queued);
  elements.doneCount.textContent = String(done);
  elements.totalCount.textContent = String(state.totalProcessed);
  elements.fileCount.textContent = `${queued} file${queued === 1 ? "" : "s"}`;
  elements.queueSummary.textContent = failed
    ? `${done} resized · ${failed} failed`
    : done
      ? `${done} resized`
      : queued
        ? "Ready to resize"
        : "";
  const w = parseInt(elements.targetWidth.value, 10);
  const h = parseInt(elements.targetHeight.value, 10);
  const hasValidDimensions = w > 0 && h > 0;
  const hasFiles = queued > 0 && !state.running;
  const hasResults = state.items.some((item) => item.status === "done" && item.blob);
  elements.resizeButton.disabled = !(hasFiles && hasValidDimensions);
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

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Could not load image."));
    img.src = URL.createObjectURL(file);
  });
}

function outputMimeType(file) {
  const type = (file.type || "").split(";")[0].toLowerCase();
  if (type === "image/png") return "image/png";
  return "image/jpeg";
}

async function resizeOne(item, targetW, targetH, resizeMode) {
  const img = await loadImage(item.file);
  const canvas = document.createElement("canvas");
  canvas.width = targetW;
  canvas.height = targetH;
  const ctx = canvas.getContext("2d");

  const mime = outputMimeType(item.file);

  if (mime === "image/jpeg" || mime === "image/png") {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, targetW, targetH);
  }

  let drawW, drawH, offsetX, offsetY;

  if (resizeMode === "stretch") {
    drawW = targetW;
    drawH = targetH;
    offsetX = 0;
    offsetY = 0;
  } else if (resizeMode === "crop-center") {
    drawW = img.naturalWidth;
    drawH = img.naturalHeight;
    offsetX = Math.round((targetW - drawW) / 2);
    offsetY = Math.round((targetH - drawH) / 2);
  } else {
    const scaleW = targetW / img.naturalWidth;
    const scaleH = targetH / img.naturalHeight;
    const scale = resizeMode === "cover" ? Math.max(scaleW, scaleH) : Math.min(scaleW, scaleH);
    drawW = Math.round(img.naturalWidth * scale);
    drawH = Math.round(img.naturalHeight * scale);
    offsetX = Math.round((targetW - drawW) / 2);
    offsetY = Math.round((targetH - drawH) / 2);
  }

  ctx.drawImage(img, offsetX, offsetY, drawW, drawH);

  URL.revokeObjectURL(img.src);

  const quality = mime === "image/jpeg" ? 0.92 : undefined;
  const blob = await new Promise((resolve) =>
    canvas.toBlob(resolve, mime, quality)
  );
  if (!blob) throw new Error("Canvas export failed.");

  item.blob = blob;
  item.outputName = resizedName(item.file.name, blob.type);
  const originalSize = item.file.size;
  const parts = [
    `${img.naturalWidth}×${img.naturalHeight} → ${targetW}×${targetH}`,
    formatBytes(blob.size),
  ];
  if (blob.size < originalSize) {
    parts.push(`${formatBytes(originalSize - blob.size)} smaller`);
  }
  item.message = parts.join(" · ");
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

async function resizeAll() {
  if (state.running || !state.items.length) return;
  const targetW = parseInt(elements.targetWidth.value, 10);
  const targetH = parseInt(elements.targetHeight.value, 10);
  if (!targetW || !targetH || targetW < 1 || targetH < 1) {
    showToast("Enter a valid width and height first.");
    return;
  }
  state.running = true;
  const resizeMode = elements.resizeMode.value;
  elements.resizeButton.textContent = "Resizing…";
  updateStats();
  let finished = 0;
  setProgress(0, state.items.length);

  await mapPool(state.items, CONCURRENCY, async (item) => {
    item.status = "working";
    item.statusLabel = "Working";
    item.message = `Resizing to ${targetW}×${targetH}…`;
    renderQueue();
    try {
      await resizeOne(item, targetW, targetH, resizeMode);
      item.status = "done";
      item.statusLabel = "Done";
      state.totalProcessed += 1;
    } catch (error) {
      item.status = "error";
      item.statusLabel = "Failed";
      item.message = error.message || "Could not resize this image.";
      item.blob = null;
    }
    finished += 1;
    setProgress(finished, state.items.length);
    renderQueue();
    updateStats();
  });

  state.running = false;
  elements.resizeButton.textContent = "Resize images";
  updateStats();

  const resized = state.items.filter((item) => item.status === "done");
  if (!resized.length) {
    showToast("No images could be resized.");
    return;
  }
  if (resized.length === 1) {
    triggerDownload(resized[0].blob, resized[0].outputName);
    showToast("Image resized. Download started.");
    return;
  }
  showToast(`${resized.length} images resized to ${targetW}×${targetH}. Download ZIP or save to a folder.`);
}

async function downloadZip() {
  const resized = state.items.filter((item) => item.status === "done" && item.blob);
  if (!resized.length) return;
  elements.downloadZipButton.disabled = true;
  elements.downloadZipButton.textContent = "Building ZIP…";
  try {
    const used = new Set();
    const form = new FormData();
    for (const item of resized) {
      form.append("files", item.blob, uniqueOutputName(item.outputName, used));
    }
    const response = await fetch("/api/logo/pack", { method: "POST", body: form });
    if (!response.ok) {
      throw new Error("The ZIP could not be created.");
    }
    const zip = await response.blob();
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    triggerDownload(zip, `resized-images-${stamp}.zip`);
    showToast("ZIP download started.");
  } catch (error) {
    showToast(error.message || "The ZIP could not be created.");
  } finally {
    elements.downloadZipButton.textContent = "Download ZIP";
    updateStats();
  }
}

async function saveToFolder() {
  const resized = state.items.filter((item) => item.status === "done" && item.blob);
  if (!resized.length || !window.showDirectoryPicker) return;
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
    for (const item of resized) {
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

// Event listeners
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
elements.resizeButton.addEventListener("click", resizeAll);
elements.downloadZipButton.addEventListener("click", downloadZip);
elements.saveFolderButton.addEventListener("click", saveToFolder);
elements.clearButton.addEventListener("click", clearAll);
elements.targetWidth.addEventListener("input", updateStats);
elements.targetHeight.addEventListener("input", updateStats);
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
