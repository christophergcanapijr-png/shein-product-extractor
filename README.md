# SHEIN Product Extractor

## Quick start

Double-click `START APP.bat`. It starts the local server and opens the app in
your default browser. If the server is already running, it only opens the app.

A local FastAPI dashboard that searches the French SHEIN site with a background,
persistent Chromium browser, extracts up to five original product images and verified
Size S measurements, and generates editable English and French resale listings
with Gemini.

## What it does

- Searches by SKU and confirms an exact match instead of opening the first result.
- Returns possible matches for manual selection when an exact match cannot be proven.
- Prefers original high-resolution image URLs and falls back to an element capture.
- Reads flexible French size-guide columns without inventing missing measurements.
- Prefers “Mesures du produit,” converts inches to centimetres, and preserves originals.
- Stores extraction history in SQLite and refreshes existing products.
- Proxies only approved SHEIN image hosts for clipboard and download support.
- Generates validated bilingual JSON listings without exposing the Gemini key.

## Windows setup

Open PowerShell or Command Prompt in this folder and run:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
python run.py
```

Open:

```text
http://127.0.0.1:8000
```

Add your Gemini API key to `.env` before using **Generate Description**:

```env
GEMINI_API_KEY=your_key_here
```

The app defaults to the stable `gemini-3.6-flash` model. Change
`GEMINI_MODEL` in `.env` if your account uses a different compatible Gemini
model.

## Vertex AI image generation

The **Generate image** button uses Vertex AI instead of a Gemini API key, so it
can spend Google Cloud free-trial credits attached to your Cloud project.

1. Install the Google Cloud CLI.
2. Run `gcloud auth application-default login`.
3. Run `gcloud services enable aiplatform.googleapis.com --project YOUR_PROJECT_ID`.
4. Put your project in `.env`:

```env
VERTEX_AI_PROJECT=YOUR_PROJECT_ID
VERTEX_AI_LOCATION=us-central1
VERTEX_AI_IMAGE_MODEL=imagen-3.0-fast-generate-001
VERTEX_AI_RECONTEXT_MODEL=gemini-2.5-flash-image
VERTEX_AI_TRY_ON_MODEL=virtual-try-on-001
```

Restart the app after changing `.env`.

To place an extracted dress onto a mannequin or room scene, open **Generate
image**, choose **Add mannequin / scene reference**, upload the mannequin photo,
and prompt it like: `place the dress on the mannequin`. Mannequin/display-form
prompts use Gemini image editing with person generation disabled, so the model
does not intentionally turn the mannequin into a human. Human model clothing
replacement prompts can still use Vertex's virtual try-on flow.

Use the **Prompt preset** dropdown for common dress shots: mannequin front,
side view, back view, model clothing replacement, floor front, and floor back.
For prompts that mention a “3rd image,” upload that image under **Add optional
guide reference**; it is sent only as a length, width, orientation, or placement
guide, not as the background.

The image panel defaults to a Gemini-style conversation mode: write a natural
message, generate an image, then send follow-up edits such as `remove the hair`
or `make the hem shorter`. Follow-ups include the last generated image as the
current canvas, similar to editing in Gemini.com.

For best mannequin results, upload a strong Gemini.com output under **Add
example result reference**. The app sends it as an ideal quality reference so
Vertex can match the clean mannequin presentation, fabric drape, lighting, and
absence of hands, hair, feet, bags, or other human artifacts.

## Normal workflow

1. Paste the SKU and choose **Extract Product**.
2. Chromium runs in the background while the search and Size S hover complete.
3. If SHEIN requests verification, temporarily set
   `PLAYWRIGHT_BACKGROUND=false`, restart the app, complete verification in
   Chromium, and choose **Retry**.
4. Review images, facts, and verified measurements.
5. Choose **Generate Description**, edit either listing, and copy the fields you need.

## Dotb draft workflow

1. Reload the unpacked extension from `brave-extension/` after installing an update.
2. Prepare the French descriptions and calculated prices for every product in a batch.
3. Select the batch and choose **Open batch in Dotb**.
4. Choose the Vinted account from the Dotb popup and set the item quantity in the Product Extractor panel.
5. Choose **Fill current item**, then select an extracted product from the popup showing its preview image, title, SKU, price, and description. The floating panel keeps a small preview of the selected SKU while you manually upload its photos in Dotb.
6. Choose the product's category in the Dotb popup. Long dresses use size S, condition is Very Good, package size is Small, Auto-Restock is enabled, brand follows the generated listing (with an unbranded fallback), and up to two colours are selected from the generated title.
   If Dotb changes or delays one of its menus, use **Retry details** in the floating panel.
7. Review every photo, title, description, price, SKU, and detail, then choose **Save draft & next**. After Dotb confirms the save, the matching local Product Extractor product is deleted with its saved screenshots and removed from the extension's active picker. The last control is **Save final draft**.
8. These controls only save pending Dotb drafts; they never import or publish anything to Vinted.

Browser cookies and session data are kept in `browser_profile/`. Extracted
records remain in `data/products.db`. Element-capture image fallbacks are saved
in `downloads/`.

## Tests

```bash
pytest
```

The test suite covers SKU and candidate URL validation, both common size-table
orientations, label normalization, inch conversion, Size S enforcement, Gemini
response validation, SQLite upserts, and image-proxy host restrictions.

## Debugging

Set this in `.env` to retain HTML and full-page screenshots for every extraction:

```env
DEBUG=true
```

Failures always save diagnostic HTML and a screenshot under `debug/`. Logs show
the major extraction stages without logging API keys.

## Selector maintenance

SHEIN changes its JavaScript UI periodically. The extractor intentionally uses
semantic roles, visible French text, URL patterns, metadata, attributes, and
several fallback selectors. If a future layout change breaks extraction, the
most likely adjustment points are the selector tuples near the top of
`backend/services/shein_extractor.py` and the size-guide trigger strategy in
`_open_size_guide`.

The saved-fixture parser is independent of Playwright:
`parse_size_s_measurements(html)` can be tested against captured size-guide HTML
without opening SHEIN.

## Local API

- `POST /api/products/extract`
- `POST /api/products/{product_id}/generate-description`
- `POST /api/products/{product_id}/refresh`
- `GET /api/products`
- `GET /api/products/{product_id}`
- `DELETE /api/products/{product_id}`
- `GET /api/images/proxy`
- `GET /health`
- Interactive API documentation: `http://127.0.0.1:8000/docs`

Use this tool in line with SHEIN’s terms and applicable laws. It does not bypass
CAPTCHAs, access controls, or login requirements.
