# Desktop acceptance evidence — 2026-09-13

Live run of the `orchestrator/desktop/AGENTS.md` sequence on the **primary
`pnpm dev:all` stack** (Temporal :7233, API :8787, Next :3000, container
`gwen-desktop` image `c0bf84be7351`, noVNC :6080). Model `openai/gpt-6-astra`
via Perplexity Agent API, selected in the live picker. Fixture served from the
host at `http://host.lima.internal:8798/index.html` (random canvas code,
never read from DOM/source).

Workflow/session ID throughout: `chat-36305ecd7e99feb5`.

| # | Step | Observed |
|---|------|----------|
| 1 | Literal browser task submitted through the real composer | `init_session`, `navigate`, `click` executed on `desktop-browser`; noVNC iframe `http://localhost:6080/vnc.html?...view_only=true` embedded in `WebPreviewBody`; three 1440×900 screenshots decoded in the Task timeline (`01-agent-working.png`) |
| 2 | Visual-only answer | Model reported `VISION-681004`; the physical display showed the same code (`02-desktop-after-click.png`, captured from the X display, not the UI) |
| 3 | Take Control | `runtime.json` mode `agent → human`; x11vnc `viewonly:0,deny:0,client_count:1`; iframe switched to `view_only=false` |
| 4 | Human edit via noVNC keyboard | Note field changed to `HUMAN-UPDATED` with real key events through the viewer (`03-human-note-crop.png`) |
| 5 | Relinquish | x11vnc `viewonly:1,deny:0,pointer_mask:0x0`; mode `instructions`; steering PromptInput appeared |
| 6 | Steering | Run ID `a75e8f38-…` → `ff067d33-…`, Workflow ID unchanged; fresh `take_screenshot` (operation 2 of the new run); model confirmed `HUMAN-UPDATED` **and** retained `VISION-681004`; UI reached "Chat idle" (`04-browser-path-complete.png`) |
| 7 | Computer-use path, outside the browser | Mousepad opened with fresh `EDITOR-506656`; agent used only `take_screenshot`/`click`/`press_key`/`type`: 6 actions, appended `DESKTOP-INPUT-VERIFIED`, verified by screenshot, all 10 timeline actions `output-available` (`05-editor-open.png`, `06-editor-after-agent-crop.png`, `07-editor-path-complete.png`) |
| 8 | Held input + repeated handoff | Second Take Control → mouse button held through noVNC (`pointer_mask:0x100`) → Relinquish cleared it (`pointer_mask:0x0`, `viewonly:1`) |
| 9 | Worker/container loss | `docker stop gwen-desktop` while owned: `/health` `desktop:false`; `desktop-control` GET returned 503 (recovery); `concurrently --kill-others` shut the whole stack down (documented behaviour) |
| 10 | Cold restart with stale owner | `pnpm dev:all` rebuilt runtime: `Reclaimed desktop from closed workflow 'chat-36305ecd7e99feb5' (status=NOT_FOUND)` → `runtime.json` `owner:null, mode:agent`; `/health` `desktop:true` |

Not covered by this run: stale-client reconnect during human mode (only the
QA viewer was connected), GCE deployment, multi-user isolation.
