# Photo Scout — iPhone / iPad

This is an iOS 16+ UIKit/WKWebView app. It loads the canonical Photo Scout website,
so the map, controls, search, Street View, history, avatar library, photo studio,
comments, and publications use the same interface and backend as the Web App.
It requires an internet connection. This is a hybrid app, not a separate SwiftUI
reimplementation or an offline copy of the website.

## Open and run

1. Install full Xcode (16 or newer) and an iOS simulator runtime.
2. Open `PhotoScout.xcodeproj`, choose the **PhotoScout** scheme and an iPhone or
   iPad simulator, then Run. There are no third-party native dependencies.
3. For a physical device, select your Apple Development Team under Signing &
   Capabilities. The default bundle identifier is `net.aisoup.photoscout`; change
   it if your team requires a different identifier. Keep the callback URL scheme
   and server callback constant aligned if changing that scheme.
4. Deploy the backend changes in `accounts.py` and `native_auth.py` before testing
   Google login. Existing Google web OAuth credentials and HTTPS callback URLs
   are reused; no API key or OAuth secret belongs in the app.

```sh
xcodebuild -project ios/PhotoScout.xcodeproj -scheme PhotoScout \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
xcodebuild -project ios/PhotoScout.xcodeproj -scheme PhotoScout \
  -destination 'platform=iOS Simulator,name=iPhone 16' CODE_SIGNING_ALLOWED=NO test
```

Choose a simulator name installed on your machine for the second command. A
manual GitHub Actions workflow (`Photo Scout iOS`) builds and runs the native
unit tests on a macOS runner when dispatched.

## Native integration

- Persistent WebKit storage retains cookies and website preferences between launches.
- Existing responsive CSS handles the notch, home indicator, rotation and keyboard.
  No additional navigation bar covers the map or duplicates website controls.
- Camera preview and file input use WebKit's system permission / photo picker UI.
- Location requests use Core Location with foreground-only permission, denial
  feedback and a timeout. The web map remains usable without location permission.
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
- On foreground return, the website's task recovery resumes. iOS does not keep
  the webpage running while suspended; server jobs continue independently.

Apple references: [system authentication](https://developer.apple.com/documentation/authenticationservices/aswebauthenticationsession),
[WebKit downloads](https://developer.apple.com/documentation/webkit/wkdownload).

## Validation and release status

Implemented source, backend integration and automated checks are in this change.
The implementation machine has Command Line Tools but **no full Xcode / iOS SDK**.
Swift parsing, origin-policy checks, backend tests and JavaScript bridge tests can
run there; an iOS build, simulator run and device tests have **not** been completed.
The new backend endpoint is **not yet deployed**. The app cannot complete native
login against production until it is deployed.

Before distributing a build, verify on iPhone and iPad:

- App launch, offline error/retry, portrait/landscape, keyboard, safe areas.
- Search with Enter and button, map layers, Street View and saved history.
- Google login, cancel/retry, app relaunch, logout, guest-to-account history.
- Location allowed / denied / approximate; map selection without permission.
- Camera, picker, HEIC upload, composition, avatar selection and completed photos.
- Share cancellation/success, Download to Files and iPad popover behavior.
- Background a running search/composition, return, and recover its result.

No signing team, provisioning profile, TestFlight upload or App Store submission
is configured. Store release also requires reviewing the privacy labels against
actual backend practices, account deletion and login requirements, and the app's
eligibility under Apple's review guidelines. The privacy manifest is a starting
inventory for this implementation, not a completed App Store privacy submission.
