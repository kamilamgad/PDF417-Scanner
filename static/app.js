const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const cardGuide = document.getElementById("cardGuide");
const statusEl = document.getElementById("status");
const tipsEl = document.getElementById("tips");
const resultJson = document.getElementById("resultJson");
const resultSummary = document.getElementById("resultSummary");
const resultGrid = document.getElementById("resultGrid");
const successPanel = document.getElementById("successPanel");
const copyJsonBtn = document.getElementById("copyJsonBtn");
const copySummaryBtn = document.getElementById("copySummaryBtn");

const stopCameraBtn = document.getElementById("stopCameraBtn");
const torchBtn = document.getElementById("torchBtn");
const scanBurstBtn = document.getElementById("scanBurstBtn");
const autoScanToggle = document.getElementById("autoScanToggle");
const burstCountInput = document.getElementById("burstCount");
const burstGapInput = document.getElementById("burstGap");
const captureInput = document.getElementById("captureInput");

let stream = null;
let running = false;
let scanning = false;
let torchOn = false;
let currentTrack = null;
let consecutiveFailures = 0;
let cameraReadyAtMs = 0;
let previousMotionSignature = null;

const API_BASE = window.location.origin;
const QUALITY_MODE = "strict"; // strict | balanced
const STRICT_CYCLES_BEFORE_RELAX = 6;
const RELAXED_MODE_DURATION_CYCLES = 4;
const MAX_TOTAL_SCAN_CYCLES = 20;
const BURST_REQUEST_TIMEOUT_MS = 45000;
let runtimeQualityMode = QUALITY_MODE;
const LAST_SUCCESS_PROFILE_KEY = "pdf417_last_success_profile_v1";
const LEARNING_PROFILE_KEY = "pdf417_learning_profile_v1";
let qualityCalibration = {
  loaded: false,
  targetSharpness: 130,
  targetEdgeSignal: 14,
  targetGlareRatio: 0.08,
  targetDarkRatio: 0.2,
  targetMean: 130,
};
let learningProfile = {
  successfulFrameTypes: { full: 0, guide: 0, strip: 0 },
  successfulTransforms: {},
  preferredTransformHint: "",
};

const FIELD_LABELS = [
  ["firstName", "First Name"],
  ["middleName", "Middle Name"],
  ["lastName", "Last Name"],
  ["dateOfBirth", "Date of Birth"],
  ["gender", "Gender"],
  ["driverClass", "Driver Class"],
  ["licenseNumber", "License Number"],
  ["documentNumber", "Document Number"],
  ["addressLine1", "Address Line 1"],
  ["addressLine2", "Address Line 2"],
  ["city", "City"],
  ["state", "State"],
  ["postalCode", "Postal Code"],
  ["issueDate", "Issue Date"],
  ["expirationDate", "Expiration Date"],
];

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.className = isError ? "status error" : "status";
}

function setTips(tips = []) {
  tipsEl.innerHTML = "";
  tips.forEach((tip) => {
    const li = document.createElement("li");
    li.textContent = tip;
    tipsEl.appendChild(li);
  });
}

function summarizeQualityIssues(candidates = []) {
  const summary = {
    blurry: 0,
    glare: 0,
    badLighting: 0,
    tooSmall: 0,
    tooClose: 0,
  };
  if (!candidates.length) return summary;

  for (const c of candidates) {
    const q = c.quality || {};
    if (q.reason === "blurry") summary.blurry += 1;
    if (q.reason === "glare") summary.glare += 1;
    if (q.reason === "bad-lighting") summary.badLighting += 1;
    if (q.reason === "barcode-too-small-or-off-zone") summary.tooSmall += 1;

    // Heuristic: very high edge energy but below sharpness threshold often indicates being too close/out of focus.
    if (
      typeof q.edgeSignal === "number" &&
      typeof q.edgeThreshold === "number" &&
      typeof q.sharpness === "number" &&
      typeof q.sharpThreshold === "number" &&
      q.edgeSignal > q.edgeThreshold * 1.8 &&
      q.sharpness < q.sharpThreshold
    ) {
      summary.tooClose += 1;
    }
  }
  return summary;
}

function buildQualityPrompt(candidates = []) {
  const issues = summarizeQualityIssues(candidates);
  const tips = [];
  let headline = "Improving capture quality...";

  if (issues.tooClose > 0) {
    headline = "Move camera slightly away from the ID.";
    tips.push("Back up a little, then hold still for autofocus to lock.");
  }
  if (issues.tooSmall > 0) {
    if (!tips.some((t) => t.includes("Back up"))) {
      headline = "Move closer and center the barcode.";
    }
    tips.push("Bring the ID closer so the barcode fills more of the guide.");
  }
  if (issues.badLighting > 0) {
    tips.push("Increase lighting and avoid shadows over the barcode.");
  }
  if (issues.glare > 0) {
    tips.push("Tilt the ID slightly to remove glare spots.");
  }
  if (issues.blurry > 0) {
    tips.push("Hold steady for one second before capture.");
  }

  if (!tips.length) {
    tips.push("Align the ID in the guide and keep the barcode sharp.");
    tips.push("Use even light and avoid reflections.");
  }

  return { headline, tips: tips.slice(0, 3) };
}

function buildProfileSummary(fields = {}) {
  const fullName = [fields.firstName, fields.middleName, fields.lastName].filter(Boolean).join(" ");
  const cityStateZip = [fields.city, fields.state, fields.postalCode].filter(Boolean).join(", ").replace(", ,", ",");
  const addressParts = [fields.addressLine1, fields.addressLine2, cityStateZip].filter(Boolean);

  return [
    fullName ? `Customer: ${fullName}` : "",
    fields.dateOfBirth ? `Date of Birth: ${fields.dateOfBirth}` : "",
    fields.licenseNumber ? `License Number: ${fields.licenseNumber}` : "",
    fields.documentNumber ? `Document Number: ${fields.documentNumber}` : "",
    addressParts.length ? `Address: ${addressParts.join(", ")}` : "",
    fields.driverClass ? `Driver Class: ${fields.driverClass}` : "",
    fields.issueDate ? `Issue Date: ${fields.issueDate}` : "",
    fields.expirationDate ? `Expiration Date: ${fields.expirationDate}` : "",
  ].filter(Boolean).join("\n");
}

function renderFieldGrid(fields = {}) {
  resultGrid.innerHTML = "";
  for (const [key, label] of FIELD_LABELS) {
    const value = fields[key];
    if (!value) continue;

    const item = document.createElement("article");
    item.className = "field-card";

    const labelEl = document.createElement("span");
    labelEl.className = "field-label";
    labelEl.textContent = label;

    const valueEl = document.createElement("strong");
    valueEl.className = "field-value";
    valueEl.textContent = value;

    item.appendChild(labelEl);
    item.appendChild(valueEl);
    resultGrid.appendChild(item);
  }
}

async function copyText(text, successMessage) {
  try {
    await navigator.clipboard.writeText(text);
    setStatus(successMessage);
  } catch {
    setStatus("Copy failed. Use manual copy instead.", true);
  }
}

function showSuccess(data) {
  const fields = data.fields || {};
  const jsonText = JSON.stringify(fields, null, 2);
  const summaryText = buildProfileSummary(fields);

  successPanel.classList.remove("hidden");
  renderFieldGrid(fields);
  resultJson.textContent = jsonText;
  resultSummary.textContent = summaryText || "No profile summary available.";

  copyJsonBtn.onclick = () => copyText(jsonText, "Structured JSON copied.");
  copySummaryBtn.onclick = () => copyText(summaryText || jsonText, "Profile summary copied.");
}

function clearSuccess() {
  successPanel.classList.add("hidden");
  resultJson.textContent = "";
  resultSummary.textContent = "";
  resultGrid.innerHTML = "";
}

function getErrorMessage(err) {
  if (err && typeof err === "object" && "message" in err) {
    return err.message;
  }
  return String(err || "Unknown error");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function saveLastSuccessProfile() {
  try {
    const profile = {
      qualityMode: runtimeQualityMode,
      burstCount: Number(burstCountInput.value || 6),
      burstGap: Number(burstGapInput.value || 110),
      savedAt: Date.now(),
    };
    localStorage.setItem(LAST_SUCCESS_PROFILE_KEY, JSON.stringify(profile));
  } catch {
    // ignore storage failures
  }
}

function applyLastSuccessProfile() {
  try {
    const raw = localStorage.getItem(LAST_SUCCESS_PROFILE_KEY);
    if (!raw) return;
    const profile = JSON.parse(raw);
    if (profile && typeof profile === "object") {
      if (typeof profile.burstCount === "number") {
        burstCountInput.value = String(clamp(profile.burstCount, 3, 10));
      }
      if (typeof profile.burstGap === "number") {
        burstGapInput.value = String(clamp(profile.burstGap, 60, 500));
      }
      if (typeof profile.qualityMode === "string" && (profile.qualityMode === "strict" || profile.qualityMode === "balanced")) {
        runtimeQualityMode = profile.qualityMode;
      }
    }
  } catch {
    // ignore parse/storage errors
  }
}

function loadLearningProfile() {
  try {
    const raw = localStorage.getItem(LEARNING_PROFILE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return;
    learningProfile = {
      successfulFrameTypes: {
        full: Number(parsed.successfulFrameTypes?.full || 0),
        guide: Number(parsed.successfulFrameTypes?.guide || 0),
        strip: Number(parsed.successfulFrameTypes?.strip || 0),
      },
      successfulTransforms: parsed.successfulTransforms || {},
      preferredTransformHint: String(parsed.preferredTransformHint || ""),
    };
  } catch {
    // ignore storage/parse errors
  }
}

function saveLearningProfile() {
  try {
    localStorage.setItem(LEARNING_PROFILE_KEY, JSON.stringify(learningProfile));
  } catch {
    // ignore storage failures
  }
}

function frameTypeWeight(type) {
  const counts = learningProfile.successfulFrameTypes || {};
  const v = Number(counts[type] || 0);
  return Math.min(24, v * 3);
}

function updateLearningFromSuccess(data, selectedMeta) {
  try {
    const attempts = data?.debug?.attempts || [];
    const firstSuccess = attempts.find((a) => a && a.success);
    if (!firstSuccess) return;

    const transform = String(firstSuccess.transform || "");
    const frameMatch = transform.match(/^frame(\d+)\//i);
    if (frameMatch) {
      const idx = Number(frameMatch[1]) - 1;
      const type = selectedMeta?.[idx]?.type;
      if (type && learningProfile.successfulFrameTypes[type] !== undefined) {
        learningProfile.successfulFrameTypes[type] += 1;
      }
    }

    const transformPart = transform.includes("/")
      ? transform.split("/")[1]
      : transform;
    const transformHint = transformPart.split(":")[0] || "";
    if (transformHint) {
      const cur = Number(learningProfile.successfulTransforms[transformHint] || 0);
      learningProfile.successfulTransforms[transformHint] = cur + 1;

      let bestKey = "";
      let bestVal = -1;
      for (const [k, v] of Object.entries(learningProfile.successfulTransforms)) {
        const n = Number(v || 0);
        if (n > bestVal) {
          bestVal = n;
          bestKey = k;
        }
      }
      learningProfile.preferredTransformHint = bestKey;
    }
    saveLearningProfile();
  } catch {
    // ignore learning update errors
  }
}

async function maximizeCaptureResolution(track) {
  if (!track || !track.getCapabilities || !track.applyConstraints) {
    return;
  }
  try {
    const caps = track.getCapabilities();
    const widthMax = caps.width && typeof caps.width.max === "number" ? caps.width.max : 1920;
    const heightMax = caps.height && typeof caps.height.max === "number" ? caps.height.max : 1080;
    await track.applyConstraints({
      width: { ideal: Math.max(1920, widthMax) },
      height: { ideal: Math.max(1080, heightMax) },
      frameRate: { ideal: 30 },
    });
  } catch {
    // Keep current stream if device/browser rejects aggressive constraints.
  }
}

function readStreamResolution(track) {
  if (!track || !track.getSettings) return null;
  const s = track.getSettings();
  if (!s) return null;
  if (!s.width || !s.height) return null;
  return { width: s.width, height: s.height };
}

function makeMotionSignature(sourceCanvas, targetW = 160, targetH = 90) {
  const tmp = document.createElement("canvas");
  tmp.width = targetW;
  tmp.height = targetH;
  const tctx = tmp.getContext("2d", { willReadFrequently: true });
  tctx.drawImage(sourceCanvas, 0, 0, targetW, targetH);
  const rgba = tctx.getImageData(0, 0, targetW, targetH).data;
  const out = new Uint8Array(targetW * targetH);
  for (let i = 0, p = 0; i < rgba.length; i += 4, p += 1) {
    out[p] = Math.round((rgba[i] * 0.299) + (rgba[i + 1] * 0.587) + (rgba[i + 2] * 0.114));
  }
  return out;
}

function motionScore(prevSig, curSig) {
  if (!prevSig || !curSig || prevSig.length !== curSig.length) return 0;
  let sum = 0;
  for (let i = 0; i < curSig.length; i += 1) {
    sum += Math.abs(curSig[i] - prevSig[i]);
  }
  return sum / curSig.length;
}

async function loadQualityCalibration() {
  try {
    const res = await fetch(`${API_BASE}/api/quality-target`);
    if (!res.ok) return;
    const data = await res.json();
    if (!data.available || !data.target) return;
    qualityCalibration = {
      loaded: true,
      targetSharpness: Number(data.target.sharpness || 130),
      targetEdgeSignal: Number(data.target.edgeSignal || 14),
      targetGlareRatio: Number(data.target.glareRatio || 0.08),
      targetDarkRatio: Number(data.target.darkRatio || 0.2),
      targetMean: Number(data.target.mean || 130),
    };
  } catch {
    // Keep defaults when unavailable.
  }
}

function calcSharpness(grayData, width, height) {
  // Lightweight Laplacian variance approximation.
  let sum = 0;
  let sumSq = 0;
  let count = 0;
  for (let y = 1; y < height - 1; y += 2) {
    for (let x = 1; x < width - 1; x += 2) {
      const i = y * width + x;
      const c = grayData[i];
      const lap = Math.abs(
        4 * c -
        grayData[i - 1] -
        grayData[i + 1] -
        grayData[i - width] -
        grayData[i + width]
      );
      sum += lap;
      sumSq += lap * lap;
      count += 1;
    }
  }
  if (!count) return 0;
  const mean = sum / count;
  return (sumSq / count) - mean * mean;
}

function assessQuality(cropCanvas) {
  const ctx = cropCanvas.getContext("2d", { willReadFrequently: true });
  const { width, height } = cropCanvas;
  if (!width || !height) {
    return { ok: false, score: 0, reason: "empty-crop" };
  }

  const img = ctx.getImageData(0, 0, width, height).data;
  const gray = new Uint8Array(width * height);
  let mean = 0;
  let brightPixels = 0;
  let darkPixels = 0;

  for (let i = 0, p = 0; i < img.length; i += 4, p += 1) {
    const g = Math.round((img[i] * 0.299) + (img[i + 1] * 0.587) + (img[i + 2] * 0.114));
    gray[p] = g;
    mean += g;
    if (g > 240) brightPixels += 1;
    if (g < 20) darkPixels += 1;
  }

  const total = gray.length || 1;
  mean /= total;
  const glareRatio = brightPixels / total;
  const darkRatio = darkPixels / total;
  const sharpness = calcSharpness(gray, width, height);

  // Heuristic barcode texture signal: vertical edge activity in right-third.
  let edgeEnergy = 0;
  let edgeCount = 0;
  const xStart = Math.floor(width * 0.55);
  for (let y = 1; y < height - 1; y += 2) {
    for (let x = xStart; x < width - 1; x += 2) {
      const i = y * width + x;
      edgeEnergy += Math.abs(gray[i + 1] - gray[i - 1]);
      edgeCount += 1;
    }
  }
  const edgeSignal = edgeCount ? edgeEnergy / edgeCount : 0;

  const profile = runtimeQualityMode === "strict"
    ? {
        sharpMul: 0.92,
        sharpMin: 100,
        edgeMul: 0.82,
        edgeMin: 16,
        glareMul: 5.5,
        glareMax: 0.09,
        darkMul: 1.25,
        darkMax: 0.26,
        meanBand: 52,
      }
    : {
        sharpMul: 0.82,
        sharpMin: 85,
        edgeMul: 0.68,
        edgeMin: 12,
        glareMul: 7.0,
        glareMax: 0.12,
        darkMul: 1.5,
        darkMax: 0.30,
        meanBand: 70,
      };

  const sharpThreshold = Math.max(profile.sharpMin, qualityCalibration.targetSharpness * profile.sharpMul);
  const edgeThreshold = Math.max(profile.edgeMin, qualityCalibration.targetEdgeSignal * profile.edgeMul);
  const glareThreshold = Math.min(profile.glareMax, Math.max(0.025, qualityCalibration.targetGlareRatio * profile.glareMul));
  const darkThreshold = Math.min(profile.darkMax, Math.max(0.08, qualityCalibration.targetDarkRatio * profile.darkMul));
  const meanLow = Math.max(55, qualityCalibration.targetMean - profile.meanBand);
  const meanHigh = Math.min(215, qualityCalibration.targetMean + profile.meanBand);

  const exposureOk = mean >= meanLow && mean <= meanHigh;
  const glareOk = glareRatio <= glareThreshold;
  const blackoutOk = darkRatio <= darkThreshold;
  const sharpOk = sharpness >= sharpThreshold;
  const textureOk = edgeSignal >= edgeThreshold;

  const sharpRatio = Math.min(1.6, sharpness / Math.max(1, qualityCalibration.targetSharpness));
  const edgeRatio = Math.min(1.6, edgeSignal / Math.max(1, qualityCalibration.targetEdgeSignal));

  const score =
    (sharpRatio * 120) +
    (edgeRatio * 90) +
    (exposureOk ? 60 : 0) +
    (glareOk ? 35 : -60) +
    (blackoutOk ? 20 : -40);

  let reason = "ok";
  if (!sharpOk) reason = "blurry";
  else if (!glareOk) reason = "glare";
  else if (!exposureOk) reason = "bad-lighting";
  else if (!textureOk) reason = "barcode-too-small-or-off-zone";

  return {
    ok: sharpOk && glareOk && exposureOk && blackoutOk && textureOk,
    score,
    reason,
    sharpness,
    glareRatio,
    edgeSignal,
    mean,
    sharpThreshold,
    edgeThreshold,
  };
}

function getGuideRectInSourcePixels() {
  if (!video.videoWidth || !video.videoHeight) {
    return null;
  }

  const vRect = video.getBoundingClientRect();
  const gRect = cardGuide.getBoundingClientRect();
  const displayW = vRect.width;
  const displayH = vRect.height;
  if (!displayW || !displayH) {
    return null;
  }

  // object-fit: cover mapping from displayed video box back to source pixels
  const sourceW = video.videoWidth;
  const sourceH = video.videoHeight;
  const scale = Math.max(displayW / sourceW, displayH / sourceH);
  const renderedW = sourceW * scale;
  const renderedH = sourceH * scale;
  const offsetX = (renderedW - displayW) / 2;
  const offsetY = (renderedH - displayH) / 2;

  const gx = gRect.left - vRect.left;
  const gy = gRect.top - vRect.top;
  const gw = gRect.width;
  const gh = gRect.height;

  let sx = Math.floor((gx + offsetX) / scale);
  let sy = Math.floor((gy + offsetY) / scale);
  let sw = Math.floor(gw / scale);
  let sh = Math.floor(gh / scale);

  sx = clamp(sx, 0, sourceW - 1);
  sy = clamp(sy, 0, sourceH - 1);
  sw = clamp(sw, 1, sourceW - sx);
  sh = clamp(sh, 1, sourceH - sy);

  if (sw < 80 || sh < 50) {
    return null;
  }

  return { x: sx, y: sy, w: sw, h: sh };
}

async function applyPreferredCaptureConstraints(track) {
  if (!track || !track.getCapabilities) {
    return;
  }
  const caps = track.getCapabilities();
  const advanced = {};

  if (Array.isArray(caps.focusMode) && caps.focusMode.includes("continuous")) {
    advanced.focusMode = "continuous";
  }
  if (Array.isArray(caps.exposureMode) && caps.exposureMode.includes("continuous")) {
    advanced.exposureMode = "continuous";
  }

  if (Object.keys(advanced).length > 0) {
    try {
      await track.applyConstraints({ advanced: [advanced] });
    } catch {
      // Some mobile browsers expose capabilities but reject constraints; ignore.
    }
  }
}

function updateTorchAvailability(track) {
  torchBtn.classList.add("hidden");
  torchOn = false;
  torchBtn.textContent = "Torch Off";

  if (!track || !track.getCapabilities) {
    return;
  }
  const caps = track.getCapabilities();
  if (caps && caps.torch) {
    torchBtn.classList.remove("hidden");
  }
}

async function toggleTorch() {
  if (!currentTrack || !currentTrack.applyConstraints) {
    return;
  }

  torchOn = !torchOn;
  try {
    await currentTrack.applyConstraints({ advanced: [{ torch: torchOn }] });
    torchBtn.textContent = torchOn ? "Torch On" : "Torch Off";
  } catch (err) {
    torchOn = false;
    torchBtn.textContent = "Torch Off";
    setStatus(`Torch control failed: ${getErrorMessage(err)}`, true);
  }
}

async function startCamera() {
  if (running && stream && video.srcObject) {
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus("Live camera requires HTTPS or localhost on many phones. Use fallback capture below.", true);
    return;
  }

  try {
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }

    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: "environment" },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
      audio: false,
    });

    video.srcObject = stream;
    currentTrack = stream.getVideoTracks()[0] || null;
    await maximizeCaptureResolution(currentTrack);
    await applyPreferredCaptureConstraints(currentTrack);
    updateTorchAvailability(currentTrack);

    running = true;
    cameraReadyAtMs = Date.now();
    previousMotionSignature = null;
    setTips([
      "Fill the guide with the card edges.",
      "Keep barcode side in focus and avoid glare.",
      "Hold steady during burst capture.",
    ]);
    const res = readStreamResolution(currentTrack);
    if (res) {
      setStatus(`Camera ready (${res.width}x${res.height}). Starting scan...`);
    } else {
      setStatus("Camera ready. Starting scan...");
    }
  } catch (err) {
    setStatus(`Camera error: ${getErrorMessage(err)}`, true);
  }
}

function stopCamera() {
  running = false;
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    stream = null;
  }
  currentTrack = null;
  previousMotionSignature = null;
  torchOn = false;
  torchBtn.textContent = "Torch Off";
  torchBtn.classList.add("hidden");
  video.srcObject = null;
  setStatus("Camera stopped.");
}

async function rectToFile(sourceCanvas, rect, fileName) {
  if (!rect || rect.w <= 0 || rect.h <= 0) {
    return null;
  }
  const cropCanvas = document.createElement("canvas");
  cropCanvas.width = rect.w;
  cropCanvas.height = rect.h;
  const cropCtx = cropCanvas.getContext("2d", { willReadFrequently: true });
  cropCtx.drawImage(sourceCanvas, rect.x, rect.y, rect.w, rect.h, 0, 0, rect.w, rect.h);
  const blob = await new Promise((resolve) => cropCanvas.toBlob(resolve, "image/jpeg", 0.92));
  if (!blob) {
    return null;
  }
  return new File([blob], fileName, { type: "image/jpeg" });
}

async function canvasToFile(sourceCanvas, fileName) {
  const blob = await new Promise((resolve) => sourceCanvas.toBlob(resolve, "image/jpeg", 0.92));
  if (!blob) {
    return null;
  }
  return new File([blob], fileName, { type: "image/jpeg" });
}

async function rectToCanvas(sourceCanvas, rect) {
  if (!rect || rect.w <= 0 || rect.h <= 0) {
    return null;
  }
  const cropCanvas = document.createElement("canvas");
  cropCanvas.width = rect.w;
  cropCanvas.height = rect.h;
  const cropCtx = cropCanvas.getContext("2d", { willReadFrequently: true });
  cropCtx.drawImage(sourceCanvas, rect.x, rect.y, rect.w, rect.h, 0, 0, rect.w, rect.h);
  return cropCanvas;
}

async function captureBurstFrames() {
  const warmupMs = 1200;
  const sinceReady = Date.now() - cameraReadyAtMs;
  if (cameraReadyAtMs > 0 && sinceReady < warmupMs) {
    await sleep(warmupMs - sinceReady);
  }

  const frameCount = Math.max(3, Math.min(10, Number(burstCountInput.value || 6)));
  const gapMs = Math.max(60, Math.min(500, Number(burstGapInput.value || 110)));
  const maxUploads = 6;

  if (!running || !video.videoWidth || !video.videoHeight) {
    throw new Error("Camera is not ready.");
  }

  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });

  const candidates = [];

  for (let i = 0; i < frameCount; i += 1) {
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const currentSignature = makeMotionSignature(canvas);
    const mScore = motionScore(previousMotionSignature, currentSignature);
    previousMotionSignature = currentSignature;
    const motionThreshold = runtimeQualityMode === "strict" ? 16 : 20;
    const highMotion = mScore > motionThreshold;

    const guideRect = getGuideRectInSourcePixels();
    const fullRect = { x: 0, y: 0, w: canvas.width, h: canvas.height };

    const frameCandidates = [];
    if (guideRect) {
      frameCandidates.push({ rect: guideRect, name: `frame-${i + 1}-guide.jpg`, type: "guide" });

      const stripRect = {
        x: guideRect.x + Math.floor(guideRect.w * 0.58),
        y: guideRect.y + Math.floor(guideRect.h * 0.05),
        w: Math.floor(guideRect.w * 0.38),
        h: Math.floor(guideRect.h * 0.9),
      };
      if (stripRect.w > 50 && stripRect.h > 80) {
        frameCandidates.push({ rect: stripRect, name: `frame-${i + 1}-barcode-strip.jpg`, type: "strip" });
      }
    }
    if (i % 3 === 0) {
      frameCandidates.push({ rect: fullRect, name: `frame-${i + 1}-full.jpg`, type: "full" });
    }

    if (!highMotion) {
      for (const candidate of frameCandidates) {
        const cropCanvas = await rectToCanvas(canvas, candidate.rect);
        if (!cropCanvas) continue;
        const q = assessQuality(cropCanvas);
        candidates.push({
          ...candidate,
          quality: q,
          canvas: cropCanvas,
        });
      }
    }

    if (i < frameCount - 1) {
      // If quality is bad, wait a little longer for autofocus/exposure to settle.
      const thisFrameBest = candidates
        .filter((c) => c.name.startsWith(`frame-${i + 1}-`))
        .reduce((acc, cur) => (cur.quality.score > acc ? cur.quality.score : acc), -Infinity);
      const lowQualityExtra = thisFrameBest < 110 ? 120 : 0;
      const motionExtra = highMotion ? 180 : 0;
      const failureExtra = Math.min(220, consecutiveFailures * 30);
      const adaptiveGap = clamp(gapMs + lowQualityExtra + motionExtra + failureExtra, 80, 900);
      await sleep(adaptiveGap);
    }
  }

  candidates.sort((a, b) => {
    const as = a.quality.score + frameTypeWeight(a.type);
    const bs = b.quality.score + frameTypeWeight(b.type);
    return bs - as;
  });
  const accepted = candidates
    .filter((c) => {
      if (c.quality.ok) return true;
      if (consecutiveFailures >= 3 && c.quality.score >= 185 && c.quality.reason !== "glare") {
        return true;
      }
      if (consecutiveFailures >= 6 && c.quality.score >= 165) {
        return true;
      }
      return false;
    })
    .slice(0, maxUploads);

  // Always keep at least one full-frame candidate for context.
  const bestFull = candidates.find((c) => c.type === "full");
  if (bestFull && !accepted.some((c) => c.type === "full")) {
    if (accepted.length < maxUploads) {
      accepted.push(bestFull);
    } else if (accepted.length > 0) {
      accepted[accepted.length - 1] = bestFull;
    }
  }

  const minAccepted = runtimeQualityMode === "strict" ? 2 : 1;
  const finalSelection = accepted;

  const files = [];
  const selectedMeta = [];
  for (const candidate of finalSelection) {
    const file = await canvasToFile(candidate.canvas, candidate.name);
    if (file) {
      files.push(file);
      selectedMeta.push({
        name: candidate.name,
        type: candidate.type,
      });
    }
  }

  if (files.length < minAccepted) {
    const prompt = buildQualityPrompt(candidates.slice(0, 8));
    setStatus(`${prompt.headline} Auto-retrying...`, false);
    setTips(prompt.tips);
    return { files: [], selectedMeta: [] };
  }

  const best = accepted[0].quality;
  setStatus(`Captured quality frames (sharpness ${Math.round(best.sharpness)}).`);

  return { files, selectedMeta };
}

async function postBurst(files) {
  const formData = new FormData();
  files.forEach((f) => formData.append("images", f));
  formData.append("quality_mode", runtimeQualityMode);
  formData.append("decode_profile", runtimeQualityMode === "strict" ? "fast" : "extended");
  if (learningProfile.preferredTransformHint) {
    formData.append("preferred_transform_hint", learningProfile.preferredTransformHint);
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), BURST_REQUEST_TIMEOUT_MS);

  let res;
  try {
    res = await fetch(`${API_BASE}/api/decode-burst`, {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new Error("burst_timeout");
    }
    throw err;
  } finally {
    clearTimeout(timeout);
  }

  if (!res.ok) {
    throw new Error(`Burst request failed (${res.status})`);
  }
  return res.json();
}

async function postSingle(file) {
  const formData = new FormData();
  formData.append("image", file);

  const res = await fetch(`${API_BASE}/api/decode`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    throw new Error(`Upload request failed (${res.status})`);
  }
  return res.json();
}

async function scanLoop() {
  if (scanning) return;
  if (!running) {
    setStatus("Start camera first.", true);
    return;
  }

  scanning = true;
  clearSuccess();
  setTips([]);
  runtimeQualityMode = QUALITY_MODE;
  let cycleCount = 0;
  let relaxUntilCycle = -1;

  try {
    do {
      cycleCount += 1;
      if (cycleCount > MAX_TOTAL_SCAN_CYCLES) {
        setStatus("Scan timed out. Reposition ID and tap Scan id again.", true);
        break;
      }

      if (runtimeQualityMode === "strict" && consecutiveFailures >= STRICT_CYCLES_BEFORE_RELAX) {
        runtimeQualityMode = "balanced";
        relaxUntilCycle = cycleCount + RELAXED_MODE_DURATION_CYCLES;
      } else if (
        runtimeQualityMode === "balanced" &&
        relaxUntilCycle > 0 &&
        cycleCount > relaxUntilCycle
      ) {
        runtimeQualityMode = "strict";
        consecutiveFailures = Math.max(0, consecutiveFailures - 2);
      }

      setStatus(`Capturing burst (${runtimeQualityMode} mode)...`);
      const capture = await captureBurstFrames();
      const frames = capture.files;
      const selectedMeta = capture.selectedMeta;
      if (!frames.length) {
        consecutiveFailures += 1;
        await sleep(500);
        continue;
      }
      setStatus(`Decoding ${frames.length} quality images on server...`);

      const data = await postBurst(frames);
      if (data.status === "success") {
        consecutiveFailures = 0;
        saveLastSuccessProfile();
        updateLearningFromSuccess(data, selectedMeta);
        runtimeQualityMode = QUALITY_MODE;
        setStatus("Success: PDF417 decoded.");
        setTips([]);
        showSuccess(data);
        break;
      }
      consecutiveFailures += 1;

      const tips = data.tips || [
        "Increase lighting.",
        "Reduce glare.",
        "Move closer and keep barcode sharp.",
      ];
      setTips(tips);
      setStatus("Not decoded yet. Adjust camera conditions and retry.");

      if (!autoScanToggle.checked) {
        break;
      }

      await sleep(700);
    } while (running);
  } catch (err) {
    const msg = getErrorMessage(err);
    if (msg === "burst_timeout") {
      consecutiveFailures += 1;
      runtimeQualityMode = "strict";
      setStatus("Server took too long to respond. Retrying automatically...", false);
      setTips([
        "Hold steady and keep the ID centered.",
        "Use even lighting and reduce glare.",
        "Wait for autofocus to settle before movement.",
      ]);
      if (running && autoScanToggle.checked) {
        scanning = false;
        setTimeout(() => {
          void scanLoop();
        }, 600);
        return;
      }
    }
    setStatus(`Scan error: ${msg}`, true);
  } finally {
    scanning = false;
  }
}

stopCameraBtn.addEventListener("click", () => {
  stopCamera();
});

torchBtn.addEventListener("click", () => {
  void toggleTorch();
});

scanBurstBtn.addEventListener("click", async () => {
  await startCamera();
  await sleep(150);
  void scanLoop();
});

captureInput.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;

  clearSuccess();
  setStatus("Uploading photo...");

  try {
    const data = await postSingle(file);
    if (data.status === "success") {
      setStatus("Success: PDF417 decoded.");
      setTips([]);
      showSuccess(data);
      return;
    }

    setTips(data.tips || []);
    setStatus("Could not decode that image. Try again with better conditions.");
  } catch (err) {
    setStatus(`Upload error: ${getErrorMessage(err)}`, true);
  }
});

applyLastSuccessProfile();
loadLearningProfile();
void loadQualityCalibration();
