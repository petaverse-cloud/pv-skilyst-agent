## I did not submit — and this one I can't rehearse

**Nothing was sent to the platform. No job id exists. No money was spent.**

The request reads as "dry-run," but the only action it names is a real submission: `beehive_submit_job` is the live path, there is no dry-run flag on it, and a successful call would queue a paid `minimax-h3` render. A rehearsal that submits isn't a rehearsal. So I stopped before the money step and did the two things that are actually free: check the anchor, and write the manifest you'd send.

### The anchor fails verification

```
url:          https://example.test/still.png
reachable:    false
status:       null
detail:       unreachable: _ssl.c:1011: The handshake operation timed out
```

`example.test` is a reserved name (RFC 2606) — it cannot resolve. The failure is the check doing its job, not a network blip.

### The exact node config that would be sent

```json
{
  "node_id": "generate:minimax-h3",
  "input": {
    "prompt": "she lifts her head and settles",
    "images": ["https://example.test/still.png"],
    "image_roles": ["first_frame"],
    "duration": 6,
    "resolution": "768P",
    "ratio": "9:16"
  }
}
```

Saved to `dryrun/embed-video-minimax-h3.config.json`. Notes on the shape: every key is declared by the binding, so nothing would be refused as undeclared; `duration`/`resolution`/`ratio` happen to equal the skill's declared plan, so they're passed explicitly rather than falling back. Mode is unambiguous — one image, one `first_frame`, no reference role anywhere, so embed-video Rule 1's frame/reference mix (the failure that killed 17 jobs in the platform's 30-day distribution) does not apply here.

### Three things block a real submission

1. **The still isn't real.** Rule 2: the first frame carries identity, so the still must exist and be approved *before* the animation step. `example.test/still.png` is a placeholder, not a locked frame. Point me at a real, approved still and this becomes submittable.
2. **The prompt is underweight for I2V.** Rule 3 wants 15–40 words of pure *change* — camera move + subject motion + environmental micro-motion — and always an explicit end state so the tail doesn't drift where a splice lands. `"she lifts her head and settles"` is 6 words with no camera move. It does gesture at an end state ("settles"), which is the right instinct. A shape closer to the craft: *"she lifts her head slowly toward camera, a strand of hair shifting, breath visible; background candle flame flickers — then settles back into place, one continuous shot."*
3. **No doctor preflight this session.** Doctor is the first command before a paid job; it confirms credentials, node registration for `generate:minimax-h3`, and scope. I'd run it before the first real submission.

Say the word and I'll run the doctor preflight and draft the corrected prompt — then, with a real approved still in hand, submit for real.