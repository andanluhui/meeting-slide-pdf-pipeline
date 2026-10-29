# Authorized website / EADV-style video source adapter (v1.3)

This module handles **authorized** video requests already visible to the user in a logged-in browser. It is not a scraper that bypasses authentication or DRM. **Do not share HAR exports or browser profiles.** The existing PDF engine is not changed.

## Route A: browser DevTools HAR export (closest to a manual EADV workflow)

1. Open the official video website in your own browser and sign in normally.
2. Open Developer Tools -> Network; enable *Preserve log* and start actual video playback. Repeat across the authorized video pages to be captured. Ensure the media resource itself is visible in Network (often `video.mp4`).
3. Export the captured network requests to a local HAR file. **HARs can include signed links, Cookie and Authorization tokens**: treat the file like a password; never send it to colleagues or upload it to a repo.
4. Inspect available media hosts *without downloading*:

```bash
python scripts/authenticated_video.py har --har "/private/path/network.har" --dry-run
```

5. Explicitly approve the video CDN host(s), then download and process:

```bash
python scripts/authenticated_video.py har --har "/private/path/network.har" \
  --allow-host media.example.org --out "/private/work/video-jobs"
```

The tool can recognize MP4, MOV, WebM and MKV resources. It detects HLS/DASH manifest requests but marks them *unsupported* rather than attempting to decrypt or covertly download streams. HAR exports sometimes omit authentication headers. If a request fails with HTTP 401/403, re-login in your browser, play the video again and re-export the HAR; access must be granted by the website.

## Route B: browser-assisted video discovery (no manual DevTools export)

Install optional dependencies once:

```bash
python -m pip install -r scripts/requirements-browser.txt
python -m playwright install chromium
```

Start a separate browser profile and log in through the normal website UI:

```bash
python scripts/authenticated_video.py browser --url "https://portal.example.org/videos" \
  --allow-host media.example.org --out "/private/work/video-jobs"
```

While the browser window is open, click Play on videos and visit the authorized pages. Press Enter in the terminal when finished. Only observed media resources are candidates. This module does not try to guess unknown session URLs, extract hidden tokens or bypass an interactive CAPTCHA. It does not connect to an existing personal Chrome session by default.

Batch page navigation can use a private text file with one **authorized video page URL per line**:

```bash
python scripts/authenticated_video.py browser --url "https://portal.example.org/login" \
  --pages-file "/private/work/session_pages.txt" --visit-seconds 15 \
  --allow-host media.example.org --out "/private/work/video-jobs"
```

The browser profile is stored inside `video-jobs/private_browser_profile` unless `--profile-dir` overrides it. **This directory contains browser-managed session data and may retain login cookies on disk.** Keep it private. The downloader's in-memory request headers are not written to the task report. To sign out completely, sign out in the browser and remove the profile when it is no longer needed.

## Route C: reuse a verified video-address pattern (EADV-style batch download, v1.3.1)

**Plain-language summary:** Sign in normally, play one video and confirm its actual media request. If a *user-supplied list of authorized session IDs* uses the same URL structure, substitute only the session-number path segment, and download the listed sessions. This is not arbitrary `.mp4` suffix replacement and does not enumerate unknown sessions.

Example verified request: `https://media.example.org/session/100/video.mp4`. Authorized IDs file `session_ids.txt` contains `100`, `101`, `102` on separate lines. Export a local HAR after playing video `100`, then run:

```bash
# First: preview without downloading anything
python scripts/batch_by_pattern.py --har "network.har" --ids-file "session_ids.txt" \
  --allow-host media.example.org --dry-run

# Second: after confirming the source host and authorized session list
python scripts/batch_by_pattern.py --har "network.har" --ids-file "session_ids.txt" \
  --allow-host media.example.org --out "video-jobs"
```

- `--example-id 100` disambiguates if HAR contains more than one possible video.
- Supported input lists: `.txt` one ID per line or `.csv` with an `id` column. No automatic scanning for other IDs; default limit is 300 explicitly listed sessions.
- The actual observed video address, its host, and its file type must be valid. A player web page is **not** silently rewritten into a guessed MP4 URL.
- The URL's signed query parameters, if any, remain unchanged during path substitution. They may apply to only the original session; if 401/403 occurs, the batch stops and the user must reauthenticate or capture video requests individually. No authentication/DRM bypass.
- HAR credentials remain in memory only while processing; output reports omit raw URLs, cookies and tokens. Keep the HAR and `session_ids.txt` private.
- Uses `media_sources.download_media` for host allowlisting, resumable downloads, and credential isolation; successful files pass to the same PDF generator. It does not guarantee EADV compatibility until tested with the real site.

## Output / failure behavior

- Downloaded video: `<out>/videos/video-<id>.mp4` (or media-specific extension).
- Video-to-PDF output: `<out>/pdf/video-<id>-slides.pdf` plus layout preview, contact sheets and QA JSON from the v1.2 engine.
- Activity report: `<out>/media-report.json` stores **only hashed resource IDs, hostname, file names and statuses**, never full request URLs, query tokens, Cookie or Authorization headers.
- Failed `.part` video files are retained for HTTP Range continuation. Existing completed video/PDF files are skipped by default; original sources are not deleted.
- `--download-only` stops after retrieval; `--dry-run` inspects candidate counts without downloading.
- `401/403` is reported as an expired/insufficiently authorized session. Re-login/replay; the tool never brute-forces, refreshes tokens by guessing or bypasses access controls.
- HTTP redirects require explicit host approval, and credentials are stripped when destination host **or port** changes.
- By default, local/private IP hosts are blocked. Use `--allow-private-hosts` only for authorized internal test servers.

## Known limits

- The Playwright browser executable is optional and **was not exercised against the actual EADV portal**. Different players may require a site-specific adapter, including service-worker media, DRM, HLS/DASH and embedded frame interactions.
- Automatic navigation does not guarantee playback starts. Some video pages need a manual click; the browser mode pauses for this.
- HAR exports are sensitive and may omit the very credentials needed to download a video. The website's normal authentication and usage restrictions still apply.
- Downloading a large meeting library is only the first step. Correct speaker assignment and agenda reconciliation still require the existing advanced pipeline or user-provided chapter timestamps.
- Never automatically upload resulting PDF or source video to a third-party service without explicit permission.
