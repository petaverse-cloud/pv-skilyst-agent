---
name: prompt-craft
description: Camera-and-prompt craft for the video nodes on the Skilyst/Beehive platform — the cross-model structural skeleton, the per-model deltas that actually change the output, the five-part reference-lock syntax that keeps a character consistent, and the dialogue pacing rule. Use when writing or reviewing any generation prompt, before submitting a job; contains no node dependency of its own.
license: MIT
compatibility: Requires nothing but the skilyst runtime (>=0.1.0): pure methodology, no network, no credential. It declares requires.nodes = [] on purpose so it can never make a platform tool available.
metadata:
  skilyst.skill_id: skilyst/prompt-craft
  skilyst.version: "1.0.0"
---

# Prompt craft: the four rules that survive contact with a model

Load this before writing or reviewing a generation prompt. It carries only the
rules that change output; the full keyword tables, the model-by-model formula
comparison and the category templates live in the lane research corpus
(`prompt-library.md`, `cinematic-language-guide.md`, `serialized-drama-guide.md`
under `research/visual-language/` — read them when a shot needs depth, do not
duplicate them here).

## Rule 1 — the skeleton (slot order is the technique)

```
[shot size] + [subject + appearance anchors] + [ONE primary action + micro-action]
+ [scene, 3-5 elements] + [light] + [style/mood] + [audio, if the model has it]
+ [negative / constraints]
```

- **Subject must be trackable**: age, hair, wardrobe colour, accessories, build.
  Visual anchors are what lets the model recognise the same person across shots.
- **One primary action per shot.** A 5-10 s shot cannot carry a second action
  line; use environmental micro-motion (breath, wind, steam, background traffic)
  to add life — but never let it compete with the subject's action.
- **Camera at the head or in its own sentence.** Camera commands are positional,
  not decorative.
- **Light must be drawable**: `golden hour backlight with warm rim illumination`,
  never `good lighting`.
- **Negatives are model-dependent** (see Rule 2) and are only one of the levers.
- **Temporal phrasing buys rhythm**: `begins with … then … finally …`, or hard
  segmentation where the model supports it (`0-3s: … 3-6s: …`).

## Rule 2 — the per-model deltas (this is the part that changes output)

| node / model family | write it like this | trap |
| --- | --- | --- |
| `generate:minimax-h3` (H3 / V2 API) | **natural-language** camera phrasing; end with a continuity constraint (`one continuous shot`) | the 15 bracketed commands (`[Push in]`, `[Zoom in]` …) are documented for the V1 family and **not guaranteed on H3** — using them costs control, not style |
| `generate:byteplus-seedance-2.0` | multi-material referencing with `@`-style tokens per material, hard time segmentation | its duration cap depends on the mode (see `skilyst/embed-video`); the price is per second and resolution/model multipliers stack |
| `generate:google-veo` | five-part: cinematography + subject + action + context + style/ambiance, audio as its own sentence, negatives encouraged and lengthy | dialogue must use the colon form `says: "…"` — quote-only formatting has produced burned-in subtitles; lines follow the 8-second rule (12-15 words / 20-25 syllables); a camera position lands better with the in-line note `(thats where the camera is)` |
| `generate:drawnow` | text-to-video and image-to-video; same upstream family as seedance, so the same mode and duration constraints apply | treated as a cheaper sibling, not a different aesthetic |
| hosts that reject negatives (community models, e.g. the Runway-style prompters) | rewrite every negative as a positive: `locked camera` instead of `no camera movement`, `one person walking alone` instead of `no extra people` | writing negatives at a host that ignores them wastes the tokens and can invert the intent |

Cross-model calibration worth keeping: subject + movement + scene are the
required triple everywhere; framing/light/mood are the gain layer. Models are
weak on counts (a stated number of objects is usually wrong) and on complex
physics (bounces, parabolas); safety filters false-positive on innocent words, so
describe clothing instead of bodies.

## Rule 3 — the five-part reference lock (when a character must repeat)

Handing over a picture is **not** a lock: the model reads an unexplained image as
a style reference. Write all five parts:

1. **Token declaration** — name each reference (`@image1`, `@location1`,
   `@hands1`) and reuse the identical token throughout.
2. **Inheritance list, enumerated** — face, face shape, skin tone, hairstyle,
   height, build, each wardrobe item by name, footwear, bearing. Name each
   garment separately; "the same outfit" inherits nothing.
3. **Non-inheritance declaration** — background, furniture, pose, camera angle,
   original lighting, any text: state that they do NOT carry over. *Skip this and
   the reference image's composition migrates into the shot.*
4. **Cross-shot restatement** — on turns, occlusions and fast motion, restate
   that it is the same face.
5. **Negatives** — no cloning/duplication, no averaged faces, no swapping
   attributes between characters.

Sketch or line-art references additionally need `render as realistic live-action`.

## Rule 4 — dialogue and pacing

- **≤8 words per 3 seconds.** A 15-word line inside 3.3 s came in audibly too
  fast in the lane's own measurement: cut the line, never speed the render up.
- Declare the language on its own line, keep the line in the script the model was
  told to speak, and state `no silent moments and no voice-over` for continuous
  dialogue — otherwise the model scores the scene and animates the mouth anyway.
- **Write reactions as a chain, not a list**: hears → pauses → expression
  changes → body follows → aftermath. Emotional state is written as behaviour
  (looks away, pressed-together lips, slower delivery), not as a symptom.
- Move proper nouns, numbers and names out of the spoken line and onto the screen
  in post: they are mispronounced and they eat the pacing budget.
- One speaker per shot; two speakers split the mouth budget.

## Narrative craft worth applying at sequence level

- **Title each act** — the title is what bounds how much information fits.
- **Write the reversal as a picture** (`the cloud layer parts over the empty
  city`) instead of announcing a reveal.
- **Bind the emotional turn to a light change** (golden hour → blue hour).
- **Over 30 s, split into separate prompts** and cut them together — every video
  node here has a hard per-job cap; the splice craft is in
  `skilyst/embed-video`.

## How to use this skill

1. Decide the shot's single intent → pick the shot size (see Rule 1).
2. Write the skeleton, then adapt to the target node's row in Rule 2.
3. If a character repeats, write the five-part lock (Rule 3); for dialogue shots,
   apply Rule 4 and then hand off to `skilyst/lipsync-audio-refs`.
4. Verify the field names and value ranges against that node's own
   `input_schema` (`GET /api/v1/nodes/<node_id>`) before submitting — prompt
   craft decides the content, `input_schema` decides whether the job is accepted.

Deeper material (keyword slots by category, the 21-source formula comparison,
category templates) is in the lane corpus referenced above.
