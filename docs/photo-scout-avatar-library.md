# Photo Scout Avatar Library

The right map toolbar contains Avatar Library, with Characters (the default tab) and My uploads tabs. Every Take a selfie entry point (map popup and Shortlist) opens the selfie studio with the selected background. The user can upload a photo, take a photo, or explicitly open the shared library picker using Choose from Avatar Library. Choosing a character or a private upload fills only that POI's upload preview and returns to its selfie studio. Closing the picker leaves the studio available for direct upload or camera capture. The studio also includes Choose from Avatar Library to change the selection. Selecting an avatar does not submit an image-generation job or carry it into another POI.

Personal images are stored in the Photo Scout SQLite database, scoped to the existing account or anonymous cookie identity. Login adopts an active guest library. Account uploads remain private and permanent; guest uploads expire after seven days without a visit. Each library admits up to 50 visible images. Removed images are hidden from lists and inaccessible through the image endpoint.

Images are normalized on the server to JPEG, at most 1600 pixels on the longest side, with metadata stripped. The browser uses the existing resize helper before upload. HEIC needs browser decoding; otherwise the library asks for JPG/PNG export. The visible presets are 532 selectable interpretations of 521 distinct recognizable cartoon, literary, mythological and historical characters stored as PNG cutouts in `photo-scout-site/avatars/characters/`. 531 use built-in imagegen artwork; SpongeBob reuses the existing validated input asset. `presets.json` defines the visible list. Both the toolbar menu and the selfie picker open on Characters by default. Generated interpretations are not represented as official character assets.

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

The catalog contains 233 distinct characters across 16 categories: 22 Cartoons, 21 Chinese classics, 19 Storybook, 10 Mythology, 6 Fairy tales, 2 Historical, 24 Screen icons, 2 Public figures, 29 Science, 32 Art & literature, 12 Music, 13 Sports, 14 History & explorers, 1 Animation & games, 20 Anime, 6 Storybook & fantasy. Both library surfaces support category filtering and case-insensitive English names plus Chinese aliases. Filtering preserves input focus. Images load lazily; mobile cards use two columns. A private-upload API failure does not prevent the character catalog from loading.

Assets are PNG cutouts saved in `photo-scout-site/avatars/characters/`. Each listed asset passed PNG decoding, alpha-channel verification and SHA-256 uniqueness checks. The manifest lists only successfully produced assets; rejected attempts are omitted.

Built-in imagegen prompt sets (including attempted roles that are not in the visible manifest):

- [Initial expansion](photo-scout-character-prompts.json)
- [Literary and mythical additions](photo-scout-character-extra-prompts.json)
- [Myths and fairy tales](photo-scout-character-myth-prompts.json)
- [Literary and historical additions](photo-scout-character-literary-prompts.json)

## Photorealistic people

Seven AI-generated full-body photorealistic likenesses were added: Keanu Reeves, Tom Cruise, Audrey Hepburn, Bruce Lee, Charlie Chaplin, Albert Einstein and Nikola Tesla. Screen icons and Public figures are separate categories. Chinese aliases use the same search/filter controls. The library labels these as AI-generated avatars; they are not original celebrity photographs. Selecting them follows the same explicit Choose from Avatar Library flow.

[Built-in imagegen prompt set](photo-scout-realistic-avatar-prompts.json). Saved assets remain in `photo-scout-site/avatars/characters/`, with the seven names as kebab-case PNG filenames. The Marilyn Monroe attempt was omitted after output moderation returned `moderation_blocked`, category `sexual`, request `c5a53a40-9113-469b-98e9-ebd22fccd3d1`; no specific cause beyond that provider category was returned, and no retry was made.

Clicking the right-side person preview opens a compact photo-source menu with Upload photo, Take a photo and Choose from Avatar Library. It uses the same existing upload/camera/library handlers. The menu closes after selection, outside clicks, Escape or studio closure. Take a selfie still opens the studio first.

## October 10 broad expansion and readable UI

Added 162 successfully generated independent full-body cutouts, covering science, art and literature, music, screen icons, sports, history and explorers, cartoons, animation and games, anime, and storybook fantasy. The original 71 images remain unchanged. Each new image was generated with its own built-in imagegen request; attempted images rejected by the provider were omitted and not retried. The exact prompt template, category, Chinese search aliases, outcome and saved path for each attempt are recorded in [the expansion prompt set](photo-scout-avatar-expansion-prompts.json).

Every catalog item uses a lightweight 240 × 300 maximum WebP thumbnail in `photo-scout-site/avatars/thumbnails/`, with lazy image loading. Selection fetches the original transparent PNG for the current selfie scene, never the thumbnail. Both the toolbar and studio picker retain the Characters default tab, English/Chinese search and category filtering.

`photo-scout-site/typography.css` provides a shared readable font scale across map controls, menus, search settings, Shortlist, selfie forms, photo details and community controls. Form text is at least 16 px, ordinary body and labels about 14–16 px, and the main prompt is 18 px on mobile and 19 px on desktop. The shared stylesheet also applies to the privacy and terms pages. Mobile controls retain their compact layout, and imagery provider controls and map symbols are unchanged.

## Diverse visual styles

Added 11 independently generated style editions, preserving all 233 previous assets: documentary photography (Einstein), black-and-white silent film (Chaplin), fashion editorial photography (Hepburn), cel animation (Doraemon), cinematic 3D (Monkey King), clay stop-motion (Sherlock Holmes), plush and felt (Totoro), watercolor (Little Prince), Chinese ink (Monkey King), classical oil painting (Athena), and pixel art (Astro Boy). The catalog now has 244 entries. Styles are explicit per-image directions rather than a shared studio-light template. Sherlock Holmes received a second built-in edit to remove the backdrop glow.

Both library surfaces offer a separate visual-style filter, which combines with character category and English/Chinese name search. Original assets remain under Original versions because their exact visual medium has not been individually classified. New editions appear first and display their medium on the card. Thumbnails remain lightweight; choosing an edition loads its original transparent PNG. [Exact prompts and output paths](photo-scout-avatar-style-prompts.json).

## October 11 expansion to 500+ characters

The published catalog now contains 532 avatars representing 521 distinct named people and fictional characters. Existing assets and style variants are retained. Built-in imagegen produced 288 additional usable transparent PNGs: 222 world-history figures and 66 fictional characters. Artwork spans documentary realism, oil painting, watercolor, clay, layered paper, graphic novels, 3D animation, felt/plush and cel animation. Generated likenesses are imagined interpretations, not authenticated historical portraits.

All catalog paths and thumbnails were checked; generated assets passed PNG decoding, RGBA transparency and SHA-256 uniqueness checks. The installer writes the manifest atomically and requires at least 500 distinct characters. Failed generation attempts are absent from the selectable list. Both library surfaces retain the Characters default tab, category/style filters, lazy local thumbnails and full-size selection. Common Chinese aliases are included. Personal uploads and their existing limit/retention behavior are unchanged.

Assets: `photo-scout-site/avatars/characters/` and `photo-scout-site/avatars/thumbnails/`. Built-in prompt sets: [main expansion](photo-scout-avatar-500-prompts.json), [science additions](photo-scout-avatar-500-extra-prompts.json), [further historical additions](photo-scout-avatar-500-supplement-prompts.json). Each asset used its own built-in tool request; original outputs remain in the generated-images folder and production copies are saved in this repository. Generation reports with successful source paths and failed names are the corresponding `*-results.json` files.

Validation: four offline installer tests and three frontend catalog/selection/filter tests. Static deployment requires images before the atomic manifest replacement, followed by the versioned catalog/loader references. No search or image-generation backend restart is required.

## October 11 Chinese history and anime additions

Added 40 independently generated transparent full-body avatars: 20 Chinese historical figures and 20 anime/virtual characters. The catalog now contains 572 entries representing 561 distinct characters. Existing 532 entries remain unchanged. New Chinese history entries include Tang Taizong, Wu Zetian, Zhuge Liang, Liu Bei, Cao Cao, Zhao Yun, Zhou Yu, Yue Fei, Wen Tianxiang, Zhang Qian, Qi Jiguang, Zu Chongzhi, Ban Gu, Song Yingxing, Liu Zongyuan, Wang Anshi, Han Yu, Qu Yuan, Sima Guang and Cai Wenji. Anime additions include Frieren, Fern, Stark, Tanjiro, Nezuko, Zenitsu, Inosuke, Gojo, Yuji, Megumi, Nobara, Violet Evergarden, Sakura Kinomoto, Inuyasha, Sesshomaru, Kagome, Edward and Alphonse Elric, Saitama and Hatsune Miku.

Chinese history has its own category; Anime uses the existing category. English names and Chinese aliases are searchable in both library surfaces. Styles include Chinese ink, oil painting, photographic interpretations, layered paper, clay, cel animation, watercolor, cinematic 3D, manga and plush. These are AI-generated artistic interpretations, not authenticated historical portraits or official character artwork. Existing upload behavior, search and selfie workflow are unchanged.

Built-in imagegen made one request per new character; a separate edit cleaned the transparent background of Zhuge Liang. All 40 character requests returned images. Only validated RGBA PNGs with usable transparency and unique artwork were installed. Full-size copies and lightweight WebP thumbnails live under `photo-scout-site/avatars/`. Exact prompts are in [the prompt set](photo-scout-avatar-east-expansion-prompts.json), and source paths/outcomes are in [the generation report](photo-scout-avatar-east-expansion-results.json). Original built-in outputs remain in the generated-images directory.

Validation: four installer tests and nine frontend catalog/autosave tests passed. Production preserved all original 532 manifest entries, verified all 80 new asset hashes before replacing the manifest, and public checks confirmed the 572-entry catalog, all 40 new characters, 80 HTTP 200 image URLs and updated loader versions.
