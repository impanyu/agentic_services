# Photo Scout: Sign in with Apple

Google and Apple are separate login choices on the same sign-in page. Both establish
an HttpOnly Photo Scout session and use the existing account/history/points system.
Apple accounts use the verified Apple subject; they are not automatically merged with
Google accounts by email, including Apple's private relay addresses. Existing Google
balances/history remain with the Google account. Explicit account linking is separate work.

## Activation

The developer account must have access to Certificates, Identifiers & Profiles.
Enable Sign in with Apple on the primary App ID `net.aisoup.photoscout`, create an
associated Services ID (suggested identifier: `net.aisoup.photoscout.web`), and register:

- Domain: `photo-scout.aisoup.net`
- Return URL: `https://photo-scout.aisoup.net/api/v1/auth/apple/callback`

Create a Sign in with Apple key restricted to the primary App ID. Keep its `.p8` file
outside Git and chat. Set these on the backend only:

- `PHOTO_SCOUT_APPLE_CLIENT_ID`: Services ID
- `PHOTO_SCOUT_APPLE_TEAM_ID`: developer Team ID
- `PHOTO_SCOUT_APPLE_KEY_ID`: Apple key ID
- `PHOTO_SCOUT_APPLE_PRIVATE_KEY_FILE`: securely mounted `.p8` path, or
  `PHOTO_SCOUT_APPLE_PRIVATE_KEY`: PEM value delivered through secret configuration

The client secret is signed server-side with ES256 and a five-minute lifetime.
No private key, client secret or identity token is sent into the application's JavaScript.
The `/auth/me` response advertises only provider readiness. Unconfigured Apple login is
disabled with a customer-facing availability note, and its start endpoint returns 503.

## Web and iOS flow

Apple returns a top-level `form_post`. A separate Secure, HttpOnly, SameSite=None state
cookie binds it to the initiating browser; server state is single-use and expires in
10 minutes. The backend exchanges the code with Apple and verifies the JWT's RSA
signature, issuer, audience, expiry and nonce. Apple supplies the user's name on first
authorization only; it is retained under the verified Apple subject. Email comes only
from verified token claims. Successful POST callbacks return a 303 redirect.

The iOS wrapper uses the existing ASWebAuthenticationSession and proof-bound one-use
handoff, then establishes the cookie in the original WKWebView without losing studio
edits. It does not navigate the authenticated website inside an unrelated browser.
No native Apple entitlement is added for this web authorization flow. The primary App
ID and Services ID must still be configured in the developer account. Old installed
wrappers advertise no Apple provider and keep that choice disabled until upgraded.

## Validation

Run `pytest tests/test_photo_apple_auth.py tests/test_photo_accounts.py tests/test_photo_native_auth.py`,
the billing/bridge JavaScript tests, and Xcode's PhotoScout build/tests. Tests use signed
RSA ID tokens and validate the ES256 client secret, nonce/audience/issuer/signature/
expiry rejection, cancellation, state replay rejection, name persistence and the native
handoff. Real Apple authorization remains unverified until production credentials exist.

After activation, verify a real first and repeat Apple sign-in on Web and iPhone,
including cancellation, private relay email, sign-out, and preservation of drafts.

Apple references:
https://developer.apple.com/help/account/capabilities/configure-sign-in-with-apple-for-the-web/
https://developer.apple.com/help/account/capabilities/create-a-sign-in-with-apple-private-key/
