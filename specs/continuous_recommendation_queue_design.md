# Feature: Continuous Recommendation Queue

## Requirements (EARS Format)

- When a user plays one song with `queue_more` omitted or true, the system shall add
  related YouTube Music radio tracks behind it.
- While `queue_more` is enabled, when playback approaches the end of the queue, the
  system shall append another deduplicated recommendation batch.
- When a user disables `queue_more`, the system shall preserve the existing queue and
  stop adding recommendations.
- If recommendation lookup fails, the system shall continue playing the requested
  track and leave the queue usable.

## Architecture

- Frontend/client: expose `queue_more` on `play_media` and a `set_queue_more` MCP tool.
  The HTTP play request accepts both Python-style `queue_more` and JSON-style
  `queueMore` names.
- Backend: use the YouTube Music watch-radio feed, append a bounded batch at startup,
  and replenish when only a few unplayed items remain. Filter IDs already in the
  queue before appending.
- Security: Pydantic validates the boolean request; the recommendation limit is a
  server constant rather than caller-controlled; no credentials or upstream error
  details are returned. Existing deployment authentication/network guidance applies.

## Implementation Plan

- [x] Add request/state schemas and queue-more control endpoint.
- [x] Add normalized YouTube Music radio recommendation lookup.
- [x] Replenish the queue during play and track advancement.
- [x] Expose MCP controls and document behavior.
- [x] Add unit and integration tests for defaults, opt-out, deduplication, and control.
