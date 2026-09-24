// ==========================================================================
// Animagine Studio — frontend logic
// ==========================================================================

const DEFAULT_NEGATIVE_PROMPT =
  "lowres, bad anatomy, bad hands, text, error, missing finger, extra digits, " +
  "fewer digits, cropped, worst quality, low quality, low score, bad score, " +
  "average score, signature, watermark, username, blurry";

const DEFAULT_PROMPT = "1girl, solo, outdoors, looking at viewer, masterpiece, high score, great score, absurdres";

const QUICK_TAGS = ["masterpiece", "high score", "great score", "absurdres"];

const RESOLUTIONS = {
  square: { w: 1024, h: 1024 },
  portrait: { w: 832, h: 1216 },
  landscape: { w: 1216, h: 832 },
  tall: { w: 896, h: 1152 },
  wide: { w: 1152, h: 896 },
};

const STORAGE_KEY = "animagineStudioState";

// --- DOM references --------------------------------------------------------
const el = (id) => document.getElementById(id);

const promptInput = el("promptInput");
const negativeInput = el("negativeInput");
const tagSuggestions = el("tagSuggestions");
const quickTagsWrap = el("quickTags");

const stylePanel = el("stylePanel");
const stylePreset = el("stylePreset");
const loraScaleWrap = el("loraScaleWrap");
const loraScale = el("loraScale");
const loraScaleValue = el("loraScaleValue");

const numImagesInput = el("numImages");
const resolutionPreset = el("resolutionPreset");
const customRes = el("customRes");
const customWidth = el("customWidth");
const customHeight = el("customHeight");
const aspectHint = el("aspectHint");

const cfgScale = el("cfgScale");
const cfgValue = el("cfgValue");
const stepsRange = el("stepsRange");
const stepsValue = el("stepsValue");
const seedInput = el("seedInput");

const generateBtn = el("generateBtn");
const generateBtnLabel = el("generateBtnLabel");
const errorBanner = el("errorBanner");

const gallery = el("gallery");
const emptyState = el("emptyState");
const downloadAllBtn = el("downloadAllBtn");

const settingsToggle = el("settingsToggle");
const settingsPanel = el("settingsPanel");
const closeSettings = el("closeSettings");
const backendUrlInput = el("backendUrl");
const apiKeyInput = el("apiKey");
const testConnectionBtn = el("testConnectionBtn");
const testResult = el("testResult");
const connectionStatus = el("connectionStatus");

const controlsToggle = el("controlsToggle");
const sidebar = el("sidebar");

const lightbox = el("lightbox");
const lightboxImage = el("lightboxImage");
const lightboxMeta = el("lightboxMeta");
const lightboxClose = el("lightboxClose");
const lightboxPrev = el("lightboxPrev");
const lightboxNext = el("lightboxNext");
const lightboxDownload = el("lightboxDownload");
const filmstrip = el("filmstrip");

let galleryImages = []; // newest first: {id, src, seed, width, height, prompt}
let lightboxIndex = -1;

let availableStyles = []; // [{id, name, trigger_word, default_scale}, ...] from GET /api/styles
let pendingStyleId = "none"; // style to restore from saved state, once the list is populated

// ==========================================================================
// State persistence (settings only — generated images are not persisted)
// ==========================================================================
function loadState() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch {
    return {};
  }
}

function saveState(patch) {
  const current = loadState();
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...current, ...patch }));
}

function initFromState() {
  const s = loadState();
  promptInput.value = s.prompt ?? DEFAULT_PROMPT;
  negativeInput.value = s.negative ?? DEFAULT_NEGATIVE_PROMPT;
  numImagesInput.value = s.numImages ?? 1;
  resolutionPreset.value = s.resolutionPreset ?? "portrait";
  customWidth.value = s.customWidth ?? 832;
  customHeight.value = s.customHeight ?? 1216;
  cfgScale.value = s.cfg ?? 5;
  cfgValue.textContent = Number(cfgScale.value).toFixed(1);
  stepsRange.value = s.steps ?? 28;
  stepsValue.textContent = stepsRange.value;
  seedInput.value = s.seed ?? -1;
  backendUrlInput.value = s.backendUrl ?? "";
  apiKeyInput.value = s.apiKey ?? "";

  pendingStyleId = s.style ?? "none";
  loraScale.value = s.loraScale ?? 0.8;
  loraScaleValue.textContent = Number(loraScale.value).toFixed(2);

  onResolutionChange();
  renderQuickTags();

  if (s.backendUrl) checkConnection(s.backendUrl, s.apiKey ?? "");
}

// ==========================================================================
// Quick tags
// ==========================================================================
function renderQuickTags() {
  quickTagsWrap.innerHTML = "";
  QUICK_TAGS.forEach((tag) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "quick-tag";
    btn.textContent = tag;
    if (promptHasTag(tag)) btn.classList.add("active");
    btn.addEventListener("click", () => toggleQuickTag(tag, btn));
    quickTagsWrap.appendChild(btn);
  });
}

function promptHasTag(tag) {
  return promptInput.value
    .split(",")
    .map((t) => t.trim().toLowerCase())
    .includes(tag.toLowerCase());
}

function toggleQuickTag(tag, btn) {
  let parts = promptInput.value.split(",").map((t) => t.trim()).filter(Boolean);
  const lower = tag.toLowerCase();
  if (parts.map((p) => p.toLowerCase()).includes(lower)) {
    parts = parts.filter((p) => p.toLowerCase() !== lower);
    btn.classList.remove("active");
  } else {
    parts.push(tag);
    btn.classList.add("active");
  }
  promptInput.value = parts.join(", ");
}

// ==========================================================================
// Danbooru tag autocomplete
// ==========================================================================
let suggestionItems = [];
let activeSuggestion = -1;

function debounce(fn, wait) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

const onPromptInput = debounce(() => {
  const fragment = currentTagFragment();
  if (fragment.length < 2) {
    hideSuggestions();
    return;
  }
  fetch(`/api/tags?q=${encodeURIComponent(fragment)}`)
    .then((r) => r.json())
    .then((tags) => renderSuggestions(tags, fragment))
    .catch(() => hideSuggestions());
}, 250);

promptInput.addEventListener("input", () => {
  onPromptInput();
  renderQuickTags();
});

function currentTagFragment() {
  const cursor = promptInput.selectionStart;
  const before = promptInput.value.slice(0, cursor);
  const lastComma = before.lastIndexOf(",");
  return before.slice(lastComma + 1).trim();
}

function highlightMatch(text, query) {
  if (!query) return escapeHtml(text);
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx === -1) return escapeHtml(text);
  const before = text.slice(0, idx);
  const match = text.slice(idx, idx + query.length);
  const after = text.slice(idx + query.length);
  return `${escapeHtml(before)}<strong>${escapeHtml(match)}</strong>${escapeHtml(after)}`;
}

function renderSuggestions(tags, fragment) {
  suggestionItems = tags;
  // First result pre-selected: typing + pressing Enter/Tab completes the tag
  // right away, no arrow-key press needed first.
  activeSuggestion = tags.length ? 0 : -1;
  if (!tags.length) {
    hideSuggestions();
    return;
  }
  tagSuggestions.innerHTML = "";
  tags.forEach((tag, i) => {
    const li = document.createElement("li");
    li.className = "tag-suggestion";
    li.dataset.index = i;
    const count = tag.post_count >= 1000 ? `${(tag.post_count / 1000).toFixed(1)}k` : tag.post_count;
    const displayName = tag.name.replace(/_/g, " "); // shown with spaces; the underscore form is still what gets inserted

    let nameHtml;
    if (tag.alias_of) {
      const oldDisplay = tag.alias_of.replace(/_/g, " ");
      nameHtml = `${highlightMatch(oldDisplay, fragment)} <span class="alias-arrow">→</span> ${escapeHtml(displayName)}`;
    } else {
      nameHtml = highlightMatch(displayName, fragment);
    }

    li.innerHTML = `<span class="tag-cat-dot tag-cat-${tag.category}"></span><span class="name">${nameHtml}</span><span class="count">${count}</span>`;
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      applySuggestion(tag.name);
    });
    tagSuggestions.appendChild(li);
  });
  tagSuggestions.classList.remove("hidden");
  highlightSuggestion();
}

function hideSuggestions() {
  tagSuggestions.classList.add("hidden");
  suggestionItems = [];
  activeSuggestion = -1;
}

function applySuggestion(tagName) {
  const cursor = promptInput.selectionStart;
  const value = promptInput.value;
  const before = value.slice(0, cursor);
  const after = value.slice(cursor);
  const lastComma = before.lastIndexOf(",");
  const prefix = lastComma === -1 ? "" : before.slice(0, lastComma + 1) + " ";
  const newValue = `${prefix}${tagName}, ${after.replace(/^\s*,?\s*/, "")}`;
  promptInput.value = newValue;
  const newCursor = `${prefix}${tagName}, `.length;
  promptInput.setSelectionRange(newCursor, newCursor);
  promptInput.focus();
  hideSuggestions();
  renderQuickTags();
}

promptInput.addEventListener("keydown", (e) => {
  if (tagSuggestions.classList.contains("hidden")) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeSuggestion = Math.min(activeSuggestion + 1, suggestionItems.length - 1);
    highlightSuggestion();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeSuggestion = Math.max(activeSuggestion - 1, 0);
    highlightSuggestion();
  } else if ((e.key === "Enter" || e.key === "Tab") && activeSuggestion >= 0) {
    // Tab/Enter completes the highlighted (by default, top) prediction —
    // press it and the tag is finished, no need to reach for the mouse.
    e.preventDefault();
    applySuggestion(suggestionItems[activeSuggestion].name);
  } else if (e.key === "Escape") {
    hideSuggestions();
  }
});

function highlightSuggestion() {
  [...tagSuggestions.children].forEach((li, i) => li.classList.toggle("active", i === activeSuggestion));
}

document.addEventListener("click", (e) => {
  if (!tagSuggestions.contains(e.target) && e.target !== promptInput) hideSuggestions();
});


function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

// ==========================================================================
// Resolution controls
// ==========================================================================
function gcd(a, b) {
  return b === 0 ? a : gcd(b, a % b);
}

function updateAspectHint(w, h) {
  const g = gcd(w, h) || 1;
  aspectHint.textContent = `Aspect ratio ${w / g}:${h / g} — ${w}×${h}`;
}

function onResolutionChange() {
  const preset = resolutionPreset.value;
  if (preset === "custom") {
    customRes.classList.remove("hidden");
    updateAspectHint(Number(customWidth.value), Number(customHeight.value));
  } else {
    customRes.classList.add("hidden");
    const { w, h } = RESOLUTIONS[preset];
    updateAspectHint(w, h);
  }
}

resolutionPreset.addEventListener("change", onResolutionChange);
customWidth.addEventListener("input", () => updateAspectHint(Number(customWidth.value), Number(customHeight.value)));
customHeight.addEventListener("input", () => updateAspectHint(Number(customWidth.value), Number(customHeight.value)));

function currentResolution() {
  if (resolutionPreset.value === "custom") {
    return { width: Number(customWidth.value), height: Number(customHeight.value) };
  }
  const { w, h } = RESOLUTIONS[resolutionPreset.value];
  return { width: w, height: h };
}

// ==========================================================================
// Steppers / range labels
// ==========================================================================
document.querySelectorAll(".stepper-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const step = Number(btn.dataset.step);
    const next = Math.min(8, Math.max(1, Number(numImagesInput.value) + step));
    numImagesInput.value = next;
  });
});

cfgScale.addEventListener("input", () => (cfgValue.textContent = Number(cfgScale.value).toFixed(1)));
stepsRange.addEventListener("input", () => (stepsValue.textContent = stepsRange.value));

// ==========================================================================
// Settings panel + connection status
// ==========================================================================
function openSettings() {
  settingsPanel.classList.remove("hidden");
}
function hideSettingsPanel() {
  settingsPanel.classList.add("hidden");
}

settingsToggle.addEventListener("click", openSettings);
closeSettings.addEventListener("click", () => {
  saveState({ backendUrl: backendUrlInput.value.trim(), apiKey: apiKeyInput.value.trim() });
  hideSettingsPanel();
  checkConnection(backendUrlInput.value.trim(), apiKeyInput.value.trim());
});

testConnectionBtn.addEventListener("click", async () => {
  testResult.textContent = "Checking…";
  const ok = await checkConnection(backendUrlInput.value.trim(), apiKeyInput.value.trim());
  testResult.textContent = ok ? "Connected." : "Couldn't connect — check the URL and API key.";
});

async function checkConnection(backendUrl, apiKey) {
  if (!backendUrl) {
    setStatusPill("unknown", "Not connected");
    return false;
  }
  setStatusPill("unknown", "Checking…");
  try {
    const resp = await fetch(`/api/status?backend_url=${encodeURIComponent(backendUrl)}&api_key=${encodeURIComponent(apiKey)}`);
    const data = await resp.json();
    if (data.ok) {
      setStatusPill("ok", "Connected");
      fetchStyles(backendUrl, apiKey);
      return true;
    }
    setStatusPill("error", "Connection failed");
    return false;
  } catch {
    setStatusPill("error", "Connection failed");
    return false;
  }
}

// ==========================================================================
// Style / LoRA selector — populated from whatever the connected backend has
// configured (see LORAS in colab_backend.py). Hidden entirely if the backend
// has no styles loaded.
// ==========================================================================
async function fetchStyles(backendUrl, apiKey) {
  try {
    const resp = await fetch(`/api/styles?backend_url=${encodeURIComponent(backendUrl)}&api_key=${encodeURIComponent(apiKey)}`);
    const data = await resp.json();
    availableStyles = data.ok && Array.isArray(data.styles) ? data.styles : [];
  } catch {
    availableStyles = [];
  }
  renderStylePreset();
}

function renderStylePreset() {
  stylePanel.classList.toggle("hidden", availableStyles.length === 0);
  if (availableStyles.length === 0) return;

  stylePreset.innerHTML = '<option value="none">None (base model)</option>';
  availableStyles.forEach((style) => {
    const opt = document.createElement("option");
    opt.value = style.id;
    opt.textContent = style.name;
    stylePreset.appendChild(opt);
  });

  // Restore the previously selected style, if it's still offered.
  const hasPending = availableStyles.some((s) => s.id === pendingStyleId);
  stylePreset.value = hasPending ? pendingStyleId : "none";
  onStyleChange(/* keepSavedScale */ true);
}

function onStyleChange(keepSavedScale) {
  const selected = availableStyles.find((s) => s.id === stylePreset.value);
  loraScaleWrap.classList.toggle("hidden", !selected);
  if (selected && !keepSavedScale) {
    loraScale.value = selected.default_scale ?? 0.8;
    loraScaleValue.textContent = Number(loraScale.value).toFixed(2);
  }
}

stylePreset.addEventListener("change", () => onStyleChange(false));
loraScale.addEventListener("input", () => (loraScaleValue.textContent = Number(loraScale.value).toFixed(2)));

function setStatusPill(state, label) {
  connectionStatus.classList.remove("status-unknown", "status-ok", "status-error");
  connectionStatus.classList.add(`status-${state}`);
  connectionStatus.querySelector(".status-label").textContent = label;
}

connectionStatus.addEventListener("click", openSettings);

// --- Mobile sidebar toggle ---
controlsToggle.addEventListener("click", () => sidebar.classList.toggle("open"));

// ==========================================================================
// Generation
// ==========================================================================
function readSettings() {
  const { width, height } = currentResolution();
  return {
    prompt: promptInput.value.trim(),
    negative: negativeInput.value.trim(),
    numImages: Math.min(8, Math.max(1, Number(numImagesInput.value))),
    resolutionPreset: resolutionPreset.value,
    customWidth: Number(customWidth.value),
    customHeight: Number(customHeight.value),
    width,
    height,
    cfg: Number(cfgScale.value),
    steps: Number(stepsRange.value),
    seed: Number(seedInput.value),
    style: stylePreset.value || "none",
    loraScale: Number(loraScale.value),
    backendUrl: (loadState().backendUrl || "").trim(),
    apiKey: (loadState().apiKey || "").trim(),
  };
}

function showError(message) {
  errorBanner.textContent = message;
  errorBanner.classList.remove("hidden");
}
function clearError() {
  errorBanner.classList.add("hidden");
}

let loadingTimer = null;

function setLoading(isLoading) {
  generateBtn.disabled = isLoading;
  if (isLoading) {
    const start = Date.now();
    loadingTimer = setInterval(() => {
      generateBtnLabel.textContent = `Rendering… ${Math.floor((Date.now() - start) / 1000)}s`;
    }, 500);
  } else {
    clearInterval(loadingTimer);
    generateBtnLabel.textContent = "Generate";
  }
}

generateBtn.addEventListener("click", async () => {
  clearError();
  const s = readSettings();

  saveState({
    prompt: s.prompt,
    negative: s.negative,
    numImages: s.numImages,
    resolutionPreset: s.resolutionPreset,
    customWidth: s.customWidth,
    customHeight: s.customHeight,
    cfg: s.cfg,
    steps: s.steps,
    seed: s.seed,
    style: s.style,
    loraScale: s.loraScale,
  });

  if (!s.prompt) {
    showError("Write at least one tag in the prompt field.");
    return;
  }
  if (!s.backendUrl) {
    showError("Set your Colab backend URL in settings first.");
    openSettings();
    return;
  }

  setLoading(true);
  try {
    const resp = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        backend_url: s.backendUrl,
        api_key: s.apiKey,
        prompt: s.prompt,
        negative_prompt: s.negative,
        num_images: s.numImages,
        width: s.width,
        height: s.height,
        guidance_scale: s.cfg,
        steps: s.steps,
        seed: s.seed,
        style: s.style && s.style !== "none" ? s.style : null,
        lora_scale: s.style && s.style !== "none" ? s.loraScale : null,
      }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || data.detail || "Generation failed.");

    addImagesToGallery(data.images, {
      prompt: data.meta.prompt || s.prompt,
      width: data.meta.width,
      height: data.meta.height,
      style: data.meta.style,
    });
    setStatusPill("ok", "Connected");
  } catch (err) {
    showError(err.message);
  } finally {
    setLoading(false);
  }
});

// ==========================================================================
// Gallery
// ==========================================================================
function addImagesToGallery(images, meta) {
  const newItems = images.map((img) => ({
    id: `${Date.now()}-${img.seed}-${Math.random().toString(36).slice(2, 7)}`,
    src: `data:image/png;base64,${img.base64}`,
    seed: img.seed,
    width: meta.width,
    height: meta.height,
    prompt: meta.prompt,
    style: meta.style,
  }));
  galleryImages = [...newItems, ...galleryImages];
  renderGallery();
}

function renderGallery() {
  emptyState.classList.toggle("hidden", galleryImages.length > 0);
  downloadAllBtn.classList.toggle("hidden", galleryImages.length === 0);

  gallery.innerHTML = "";
  galleryImages.forEach((item, index) => {
    const card = document.createElement("div");
    card.className = "gallery-item";
    card.innerHTML = `
      <img src="${item.src}" alt="Generated image, seed ${item.seed}" loading="lazy" />
      <div class="overlay">
        <span class="seed-badge">#${item.seed}</span>
        <button class="dl-btn" aria-label="Download">↓</button>
      </div>`;
    card.querySelector("img").addEventListener("click", () => openLightbox(index));
    card.querySelector(".dl-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      downloadImage(item);
    });
    gallery.appendChild(card);
  });
}

function downloadImage(item) {
  const a = document.createElement("a");
  a.href = item.src;
  a.download = `animagine_${item.seed}.png`;
  a.click();
}

downloadAllBtn.addEventListener("click", async () => {
  if (!galleryImages.length) return;
  downloadAllBtn.textContent = "Zipping…";
  const zip = new JSZip();
  galleryImages.forEach((item) => {
    zip.file(`animagine_${item.seed}.png`, item.src.split(",")[1], { base64: true });
  });
  const blob = await zip.generateAsync({ type: "blob" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "animagine_studio_batch.zip";
  a.click();
  URL.revokeObjectURL(url);
  downloadAllBtn.textContent = "Download all (.zip)";
});

// ==========================================================================
// Lightbox
// ==========================================================================
function openLightbox(index) {
  lightboxIndex = index;
  renderLightbox();
  lightbox.classList.remove("hidden");
}

function closeLightbox() {
  lightbox.classList.add("hidden");
}

function renderLightbox() {
  const item = galleryImages[lightboxIndex];
  if (!item) return;
  lightboxImage.src = item.src;
  const styleTag = item.style ? ` · style: ${item.style}` : "";
  lightboxMeta.textContent = `seed ${item.seed} · ${item.width}×${item.height}${styleTag} · ${item.prompt}`;

  filmstrip.innerHTML = "";
  galleryImages.forEach((img, i) => {
    const thumb = document.createElement("img");
    thumb.src = img.src;
    thumb.className = i === lightboxIndex ? "active" : "";
    thumb.addEventListener("click", () => {
      lightboxIndex = i;
      renderLightbox();
    });
    filmstrip.appendChild(thumb);
  });
}

function stepLightbox(delta) {
  if (!galleryImages.length) return;
  lightboxIndex = (lightboxIndex + delta + galleryImages.length) % galleryImages.length;
  renderLightbox();
}

lightboxClose.addEventListener("click", closeLightbox);
lightboxPrev.addEventListener("click", () => stepLightbox(-1));
lightboxNext.addEventListener("click", () => stepLightbox(1));
lightboxDownload.addEventListener("click", () => downloadImage(galleryImages[lightboxIndex]));

lightbox.addEventListener("click", (e) => {
  if (e.target === lightbox) closeLightbox();
});

document.addEventListener("keydown", (e) => {
  if (lightbox.classList.contains("hidden")) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowLeft") stepLightbox(-1);
  if (e.key === "ArrowRight") stepLightbox(1);
});

// ==========================================================================
// Init
// ==========================================================================
initFromState();
