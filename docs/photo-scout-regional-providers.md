# Photo Scout regional provider plan

Reviewed October 10, 2026. This is an implementation plan, not a deployed China
integration. No domestic provider credentials, access or Mainland connectivity were
verified in this review, and no paid service was purchased.

## Goal and current dependencies

Support both searching for a Mainland China location and using the website from
Mainland China. Those are separate requirements: a visitor in San Francisco can
search Chengdu; a visitor in Chengdu can search San Francisco. Select content
providers by the requested location and browser delivery by actual reachability and
explicit configuration. Do not infer either exclusively from IP, language or a
rectangular coordinate test.

Current checkout findings:
- `intent.geocode` selects Google Places under the Google provider setting; the
  alternate path uses Photon. A non-Google geocoder exists, but its Mainland
  coverage and availability have not been established.
- `program.Tools.search_places` rejects free-text POI retrieval unless the configured
  provider is Google Places. Merely disabling Google is therefore insufficient.
- The browser has non-Google basemaps, but remote OSM, OpenFreeMap, Esri and USGS
  endpoints are not verified from a Mainland network. US-only layers do not provide
  a China basemap.
- Interactive Street View and Google OAuth need independent alternatives. Browsing
  and anonymous use must not require Google login.

## Provider evaluation order

| Capability | Mainland evaluation | Status / boundary |
|---|---|---|
| Address resolution and keyword/nearby POIs | AMap Web Services first; Baidu alternative | Not connected; credentials and appropriate use terms needed |
| Basemap | Official AMap or Baidu integration appropriate to the selected data provider | Not connected; verify display, attribution and coordinate alignment; do not use unofficial tile URLs |
| Directional street imagery | Baidu Panorama static API / official panorama viewer | Not connected; advanced permission, paid activation or approved trial required |
| Additional real photographs | Authorized tourism-board, park, museum and business collections; properly licensed Commons photos where coverage exists | Evaluate per-file rights and actual location; a POI association is not a camera position |
| Portrait background | Sources explicitly permitting image adaptation and the intended commercial use | Do not equate public viewing/API access with permission for synthesis |
| Login | A non-Google sign-in method, with existing anonymous session support retained | Not implemented in this review |

AMap POI search exposes optional photos. Those are potential place illustrations,
not proof of a street-view heading or a transferable synthesis license. Mapillary
and Panoramax must be assessed by observed regional coverage; they are not assumed
to replace Google in China.

## Integration contract

1. Make the planner's place-search tool provider-neutral: the same keyword, radius,
   logical conditions and visual requirements must survive a provider switch. Never
   silently turn an arbitrary keyword into a broad scenic search.
2. Keep provider region and browser delivery profile separate. Choose eligible,
   configured providers before dispatch; use bounded timeouts and fallbacks, not a
   long Google failure followed by another full search. Preserve status reasons:
   `not_configured`, `permission_required`, `unavailable`, `no_coverage`, `ok`.
3. Every coordinate carries its CRS. Preserve original provider coordinates, and
   convert at documented API/display boundaries. WGS84, GCJ-02 and BD-09 values
   must never be relabeled as each other. AMap uses GCJ-02 for domestic services;
   its documented conversion service accepts GPS and Baidu inputs. Validate the
   complete conversion path before merging with WGS84 imagery or OSM geometry.
4. Normalize imagery capabilities separately: viewable, scorable, synthesisAllowed,
   adjustableView, cameraPositionKnown and headingKnown. Unknown permissions or
   headings remain unknown; ordinary photos do not gain eight synthetic directions.
5. Feed permitted images into the shared visual evaluation and ranking module.
   A successful POI lookup without real imagery remains an unverified candidate,
   never a visually scored recommendation.
6. Browser tiles, scripts, fonts, thumbnails, original-view links, login and share
   links all need a Mainland-network smoke test. Backend retrieval alone cannot
   establish that the product works for Mainland visitors. Verify model-service
   availability and permitted deployment separately.

## Acceptance cases

- Chengdu sculpture, cafe, a full street address and a radius-only mood search.
- Mainland keyword plus geographic constraint, e.g. lakeside cafes; preserve AND
  relationships when combining POIs and geographic features.
- Coordinate alignment: browser click, POI, street-view camera and selfie backdrop
  must refer to the same physical place after conversion.
- Google endpoints blocked: map, search, history and permitted photos still work;
  Google login cannot be the only sign-in option.
- No domestic key or no panorama coverage: give an accurate source-level status and
  usable available results, without fabricating views or broadening user intent.
- Repeat the same workflow outside China to detect routing/coordinate regressions.

## Primary references

- [AMap POI Search 2.0](https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch)
- [AMap coordinate conversion](https://lbs.amap.com/api/webservice/guide/api/convert)
- [AMap coordinate systems](https://lbs.amap.com/api/javascript-api/guide/transform/convertfrom)
- [Baidu Panorama static overview](https://lbs.baidu.com/docs/webapi?title=viewstatic/index):
  advanced paid service; a 15-day trial can be requested. No trial was requested here.
- [Baidu technical service authorization](https://lbs.baidu.com/cashier/auth?src=custommap):
  commercial access and panorama scope must be evaluated for this product; the
  public page also describes a startup program, subject to provider eligibility.
