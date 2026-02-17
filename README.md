# PDF417 Phone Scanner (Standalone)

A standalone Python app for scanning **US driver license PDF417** barcodes from a phone camera stream.

- Backend decode only (no browser decoder)
- Reuses the proven decoder pipeline from your existing `id-barcode-lab`
- Live preview + burst capture + retry prompts until decode
- Shows a success panel with parsed AAMVA fields

## Stack

- FastAPI (API + static UI)
- OpenCV + `zxing-cpp` (primary decoder)
- `pyzbar` (fallback decoder)
- AAMVA parser for key DL fields

## Run

```powershell
cd C:\Users\moham\pdf417-phone-scanner
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8012
```

Open on desktop:

- `http://localhost:8012`

Open on phone (same Wi-Fi):

- `http://<YOUR_PC_LAN_IP>:8012`

## One-Click HTTPS (recommended for live phone camera)

Run this from project root:

```powershell
.\run_https.ps1
```

What it does:

1. Installs `mkcert` (via `winget`) if missing.
2. Creates/trusts a local dev CA on your PC.
3. Generates a LAN cert in `certs\`.
4. Starts uvicorn on `https://<LAN_IP>:8012`.

### Trust the cert on your phone

The script copies the CA file to:

- `certs\rootCA.pem`

Install/trust this CA on your phone, then open the HTTPS URL printed by the script.

- iPhone/iPad: install profile, then enable full trust in Settings > General > About > Certificate Trust Settings.
- Android: install the CA cert in security settings (wording varies by device/Android version).

## Camera note (important)

Most mobile browsers require a **secure context** for live `getUserMedia` camera access (HTTPS or localhost).

- If live camera works, use `Start Camera` + `Scan Burst`.
- If blocked on LAN HTTP, use the fallback capture input on the page (opens phone camera and uploads image).

Reference: MDN `getUserMedia` secure-context requirement.

## API

- `GET /api/health`
- `POST /api/decode` (single image field: `image`)
- `POST /api/decode-burst` (multi-image field: `images`)

## Best-known methods used here

1. Decode with a native library (`zxing-cpp`) on the backend, not in browser JS.
2. Restrict to PDF417 and run multi-pass transforms (rotate, crop, contrast, threshold).
3. Use burst capture from live preview to beat motion blur/focus variance.
4. Keep barcode large in frame and high pixel density (critical for dense PDF417).

## Decoder landscape (practical)

- Open source best general choice in Python backend: `zxing-cpp` (supports PDF417).
- Fallback: `pyzbar`/ZBar wrapper (quality varies by build and symbology support).
- Commercial top-tier options for hard real-world captures: Dynamsoft / Scandit / Scanbot / Anyline (paid SDKs).

## Sources

- zxing-cpp repository: https://github.com/zxing-cpp/zxing-cpp
- zxing-cpp Python package: https://pypi.org/project/zxing-cpp/
- pyzbar repository: https://github.com/NaturalHistoryMuseum/pyzbar
- ML Kit barcode docs (PDF417 support + capture guidance): https://developers.google.com/ml-kit/vision/barcode-scanning
- ML Kit Android image guidance (dense PDF417 pixel needs): https://developers.google.com/ml-kit/vision/barcode-scanning/android
- MDN `getUserMedia` secure context: https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
- REAL ID machine-readable requirement (PDF417 / ISO 15438): https://www.law.cornell.edu/cfr/text/6/37.19
