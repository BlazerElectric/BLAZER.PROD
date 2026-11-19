<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Order Desk Maintenance

- Read [README.md](README.md) for workflow, team acceptance tests, limitations and the production deployment procedure.
- Keep one shared composer: `PasteOrderPanel` routes via `lib/order/conversation.ts`; `ChatAssistantPanel` renders history and proposals. Clarifications target existing line IDs and require explicit application. Preserve applied-proposal tracking and never recreate a removed target as a new item.
- Preserve the storage hydration guard. Startup bridge updates must not overwrite saved lines before restoration; keep the `tests/orderStore.test.ts` regression passing.
- The catalog engine is cached on `globalThis` for the process lifetime. Restart the server after matcher, normalization, slang or catalog changes before interpreting browser results; hot reload can retain the old engine.
- Keep compatibility requirements ahead of popularity and apply them to related alternatives too. AI-supplied SKUs must not bypass `SearchEngine.match` or automatically earn high confidence.
- Git push does not deploy the current CLI-linked Vercel project. Follow the README after deployment is authorized; never upload environment files. Distinguish mocked assistant checks from real Gemini checks, and do not submit a live Billtrust cart without explicit authorization.
