#!/bin/bash
# Run on a Mac with full Xcode and an enrolled Apple Developer account in Xcode.
# Produces a signed archive for Organizer; this script does not upload a release.
set -euo pipefail
: "${APPLE_TEAM_ID:?Set APPLE_TEAM_ID to your Apple Developer team ID}"
if [[ ! "$APPLE_TEAM_ID" =~ ^[A-Z0-9]{10}$ ]]; then
  echo 'APPLE_TEAM_ID must be the 10-character Apple team identifier.' >&2
  exit 1
fi
xcodebuild -version
SCOUT_ROOT=$(cd "$(dirname "$0")" && pwd)
SCOUT_BUILD_NUMBER=${SCOUT_BUILD_NUMBER:-1}
if [[ ! "$SCOUT_BUILD_NUMBER" =~ ^[1-9][0-9]{0,3}$ ]]; then
  echo 'SCOUT_BUILD_NUMBER must be an integer from 1 to 9999; increase it for each upload.' >&2
  exit 1
fi
xcodebuild -project "$SCOUT_ROOT/PhotoScout.xcodeproj" -scheme PhotoScout \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath "$SCOUT_ROOT/build/PhotoScout.xcarchive" \
  DEVELOPMENT_TEAM="$APPLE_TEAM_ID" CURRENT_PROJECT_VERSION="$SCOUT_BUILD_NUMBER" \
  -allowProvisioningUpdates archive
printf 'Archive ready: %s\n' "$SCOUT_ROOT/build/PhotoScout.xcarchive"
printf '%s\n' 'Open the archive in Xcode Organizer, validate, then choose Distribute App → App Store Connect → Upload.'
