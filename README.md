# PDF417 Phone Scanner

`PDF417 Phone Scanner` is a standalone phone-camera scanning tool for extracting structured customer data from US driver's license PDF417 barcodes.

The project was built to solve a practical intake problem: entering customer information into a system by reading an ID and typing each field by eye is slow, repetitive, and easy to get wrong. This app captures barcode data from a phone camera, decodes it on the backend, parses key AAMVA fields, and returns structured values that can be used to create customer profiles much faster.

It was designed as a faster intake workflow for CRM use, where the decoded output could be used to create or populate customer profiles with far less manual typing.

## Quick Try

The fastest local test path is:

```powershell
cd C:\Users\LocalAdmin1\PDF417-Scanner
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8012
```

Then open:

- `http://localhost:8012` on desktop
- `http://<YOUR_PC_LAN_IP>:8012` from a phone on the same Wi-Fi

If you want live phone camera support in a more reliable browser context, run:

```powershell
.\run_https.ps1
```

## Business Purpose

This tool is aimed at speeding up customer onboarding and record creation by:

- reducing manual data entry from physical IDs
- lowering mistakes caused by typing information field by field
- speeding up customer profile creation in CRM workflows
- improving consistency in repetitive intake tasks

Instead of keying in names, address details, birth date, and license information by hand, the scanner extracts that data directly from the barcode payload.

## Privacy and Safe Handling

This project is intended for consent-based intake workflows where the customer is providing their ID for a legitimate business purpose.

- scan only with customer knowledge and consent
- process images for transient decode rather than long-term image storage
- keep retained customer information limited to the downstream system that actually needs it
- delete temporary exports, copied notes, or local files when they are no longer needed
- avoid storing raw barcode payloads or ID images outside the controlled business workflow

The browser UI stores only non-sensitive local capture preferences and learning hints. It does not intentionally persist decoded customer fields in local storage, and the UI includes a `Clear Local Settings` control to remove those local preferences.

## What It Does

- captures images from a phone camera workflow
- uploads burst or single-frame captures to a local backend
- decodes PDF417 barcodes using backend-native libraries
- parses AAMVA fields into structured output
- shows a result screen with the decoded customer information
- provides profile-ready output that can be reused in intake and CRM workflows

## Stack

- FastAPI for API routes and local web app hosting
- OpenCV for image processing
- `zxing-cpp` as the primary PDF417 decoder
- `pyzbar` as a fallback decoder
- AAMVA parser for normalized license fields

## Project Layout

- `app/` backend application logic, decode pipeline, parser, and models
- `static/` local web UI
- `tests/` parser and workflow validation
- `tools/` helper scripts
- `run_https.ps1` local HTTPS startup for live phone-camera use
- `run_ngrok.ps1` optional tunnel workflow

## Workflow

1. Open the app from a phone on the same network
2. Capture an ID barcode using the live camera flow
3. Submit one or more frames for decode
4. Decode PDF417 on the backend
5. Parse key AAMVA fields into structured output
6. Use the resulting values to create or populate a customer profile in a CRM or related intake system

## Run

```powershell
cd C:\Users\LocalAdmin1\PDF417-Scanner
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8012
```

## One-Click HTTPS

For live phone camera access, use:

```powershell
.\run_https.ps1
```

This script:

1. installs `mkcert` if missing
2. creates and trusts a local development CA
3. generates a LAN certificate in `certs\`
4. starts the app over HTTPS

Most mobile browsers require HTTPS or another secure context for live camera access.

## API

- `GET /api/health`
- `POST /api/decode`
- `POST /api/decode-burst`

The decode responses are structured so the output can be reused in downstream workflows rather than treated as raw barcode text only.

## Key Technical Choices

- backend-first decoding instead of browser-side barcode parsing
- burst capture support to improve odds of getting a usable frame
- quality ranking and gating before decode attempts
- deterministic transform passes for more stable behavior
- AAMVA-aware parsing so the output is immediately more useful to intake systems

## Privacy

- uploaded images are processed for transient decode
- the app is intended for local review, not long-term storage of ID images
- raw customer data should only be retained in the downstream system that actually needs it, such as a CRM or intake platform
- local capture settings can be cleared from the UI when the session is complete

## Notes

This project is best understood as a workflow tool for faster customer data capture, not just a barcode-decoding demo.
