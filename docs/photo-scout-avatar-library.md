# Photo Scout Avatar Library

The right map toolbar contains Avatar Library, with Characters (the default tab) and My uploads tabs. Every Take a selfie entry point (map popup and Shortlist) opens the shared library picker on Characters after preparing the selected background. Choosing a character or a private upload fills only that POI's upload preview and returns to its selfie studio. Closing the picker leaves the studio available for direct upload or camera capture. The studio also includes Choose from Avatar Library to change the selection. Selecting an avatar does not submit an image-generation job or carry it into another POI.

Personal images are stored in the Photo Scout SQLite database, scoped to the existing account or anonymous cookie identity. Login adopts an active guest library. Account uploads remain private and permanent; guest uploads expire after seven days without a visit. Each library admits up to 50 visible images. Removed images are hidden from lists and inaccessible through the image endpoint.

Images are normalized on the server to JPEG, at most 1600 pixels on the longest side, with metadata stripped. The browser uses the existing resize helper before upload. HEIC needs browser decoding; otherwise the library asks for JPG/PNG export. The visible presets are 64 distinct recognizable cartoon, literary, mythological and historical characters stored as PNG cutouts in `photo-scout-site/avatars/characters/`. 63 use built-in imagegen artwork; SpongeBob reuses the existing validated input asset. `presets.json` defines the visible list. Both the toolbar menu and the selfie picker open on Characters by default. Generated interpretations are not represented as official character assets.

Endpoints behind the existing service gateway:

- GET /photo-scout/v1/avatars
- POST /photo-scout/v1/avatars — name and image data URL
- GET /photo-scout/v1/avatars/{id}/image
- DELETE /photo-scout/v1/avatars/{id}

All require the existing service API authorization. Personal access is additionally owner-scoped. Writes validate Origin and account CSRF tokens; image responses are private and not cached.

## Character artwork prompts

Built-in imagegen was used with a transparent background. Each asset used its own request. Common direction: polished recognizable full-body character cutout for Photo Scout travel-photo compositing; faithful familiar design, detailed shading, clean outlines, entire body/accessories visible with transparent margins, single character, no landscape, scene, frame or watermark.

- Doraemon: blue robot cat, white face/belly, red round nose, whiskers, red collar/yellow bell and belly pocket; cheerful smile and wave.
- Totoro: gray forest spirit, white belly with gray chevrons, pointed ears/whiskers and small rounded arms; gentle standing pose.
- Monkey King: Sun Wukong, expressive monkey face, gold head circlet, red/gold armor, cloud-pattern cloth and golden staff; lively heroic standing pose.

- Sherlock Holmes: classic Victorian detective, deerstalker, tweed Inverness cape, waistcoat and magnifying glass; friendly standing pose; no likeness of a particular actor.

SpongeBob was copied unchanged from the previously used transparent character input. Original generation outputs remain in the local imagegen output folder; deployable copies live in the repository.

Two other attempted presets were omitted following provider output moderation: Pikachu (`5d673619-df96-4d7d-9224-58fb3b476962`) and Mario (`4a77436c-2f9e-9dcc-9d6a-6d6f56d130fd`). Both reported stage `output`, category `other`; this does not identify the specific cause. No unavailable asset is listed and no further retry of these rejected requests was made.

## Expanded character catalog

The catalog contains 12 Cartoons, 15 Chinese classics, 19 Storybook characters, 10 Mythology characters, 6 Fairy tales and 2 Historical characters. Both library surfaces support category filtering and case-insensitive English names plus Chinese aliases. Filtering preserves input focus. Images load lazily; mobile cards use two columns. A private-upload API failure does not prevent the character catalog from loading.

Assets are PNG cutouts saved in `photo-scout-site/avatars/characters/`. Each listed asset passed PNG decoding, alpha-channel verification and SHA-256 uniqueness checks. The manifest lists only successfully produced assets; rejected attempts are omitted.

Built-in imagegen prompt sets (including attempted roles that are not in the visible manifest):

- [Initial expansion](photo-scout-character-prompts.json)
- [Literary and mythical additions](photo-scout-character-extra-prompts.json)
- [Myths and fairy tales](photo-scout-character-myth-prompts.json)
- [Literary and historical additions](photo-scout-character-literary-prompts.json)
