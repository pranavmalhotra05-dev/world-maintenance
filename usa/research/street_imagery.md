# Lane SV: real street-level photos per 3D block

Script: `usa/street_imagery.py` (`.venv\Scripts\python -u -W ignore -m usa.street_imagery [slug ...]`).
Output per block: `web/usa/blocks/<slug>/street_imagery.json`, thumbnails `street/<source>_<id>.jpg`
(whole frame downscaled to 640 px wide, 120 KB or less, at most 60 per block), `meta.street_imagery` and `meta.files`
(written inside `usa.blocklock.block_lock`).

Only real photos taken by people with cameras are used. Nothing is generated, in-painted or synthesised.

## Sources and licence evidence (checked 2026-11-24 in the built-in browser)

### KartaView (Grab): used
- Terms page https://kartaview.org/terms, section 4 "Open Source License". It licenses the street images on KartaView
  under CC BY-SA 4.0 and says that anyone is "free to copy, distribute, transmit and adapt data on KartaView" as long
  as they follow the licence and credit Grab and the contributors. The required credit is
  **"© Grab and KartaView Contributors"**.
- CC BY-SA 4.0 explicitly allows commercial use, so it passes the owner's licence rule. The ShareAlike term applies:
  our downscaled thumbnails are adaptations and must also be shared under CC BY-SA 4.0 with attribution. This covers
  only the photos. It does not extend to our other data.
- Section 15 (Grab IP) reserves rights in Grab's own platform materials "save where stated otherwise". Section 4
  states otherwise for the imagery. We do not reuse Grab or KartaView logos or site content, only photos plus credit.
- API: public and keyless. `GET https://api.kartaview.org/2.0/photo/?lat&lng&radius(<=500)&itemsPerPage(<=150)&page`
  returns the blur status (`autoImgProcessingResult`), the CDN thumbnail URLs (`imageLthUrl`) and `shotDate`.
  `POST https://api.kartaview.org/1.0/list/nearby-photos/` (lat, lng, radius, page, ipp) returns many photos that the
  v2 radius search leaves out (Denver: 58 versus 3 inside the bbox) and carries the contributor `username`. The script
  takes the union of both. For each v1-only id it fetches `GET /2.0/photo/{id}`.
- The `storage*.openstreetcam.org` hosts timed out. The `cdn.kartaview.org` URLs worked.
- Attribution kept per image: credit, username, sequence id and photo id, plus a link to the
  `kartaview.org/details/<seq>/<index>` page.

### Mapillary (Meta): code ready, skipped (no token)
- Terms page https://www.mapillary.com/terms (effective 2024-02-15):
  - §3: use of other users' User Content is under CC BY-SA unless Mapillary indicates otherwise. Some datasets may be
    CC BY-NC-SA, and we must not use those.
  - §11 (Developers): you must register an application to get a client token. Applications must "materially
    supplement" Mapillary and not merely redistribute content. **Downloading images and serving them from your own
    servers is allowed, but you must visibly display the Mapillary logo and link to the Mapillary homepage or the
    image page.** So caching thumbnails is permitted with that attribution, and the 3D page must show the logo and a
    link when it displays a Mapillary thumb.
  - §12 (Commercial): commercial use is limited to (i) improving or developing products, services, maps, apps and
    datasets, and (ii) providing services to clients. It also requires technical and business safeguards against
    re-identification or unblurring, and a process to report incidents to vendor-incident@meta.com.
  - §5: no attempts to re-identify or unblur, no real-time navigation use, and no scraping outside the published
    APIs.
- Token: put it in `usa/local_secrets.json` as `{"mapillary_token": "MLY|..."}`. Use
  `usa/local_secrets.example.json` as the template; `.gitignore` already lists `usa/local_secrets.json`. The script
  reads it only from there and never prints it or writes it to any output. Without a token the status is
  `"no token"`.
- Query: `GET https://graph.mapillary.com/images?fields=id,computed_geometry,geometry,compass_angle,computed_compass_angle,captured_at,thumb_256_url,thumb_1024_url,sequence,creator&bbox=W,S,E,N&limit=2000`.
- The thumbnail URLs are signed and expire. The local cached thumbnail is therefore the durable asset. The `url` field
  points to `https://www.mapillary.com/app/?pKey=<id>`.

## Artifact CSP limitation
Published artifact pages block external image URLs. Only the local `street/*.jpg` thumbnails can render inside the
artifact. Entries with `thumb: null` (platform blur status not BLURRED, past the 60-thumbnail cap, or deduplicated)
can only show a link out to the platform. The page must show the attribution string, and the Mapillary logo for
Mapillary images.

## Privacy
- No face, person or plate detection or other processing. Frames are downscaled whole and never cropped or zoomed.
- KartaView frames whose `autoImgProcessingResult` is not `BLURRED` (for example `ORIGINAL`) are stored as metadata
  only and never downloaded. Mapillary blurs every image before serving it.

## Placement and selection
- Search area: `meta.bounds` + 50 m. x/y are local metres from `meta.origin` in the house3d convention
  (Web-Mercator metres / k, y = north). `heading_deg` is the camera compass bearing. KartaView heading 0 is treated as
  "not recorded" (`null`).
- Thumbnail choice: one image per 4 m cell and heading quadrant (newest wins). In-block images come first, then
  farthest-point sampling up to 60, so the walker always has a nearby frame.

## Coverage (run 2026-11-24, KartaView only)
| block | KV photos (bbox+50 m) | in block | thumbs | newest capture |
|---|---|---|---|---|
| boulder-co | 10 | 6 | 10 | 2024-06-13 |
| dallas-tx | 24 | 0 | 20 | 2018-04-06 |
| denver-co | 58 | 0 | 48 | 2020-11-01 |
| phoenix-az | 45 | 14 | 37 | 2018-05-19 |
| other 12 blocks | 0 | 0 | 0 | none |

The zero-coverage blocks were cross-checked with the KartaView sequence-coverage tile at z17, which was empty for
Austin, Minneapolis and Kansas City. Mapillary normally covers far more of the US and is the expected fill-in once a
token is added.
