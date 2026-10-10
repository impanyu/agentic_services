# Photo Scout Avatar Library

The right map toolbar contains Avatar Library, with My uploads and Preset characters tabs. Take a selfie includes Choose from Avatar Library, which explicitly fills only the current POI's upload preview. Selecting an avatar does not submit an image-generation job or carry it into another POI.

Personal images are stored in the Photo Scout SQLite database, scoped to the existing account or anonymous cookie identity. Login adopts an active guest library. Account uploads remain private and permanent; guest uploads expire after seven days without a visit. Each library admits up to 50 visible images. Removed images are hidden from lists and inaccessible through the image endpoint.

Images are normalized on the server to JPEG, at most 1600 pixels on the longest side, with metadata stripped. The browser uses the existing resize helper before upload. HEIC needs browser decoding; otherwise the library asks for JPG/PNG export. Original presets are 32 repository-owned SVG characters, rasterized locally to PNG before use in the existing selfie workflow.

Endpoints behind the existing service gateway:

- GET /photo-scout/v1/avatars
- POST /photo-scout/v1/avatars — name and image data URL
- GET /photo-scout/v1/avatars/{id}/image
- DELETE /photo-scout/v1/avatars/{id}

All require the existing service API authorization. Personal access is additionally owner-scoped. Writes validate Origin and account CSRF tokens; image responses are private and not cached.
