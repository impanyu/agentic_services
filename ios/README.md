# Photo Scout — iPhone / iPad

This is an iOS 16+ UIKit/WKWebView app. It loads the canonical Photo Scout website,
so the map, controls, search, Street View, history, avatar library, photo studio,
comments, and publications use the same interface and backend as the Web App.
It requires an internet connection. This is a hybrid app, not a separate SwiftUI
reimplementation or an offline copy of the website.

## Open and run

1. Install full Xcode (26.3 is verified in CI) and an iOS simulator runtime.
2. Open `PhotoScout.xcodeproj`, choose the **PhotoScout** scheme and an iPhone or
   iPad simulator, then Run. There are no third-party native dependencies.
3. For a physical device, select your Apple Development Team under Signing &
   Capabilities. The default bundle identifier is `net.aisoup.photoscout`; change
   it if your team requires a different identifier. Keep the callback URL scheme
   and server callback constant aligned if changing that scheme.
4. Native login backend changes were deployed on 2026-10-12. Existing Google web
   OAuth credentials and HTTPS callback URLs are reused; no API key or OAuth secret
   belongs in the app. A real Apple-device Google login still needs manual verification.

```sh
xcodebuild -project ios/PhotoScout.xcodeproj -scheme PhotoScout \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
xcodebuild -project ios/PhotoScout.xcodeproj -scheme PhotoScout \
  -destination 'platform=iOS Simulator,name=iPhone 16' CODE_SIGNING_ALLOWED=NO test
```

Choose a simulator name installed on your machine for the second command. A
manual GitHub Actions workflow (`Photo Scout iOS`) builds and runs the native
unit tests on iPhone and iPad macOS runners when dispatched. It also loads the live
website in WKWebView and uploads simulator screenshots, app bundles and test results.
The checkout includes `ios/` and `tests/`, avoiding the large website artwork assets.
CI also runs the JavaScript bridge regression tests.

## Native integration

- Persistent WebKit storage retains cookies and website preferences between launches.
- Existing responsive CSS handles the notch, home indicator, rotation and keyboard.
  No additional navigation bar covers the map or duplicates website controls.
- Camera preview and file input use WebKit's system permission / photo picker UI.
- Location requests use Core Location with foreground-only permission, denial
  feedback and a timeout. Each JavaScript caller's timeout is respected, including
  ignoring late native replies after expiry. The web map remains usable without location permission.
- Image Share opens the iOS share sheet. Download uses WKDownload and offers Save
  to Files / sharing. The sheet has an iPad popover anchor and deletes temporary
  files after dismissal.
- External HTTPS links open a dismissible system browser. Native capabilities are
  restricted to the canonical HTTPS origin's main frame; third-party map frames
  receive no access to the bridge.
- Google sign-in uses ASWebAuthenticationSession. A random verifier remains in
  memory; its S256 challenge is bound to the server OAuth state. After Google's
  existing signature/audience/nonce checks, a 60-second, one-use handoff code
  returns through `net.aisoup.photoscout://auth`. A same-origin POST exchanges it
  for an HttpOnly WebKit cookie. Guest tasks are attached in the web view's session;
  account history remains server-side. Signing out revokes related browser/app
  sessions using the existing session-family rules.
- Successful native sign-in refreshes account state in place, preserving selected
  selfie files and studio edits instead of reloading the whole page. A failed
  account refresh reports an error and leaves those edits intact.
- On foreground return, the website's task recovery resumes. iOS does not keep
  the webpage running while suspended; server jobs continue independently.

Apple references: [system authentication](https://developer.apple.com/documentation/authenticationservices/aswebauthenticationsession),
[WebKit downloads](https://developer.apple.com/documentation/webkit/wkdownload).

## Validation and release status

Implemented source, backend integration and automated checks are in this change.
The local Mac now has Xcode 27.0 (27A266a) and the iOS 27 SDK installed. An unsigned
physical-device target build passed locally on 2026-10-12. This verifies compilation,
not installation or execution on a physical device.
The app uses the UIScene window lifecycle required by the iOS 27 SDK. Local iOS 27
simulator tests passed on iPhone and iPad (5 tests each), and the iPhone app launch
was visually checked. JavaScript bridge regression: 8 passed.
Remote CI uses Xcode 26.3 and iOS 26 simulators. The earlier iPhone and iPad builds and
XCTest runs passed on 2026-10-12, including loading the production website in
WKWebView with its native bridge. Earlier backend regression: 570 passed.
Physical-device tests have **not** been completed.

Build evidence: https://github.com/impanyu/agentic_services/actions/runs/38294090954

The first Xcode 16.4 / iOS 18.5 runtime attempt compiled but could not launch
because that simulator was missing libswiftWebKit.dylib; CI now pins Xcode 26.3
and selects an iOS 26 runtime.
The native login exchange endpoint is **deployed**. Production health and invalid-code
rejection were verified; this does not substitute for a real-device Google login.

Before distributing a build, verify on iPhone and iPad:

- App launch, offline error/retry, portrait/landscape, keyboard, safe areas.
- Search with Enter and button, map layers, Street View and saved history.
- Google login, cancel/retry, app relaunch, logout, guest-to-account history.
- Location allowed / denied / approximate; map selection without permission.
- Camera, picker, HEIC upload, composition, avatar selection and completed photos.
- Share cancellation/success, Download to Files and iPad popover behavior.
- Background a running search/composition, return, and recover its result.

A local Personal Team development certificate and device profile are configured;
Photo Scout was installed and launched on the owner's iPhone on 2026-10-12.
The selected map/selfie icon was built, installed and launched in the same device
update. This is not proof of complete real-device functional testing.
No TestFlight upload or App Store submission is configured. Store release also requires reviewing the privacy labels against
actual backend practices, account deletion and login requirements, and the app's
eligibility under Apple's review guidelines. The privacy manifest is a starting
inventory for this implementation, not a completed App Store privacy submission.

## Signing and TestFlight handoff

Organization enrollment is pending. A local Apple Development signing identity is
installed for Personal Team testing; no signing secrets are configured in GitHub. Simulator
artifacts are not installable iPhone/iPad IPA files.

Personal-device debugging does not require paid membership or an App Store release.
Sign in under Xcode Settings → Apple Accounts, connect and trust the iPhone, enable
Developer Mode on the device, and select the Personal Team under Signing & Capabilities.
Use a unique development bundle identifier if the production identifier is unavailable.
Free provisioning expires after seven days and requires rebuilding/reinstalling.
See [Apple's account overview](https://developer.apple.com/help/account/basics/about-your-developer-account).

For TestFlight, after membership activation:

1. Sign in to the enrolled Apple account in Xcode Settings → Accounts and select
   the correct team. Register `net.aisoup.photoscout` and create the matching App
   Store Connect app record under that team.
2. On a Mac with full Xcode, run `APPLE_TEAM_ID=YOURTEAMID SCOUT_BUILD_NUMBER=1
   ./ios/archive.sh`. Increase the build number for later uploads. The script
   creates a signed Release archive and does not upload or publish it.
3. Open `ios/build/PhotoScout.xcarchive` in Xcode Organizer, validate the archive,
   then Distribute App → App Store Connect → Upload.
4. Wait for processing, complete the applicable encryption/privacy/beta information,
   then add internal testers. External testing may require Apple's beta review.
5. Verify the manual device checks above before requesting App Store review.

Never commit signing certificates, private keys, provisioning profiles, or Apple
account credentials. Membership identity verification and payment must be completed
in Apple's own interface.

## Remaining work reviewed on 2026-10-12

- Complete physical-device checks above: Google login, upload/camera and multi-person
  composition, approximate/denied location, photo saving/sharing, background recovery,
  keyboard and rotation. Automated simulator tests and a device launch are partial evidence.
- Add in-app account deletion with associated server-side data cleanup. The current
  privacy page only directs deletion requests to contact support; account routes provide
  login/logout, not account deletion.
- Review login guideline 4.8: primary account authentication currently uses Google.
  Add an equivalent privacy-preserving login option (typically Sign in with Apple)
  unless a documented exception applies.
- Add community reporting, user blocking, moderation and operational handling.
  Current publication routes allow comments/reactions and owner deletion but do not
  provide report/block endpoints; comment posting has rate limits, not content filtering.
- Update privacy disclosure and consent for configured AI image providers, including
  Google/Gemini. The current photo-studio privacy paragraph describes OpenAI only.
  Reconcile retention, third-party sharing, consent, privacy manifest and App Store labels.
- Fix native location-denial guidance in `photo-scout-site/location.js`: it currently
  tells iPhone users to enable Safari website permissions instead of Photo Scout permissions.
- Before public distribution, verify rights for the chosen Totoro character icon and
  bundled avatar artwork; use licensed assets or an original character if needed.
- Finish organization membership, distribution signing, TestFlight, store screenshots,
  review access/instructions and age rating. Review hybrid-app functionality under 4.2;
  native integrations alone do not guarantee acceptance. If paid digital features are
  enabled in iOS, review the applicable storefront payment requirements before shipping.

Review reference: https://developer.apple.com/app-store/review/guidelines/
