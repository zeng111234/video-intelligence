# Video Editor Design QA

- source visual truth path: `C:\Users\zeng\.codex\generated_images\019fa69b-c7d4-7de3-9421-8d01f3991581\call_JUgUOCkSsFcX7X9CxDBqux2Q.png`
- implementation screenshots: `data/design-qa/video-editor-v2-final.png`, `data/design-qa/video-editor-v2-subtitle.png`, `data/design-qa/video-editor-v2-subtitle-light.png`, `data/design-qa/video-editor-v2-1440.png`, `data/design-qa/video-editor-v2-1024.png`, `data/design-audit/title-redesign/06-final-strict-portrait.png`, and `data/design-audit/title-redesign/08-final-strict-caption.png`
- comparison evidence: `data/design-qa/video-editor-v2-final-comparison.png`, `data/design-qa/video-editor-v2-subtitle-comparison.png`, `data/design-qa/video-editor-v2-subtitle-light-comparison.png`, and `data/design-audit/title-redesign/07-reference-vs-final-portrait.png`
- desktop viewport: 1440 × 900 CSS px, density 1; narrow viewport: 1024 × 900 CSS px, density 1
- latest desktop capture: 1310 × 902 CSS px, density 1; source image 941 × 1672 px; focused comparison normalized both portrait regions into a 760 × 620 px board.
- comparison state: the same selected talking-head source, plan preview selected, title entry and caption cue inspected separately.

## Full-view comparison evidence

The 1440 px browser capture shows the material selector, vertical preview, plan/cost panel, and contextual primary action in one screen. The 1024 px capture switches to the existing segmented sections without horizontal overflow or hidden persistent controls.

## Focused comparison evidence

The selected reference and browser preview were combined into the two comparison images above. The title is now upper-left, limited to two eight-character lines, with a purple accent and restrained contrast treatment. Captions are limited to two ten-character lines, sit in the lower safe area, and support one approved purple emphasis term without changing spoken text.

## Findings and fixes

- Fixed P1: the former centered 42 px title and 42 px captions obscured the speaker. The v2 layout separates title and caption hierarchy, uses source-canvas proportional sizing, and prevents browser-side secondary wrapping.
- Fixed P1: initial silence skipping previously skipped the visible title window in the browser preview. The preview now tracks effective playback time separately from source time.
- Fixed P2: old live batches could return v1/v2 visual metadata. The frontend now falls back to the v3 specification until the backend is restarted, so the page remains usable during rollout.
- Fixed P1: the title still used the same YaHei family and the live preview frame measured about 321 × 510 instead of 9:16. The v3 title now uses the OFL-licensed Smiley Sans display face in preview and a generated transparent PNG watermark for MPS. The preview frame now measures about 288 × 510 (ratio 0.5636), matching 9:16 within subpixel rounding.
- Fixed P2: CSS text and ASS could not guarantee the same display-title font on MPS. The formal render now uploads the approved title PNG and submits it as a timed MPS image watermark, while subtitles remain the human-approved ASS track.
- Accepted constraint: the generated reference has a different source-frame crop from the authorized video. The implementation intentionally preserves the original material framing rather than zooming or tracking the speaker.

## Comparison history

- Pass 1: title and caption both oversized; rebuilt the shared style contract, ASS styles, and preview layout.
- Pass 2: reference comparison showed the title was too close to the top-left edge; moved it inward/down and aligned the line break to the selected reference.
- Pass 3: checked title and captions in the live in-app browser, then verified wide and 1024 px layouts. No actionable P0/P1/P2 differences remain.
- Pass 4: the live caption cue was still visually too close to traditional TV subtitles. Reduced the 720p type from 34 px to 32 px, switched to a medium (not bold) weight, reduced the outline from 2 px to 1 px, and replaced the hard dark edge with a softer two-pixel shadow. The browser preview and the MPS ASS style share these values; the focused live comparison is saved above.
- Pass 5: the title family had not actually changed. Added Smiley Sans, a distinct 54 px display treatment, a crisp shadow, and a 52 × 6 px purple accent; implemented the same approved title as an MPS PNG watermark.
- Pass 6: the live comparison exposed a non-9:16 preview container. Changed the preview sizing from independent width and height constraints to a height-led 9:16 aspect ratio, then recaptured the title and caption states. No actionable P0/P1/P2 mismatch remains; the different source crop is intentional because the authorized original video is preserved.

final result: passed
