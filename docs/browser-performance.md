# Embedded browser performance

Preloading replacement images improved desktop frame cadence in these tests. Typical typing and scrolling latency stayed largely unchanged; the long local article had a shorter upper tail of scroll-response delays. The live Wikipedia page showed a smoother stream without a consistent input-latency improvement.

Tested against the original Puppy 1.0.474 viewer using Chrome for Testing 152.0.7977.75 on Linux, on an AMD Ryzen 7 PRO 8845HS. JPEG quality, capture cadence, acknowledgements, backend code and websocket compression were identical.

Each figure below is the median of three per-run measurements. Gap counts are summed across those runs. Input means typing for the dashboard and wheel-to-visible-scroll response for the other pages.

| Viewport / workload | Distinct frame FPS, before → after | Input median, ms | Input p95, ms | Gaps >50 ms |
| --- | ---: | ---: | ---: | ---: |
| Desktop / Animated dashboard + typing | 53.8 → 58.5 | 41 → 41 | 47 → 43 | 1 → 0 |
| Desktop / Long article + scrolling | 38.4 → 54.8 | 104 → 104 | 153 → 106 | 52 → 0 |
| Desktop / Live Wikipedia + scrolling | 39.9 → 48.3 | 119 → 120 | 153 → 157 | 39 → 3 |
| Phone-sized / Animated dashboard + typing | 57.7 → 57.7 | 40 → 40 | 46 → 43 | 3 → 3 |
| Phone-sized / Long article + scrolling | 60.0 → 60.0 | 85 → 85 | 86 → 86 | 0 → 0 |
| Phone-sized / Live Wikipedia + scrolling | 59.9 → 59.9 | 84 → 85 | 102 → 101 | 1 → 0 |

The desktop local article gained about 43% more distinct frames, its p95 scroll response improved about 31%, and its 52 gaps longer than 50 ms disappeared. Wikipedia gained about 21% more distinct frames and went from 39 long gaps to 3. The phone-sized pages were already close to their source frame rate.

Animation freshness is reported separately below. Frame age includes the source page’s rendering and capture delay. Matching-frame delay compares the very same source timestamp in both viewers; positive values mean the changed viewer exposed that frame later.

| Viewport / workload | Frame age median, ms, before → after | Frame age p95, ms | Matching-frame delay median / p95, ms |
| --- | ---: | ---: | ---: |
| Desktop / Animated dashboard + typing | 36 → 36 | 37 → 37 | 0 / 1 |
| Desktop / Long article + scrolling | 53 → 54 | 54 → 55 | 0 / 2 |
| Desktop / Live Wikipedia + scrolling | 54 → 69 | 76 → 88 | 0 / 16 |
| Phone-sized / Animated dashboard + typing | 34 → 35 | 35 → 35 | 0 / 1 |
| Phone-sized / Long article + scrolling | 35 → 35 | 36 → 36 | 0 / 1 |
| Phone-sized / Live Wikipedia + scrolling | 35 → 35 | 36 → 36 | 0 / 1 |

The median matching-frame delay was 0 ms in every workload. Wikipedia’s absolute frame-age distribution became older even though more frames were visible: its median rose from 54 to 69 ms. The two distributions contain different sets of frames because the original viewer misses more of them. This result supports smoother rendering, rather than a broad claim of fresher animation or faster input on every site.

The per-run summaries, source hashes and browser version are in [browser-performance-results.json](browser-performance-results.json). Raw samples remain under `data/browser-performance/result-*.json`.

## Change and checks

The viewer prepares each replacement in a separate image element, then swaps it into the page on load. It keeps at most one image load and the newest waiting frame. Failed images leave the last good picture visible; hide, reconnect and close invalidate unfinished frames. Blob URLs are released after presentation or failure, and cursor styling survives replacement. The FPS pill now counts frames put into view.

The first pointer or wheel event in a gesture is sent immediately. Subsequent events stay coalesced per display frame, wheel distance is conserved within each batch, and pending pointer movement is flushed before mouse-button release. Delayed input is cancelled when the viewer hides or reconnects.

Unit tests cover delayed and failed images, queue bounds, resource cleanup, stale completions, cursor preservation, event ordering and backpressure. The real console test also checks native clicking and typing into the embedded page, image dimensions, hidden-viewer suppression and fresh pixels on resume.

Canvas rendering, explicit `Image.decode()` waits, overlapping decodes and disabling websocket compression were explored. They did not give a consistently better balance of cadence and latency here. The retained image-load path avoids the extra display-frame delay seen with an explicit decode wait. The image loading and decoding distinction is documented by [MDN](https://developer.mozilla.org/en-US/docs/Web/API/HTMLImageElement/decode).

## Measurement method

`tests/browser_performance_compare_test.py` opens two real console profiles watching the same managed browser. One runs the original `BrowserView`; the other runs the changed class. Both use the production authenticated websocket, image path and input handlers, and receive identical source frames. Input originates from alternate viewers on successive repeats; both observations use that same native input timestamp. This controls for source-rendering and capture-phase differences that affected early tests using separate source browsers.

There are three repeats for each of six workload/viewport pairs. Console sizes are 1440 × 900 and 390 × 844; embedded page sizes are 1168 × 774 and 374 × 718 CSS pixels. The local pages exercise animated dashboard cards, typing and scrolling a long article. The external workload loads the public [Wikipedia Web browser article](https://en.wikipedia.org/wiki/Web_browser).

Each typing run sends 37 keys and pointer moves. Each scrolling run sends 37 wheel events totaling 1776 CSS pixels, plus pointer moves. Loading and startup are outside the timed interval. A small page marker encodes a timestamp, input counters, pointer coordinate and scroll position. The console reads the marker from its actual image once per animation frame. Sampling begins only after valid frames appear; unreadable markers during measurement or unacknowledged key/wheel events fail the run.

Distinct frame FPS counts changed images available to the viewer, rather than websocket arrivals. Input latency ends when the resulting marker or scroll-position change appears in sampled pixels. Two viewer profiles and one source browser run on the same host, using its shared clock. The marker forces ongoing frame damage on the otherwise static article. Ordinary idle pages can send no frames.

These are loopback, headless Chromium measurements near 60 Hz. Phone-sized emulation uses the same desktop CPU. Physical screen scanout, physical phones, Safari/Firefox, remote network delay and restricted bandwidth were not measured. Observer frame phase contributes up to roughly 17 ms of timing variation. These data do not establish a meaningful 1 ms improvement in input latency. Some runs logged aiohttp closing-transport warnings during teardown in both variants, after the timed observations.

## Reproduce

From the repository root:

```sh
mkdir -p data/browser-performance
git show 2acdf7f91f262139868d2070c3c25bb475cd0549 > data/browser-performance/before-app.js
python3 tests/browser_performance_compare_test.py \
  --baseline data/browser-performance/before-app.js \
  --output data/browser-performance/result-local.json
python3 tests/browser_performance_compare_test.py \
  --baseline data/browser-performance/before-app.js \
  --external-url https://en.wikipedia.org/wiki/Web_browser \
  --output data/browser-performance/result-wikipedia.json
```

`tests/browser_performance_test.py` also supports single-viewer trials, `--viewer-source` for a saved class, and `--compare-source` for alternating separate-browser runs. Its `--scenario dashboard|article` and `--viewport desktop|phone` options narrow a trial. Both harnesses default to three repeats and six seconds per workload; durations are bounded to keep the marker counters from wrapping.
