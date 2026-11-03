# Visual product search bonus

## Install

Copy the supplied files to the matching paths in the project:

- app/visual_search.py (new)
- app/main.py, app/llm_chat.py, app/ui.py (replace)
- requirements.txt (replace)
- tests/test_visual_search.py (new)
- docs/VISUAL_SEARCH.md (this guide)

From D:\superchat, install the image-decoding dependency and restart:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe run.py
```

Keep your existing .env and Gemini key. Use Gemini mode and a billing-disabled
Free Tier project. The application cannot verify your project's billing tier.
No additional provider, paid speech/image API or automatic model switch is added.
Image requests consume the configured Gemini model's quota. Access to image input
on that model/project must be verified live; errors are shown rather than falling
back to another model. Do not enable billing to resolve a quota error.

## Use

Refresh the browser (Ctrl+F5). Choose image, inspect its preview, select reply
language and click Search image. Selecting a file alone does not upload it.
Use a still JPEG, PNG or WebP image, at most 5 MB and 12 million pixels.

The server decodes and resizes the image to at most 1280 pixels on its longest
side and re-encodes it as JPEG, without EXIF metadata. It sends that image to
Gemini using inlineData. Original image bytes are not saved in the database or
server files; the browser preview is temporary. History retains an image-search
placeholder, result metadata and catalog rows, not the image itself. Image-search
requests cancel any pending chat write proposal, so a later Confirm cannot
accidentally confirm an old reservation/cart proposal.

Gemini classifies the main visible product into a supported type. The backend
maps that type to catalog category/keyword filters. Supported types: headphones,
earbuds, laptop, smartphone, smartwatch, speaker, shoes, backpack, coffee machine
and board game. The dataset has no reference product photos, so this is semantic
product-type matching, not image-to-image similarity or exact model verification.
Brand, price and stock are never inferred from the photo; displayed facts come
from official records. A laptop photo can therefore return both catalog laptops.

The UI reports the model's estimated confidence. It is not a calibrated probability.
A confidence below 70%, an ambiguous image or unknown type produces a clarification
instead of product cards. High-confidence classification still can be wrong; review
results. A photo outside supported types should produce unknown/clarification.
The image is treated as untrusted content and cannot directly authorize cart or
reservation writes. Subsequent typed follow-ups use the saved Shopping state.

## Local acceptance

Try in this order:

1. Clear photo of over-ear headphones: expect headphone-type catalog options,
   not a portable speaker. P001 is the over-ear headphone catalog record.
2. Laptop photo: expect P003/P004; ask which is cheaper or in stock afterwards.
3. Backpack photo: expect a backpack candidate from the official catalog.
4. A scene with several unrelated objects or a very unclear image: expect a
   clarification when the classifier marks it ambiguous or low confidence.
5. A landscape or pet photo: expect unsupported/clarification, no invented product.
6. Select a file, then Clear image: no provider request should occur.
7. Invalid file or oversized image: expect a controlled validation error.
8. Confirm cart and reservations remain unchanged after image search.
9. Verify typed chat and manual-Send voice drafts still work.

The first three expectations depend on live recognition, which has not been run
in the development environment. Do not treat mocked tests as recognition accuracy.

## Verification

163 offline Python tests passed with one existing deprecation warning. Twenty
visual-search cases cover the ten supported types, uncertain/unknown classifications,
invalid files, missing sessions, provider failures, rules-mode configuration,
image sanitization and the Gemini inlineData request. Provider results are mocked.
The 12 voice-controller checks still pass; JavaScript syntax was checked with Node.
Actual browser upload/preview, live Gemini visual recognition, latency and confidence
quality require local testing. Pillow is the only new runtime dependency.

References:
https://ai.google.dev/gemini-api/docs/generate-content/image-understanding
https://pillow.readthedocs.io/
