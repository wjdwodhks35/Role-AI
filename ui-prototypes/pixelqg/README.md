# Role-AI — Isometric Agent Control Center (UI prototype)

## Status
Standalone static demo on the **feature/pixelqg-agent-ui** branch. **Not connected to production Role-AI** and **not merged into dev**.

This layout implements the selected mockup:
- Isometric six-room agent office concept (CSS-drawn, no copied PixelQG source or artwork).
- Clickable Agent characters, with a **right-side detail inspector** showing selected agent role, task, stage, model, tools, token metrics, CPU/GPU/memory demo bars and agent activity tabs.
- Top project phase tracker.
- **Four bottom panels in the approved order:** 전체 진행 현황 → 주요 지표 → 실시간 로그 → 승인 대기.
- Responsive desktop/mobile layout, keyboard-focusable controls, local static assets only.

## Preview
Open `ui-prototypes/pixelqg/index.html` in a browser (no build, server, or external dependency required).

## Interaction
- Click or keyboard-activate any agent character to update the right-side panel.
- Switch the inspector tabs: 작업 정보 / 성능 지표 / 실행 로그.
- Filter log events by 전체 / 오류.
- Click demo approval buttons to mark a sample approval; **no actual permissions or requests are sent**.
- Click 기본 선택 to reset the inspector.

## Important limitations
- This is an **approximation** of the approved high-detail isometric concept using CSS shapes and text glyphs. It does **not** reproduce the illustrated image pixel-for-pixel. Creating equivalent character sprites and room tiles requires a separate, licensed image asset implementation.
- All displayed agents, progress, tokens, CPU/GPU/memory, times, and logs are **hardcoded demo data**, not measured values. Some displayed sample statistics are intentionally illustrative and should not be interpreted as live metrics.
- No network requests, model calls, agent execution, filesystem writes, authentication/DB/API changes, or deployment.
- The local approval controls change **only in-memory demo UI state**.

## Next step after user review
1. Replace CSS office with original licensed pixel-art/isometric sprite and tile assets.
2. Add read-only authenticated status endpoints, explicit authorization and measured metrics.
3. Bind approval controls only after a separate permission/security review and explicit approval.
4. Browser/mobile/accessibility regression tests and security review before merging.

Inspiration only: https://github.com/hugobertagna/pixelqg-app

No upstream source code or art assets were copied.
