# Role-AI Agent Studio — PixelQG-inspired UI prototype

**Status:** isolated static UI concept; not integrated into the Role-AI application.

## Purpose
- Pixel-art-inspired agent work rooms (code, research, test, lounge)
- Per-agent status, model, task, input/output tokens, and elapsed-time details
- Agent list filtering, activity timeline, and mobile-responsive layout
- Korean UI, keyboard-operable controls, no remote assets or external dependencies

## Preview
Open `index.html` in a local browser. No build step or API server required.

## Safety and data
All agent names, token counts, statuses, and timestamps are **hardcoded demo fixtures** in `app.js`.
No live metrics, API requests, authentication changes, execution controls, or data writes.
This prototype does not change `role_ai/static/`, FastAPI routes, Docker, SQLite, or Cloudflare settings.

## Future integration checklist
1. Design a read-only authenticated agent-status endpoint with per-user authorization.
2. Replace demo fixtures with sanitized, rate-limited status polling or events.
3. Separate measured token counts from estimated/unsupported counters.
4. Stop updates on logout; protect session/CSRF as appropriate for chosen transport.
5. Add accessibility, browser and mobile tests before exposing the view.
6. Review PixelQG attribution/license requirements before reusing any upstream code/assets.

Reference: https://github.com/hugobertagna/pixelqg-app (visual concept only; no upstream source or assets copied).

This is a UI branch only. Do not merge into `dev` until explicitly reviewed and approved.
