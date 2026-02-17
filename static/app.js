const video = document.getElementById("video");
const canvas = document.getElementById("canvas");
const cardGuide = document.getElementById("cardGuide");
const statusEl = document.getElementById("status");
const tipsEl = document.getElementById("tips");
const resultJson = document.getElementById("resultJson");
const successPanel = document.getElementById("successPanel");

const startCameraBtn = document.getElementById("startCameraBtn");
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

const API_BASE = window.location.origin;

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

function showSuccess(data) {
  successPanel.classList.remove("hidden");
  resultJson.textContent = JSON.stringify(data.fields || {}, null, 2);
}

function clearSuccess() {
  successPanel.classList.add("hidden");
  resultJson.textContent = "";
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
    await applyPreferredCaptureConstraints(currentTrack);
    updateTorchAvailability(currentTrack);

    running = true;
    setTips([
      "Fill the guide with the card edges.",
      "Keep barcode side in focus and avoid glare.",
      "Hold steady during burst capture.",
    ]);
    setStatus("Camera ready. Align card in guide, then tap Scan Burst.");
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

async function captureBurstFrames() {
  const frameCount = Math.max(3, Math.min(10, Number(burstCountInput.value || 6)));
  const gapMs = Math.max(60, Math.min(500, Number(burstGapInput.value || 110)));
  const maxUploads = 12;

  if (!running || !video.videoWidth || !video.videoHeight) {
    throw new Error("Camera is not ready.");
  }

  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });

  const files = [];
  const perFrameLimit = Math.max(1, Math.floor(maxUploads / frameCount));

  for (let i = 0; i < frameCount; i += 1) {
    if (files.length >= maxUploads) {
      break;
    }

    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    const guideRect = getGuideRectInSourcePixels();
    const fullRect = { x: 0, y: 0, w: canvas.width, h: canvas.height };

    const candidates = [];
    const preferFullFirst = perFrameLimit === 1 && i % 2 === 1;
    if (preferFullFirst) {
      candidates.push({ rect: fullRect, name: `frame-${i + 1}-full.jpg` });
    }
    if (guideRect) {
      candidates.push({ rect: guideRect, name: `frame-${i + 1}-guide.jpg` });

      const stripRect = {
        x: guideRect.x + Math.floor(guideRect.w * 0.58),
        y: guideRect.y + Math.floor(guideRect.h * 0.05),
        w: Math.floor(guideRect.w * 0.38),
        h: Math.floor(guideRect.h * 0.9),
      };
      if (stripRect.w > 50 && stripRect.h > 80) {
        candidates.push({ rect: stripRect, name: `frame-${i + 1}-barcode-strip.jpg` });
      }
    }
    if (!preferFullFirst) {
      candidates.push({ rect: fullRect, name: `frame-${i + 1}-full.jpg` });
    }

    let addedForFrame = 0;
    for (const candidate of candidates) {
      if (files.length >= maxUploads || addedForFrame >= perFrameLimit) {
        break;
      }
      const file = await rectToFile(canvas, candidate.rect, candidate.name);
      if (file) {
        files.push(file);
        addedForFrame += 1;
      }
    }

    if (i < frameCount - 1) {
      await sleep(gapMs);
    }
  }

  if (files.length === 0) {
    throw new Error("No frames were captured.");
  }

  return files;
}

async function postBurst(files) {
  const formData = new FormData();
  files.forEach((f) => formData.append("images", f));

  const res = await fetch(`${API_BASE}/api/decode-burst`, {
    method: "POST",
    body: formData,
  });

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

  try {
    do {
      setStatus("Capturing burst...");
      const frames = await captureBurstFrames();
      setStatus(`Decoding ${frames.length} images on server...`);

      const data = await postBurst(frames);
      if (data.status === "success") {
        setStatus("Success: PDF417 decoded.");
        setTips([]);
        showSuccess(data);
        break;
      }

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
    setStatus(`Scan error: ${getErrorMessage(err)}`, true);
  } finally {
    scanning = false;
  }
}

startCameraBtn.addEventListener("click", () => {
  void startCamera();
});

stopCameraBtn.addEventListener("click", () => {
  stopCamera();
});

torchBtn.addEventListener("click", () => {
  void toggleTorch();
});

scanBurstBtn.addEventListener("click", () => {
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
