# Blazer Electric · AI Order Desk (`blazer-ai-order-app`)

Next.js (App Router) + TypeScript + Tailwind ordering app for contractors. It is hosted on Vercel and embedded in the Billtrust storefront (`buyblazer.com`) with an iframe.

- **One order workspace:** a shared composer accepts material lists, questions and clarification replies. Straightforward lists use deterministic parsing without an AI call; conversational requests use Gemini with the current order as context.
- **Reviewable changes:** assistant proposals distinguish new items from updates to existing lines. Customers select and apply proposals explicitly; applied proposals stay marked after refresh. Removed target lines are never silently recreated.
- **Active order:** filter items needing attention, ask about a specific numbered line, edit quantities and cut notes, or undo the last removal. Order data, conversation and shipping notes survive refresh when browser storage is available.
- **Cart or copy:** a connected Billtrust bridge enables cart submission. Standalone pages offer **Copy for QuickPad** instead.

## How it works

```
blazer_web_catalog.json ──(scripts/sync-catalog.mjs)──► data/ ──► lib/catalog/server.ts
                                                                    │  in-memory index (~2.5 s at boot)
   /api/search  /api/parse  /api/products/[sku]  /api/chat  /api/summary  /api/health
                                   ▲
 Browser (zustand order store) ────┘      iframe ──postMessage──► public/billtrust-bridge.js (buyblazer.com)
```

| Path | Purpose |
| --- | --- |
| `lib/search/` | Normalization (contractor shorthand, synonyms, fractions, gauges), typo-tolerant scoring with `rank` popularity, and confidence rules |
| `lib/parser/orderParser.ts` | Splits pasted text into lines. Handles `500'`, `20 sticks`, `x 3`, `(25)`, `a roll of`, `two boxes of` and `5 cuts @ 50ft`. Wire gauges and trade sizes are not read as quantities |
| `lib/order/conversation.ts` | Routes straightforward material lists to parsing and questions or clarification replies to chat |
| `lib/ai/gemini.ts` | Structured-output chat and shipping summary. The server key stays server-side |
| `lib/billtrust/` | postMessage protocol, payload sanitizing and the iframe client |
| `store/orderStore.ts` | Active order, chat history and shipping notes, persisted to `localStorage` with an in-memory fallback |
| `components/` | `OrderDesk` shell, `PasteOrderPanel`, `ChatAssistantPanel`, `OrderPanel`, `BomLineCard`, `AlternativeSelect`, `ProductSearchBox`, `AccessoryRail`, `SubmitPanel` |
| `public/billtrust-bridge.js` | Script you install on the Billtrust site |

The catalog stays on the server. Phones never download the 7 MB JSON; they call the search and parse APIs instead.

Pasted lines with no quantity, no numbers and no catalog match (for example `Smith residence - rough in`) are treated as job headings. They are listed under the BOM as "Skipped", and each one offers **Add to order notes** and **Add as item**. Every matched line, including green ones, has a **Change** button that opens the alternatives list and a catalog search.

Conversational lists also separate quantities joined by "and" and preserve wire footage, grouped fitting quantities, and requested package counts. Inline revisions such as "or actually ... wait no ..." stay unmatched until the final cable specification is confirmed. Within each numbered quote, nearby same-size items can suggest adapter material, and a uniquely named panel family can carry over to breaker requests. Inferred specifications remain review-required; explicit Homeline, breaker pole/rating, conductor, and Myers requirements are not silently substituted. Catalog records do not establish stock availability or utility approval.

Comma-separated THHN color lists repeat footage or spool length for each color while retaining gauge and stranding. Paired connectors/couplings can carry a size/style and an explicit "25 each" quantity; box/jar/bag quantities remain packages, and catalog piece counts sold by the box are converted to whole boxes and require review. Ordinary MC does not silently select lighting-control or PVC-coated MC. Cover cutouts, non-fusible outdoor disconnect ratings, and requested grounding provisions are checked separately. A suffixed part-number candidate is not reported as an exact match. Starter/overload assemblies and unspecified phasing-tape assortments remain on hold for counter verification.

Footage quantities are converted to catalog package counts before QuickPad submission when the stock length is known (for example, 100 ft of 10-ft EMT becomes 10 sticks). EMT stock length defaults to 10 ft only when the catalog omits it; those lines are marked for review. Non-exact package quantities are rounded up and the extra footage is shown for confirmation.

### Trade slang dictionary (`config/trade_slang.csv`)

Counter staff can teach the app shop talk without a code change. Each row maps slang to catalog wording:

| Column | Meaning |
| --- | --- |
| `slang` | One or more terms separated by `\|`. Matching is whole-word, case-insensitive and plural-tolerant: `colorado jim` also matches `Colorado Jims` and `colorado-jim` |
| `search_as` | Catalog wording that replaces the slang before searching, e.g. `plastic push in connector` |
| `skus` | Optional Blazer SKUs separated by `\|`. They are pinned to the top, and the first one is the default. A size in the request, such as `3/4 asshole`, can still select a later SKU |
| `notes` | Shown on the BOM line ("Trade term … → …") and given to the AI assistant as a glossary |

Longer terms win, so `sealtite connector` beats `sealtite`. The file is read once at server start; restart the dev server or redeploy after you edit it. Only pin SKUs you have verified.

## Local development

```bash
cd blazer-ai-order-app
npm install
# Set GEMINI_API_KEY in .env.local to enable the AI assistant.
npm run dev                    # predev copies ../blazer_web_catalog.json into data/
```

| Command | What it does |
| --- | --- |
| `npm run sync-catalog` | Re-copy the catalog after `export_web_catalog.py` runs |
| `npm test` | Vitest unit tests (search engine, parser, Billtrust protocol) |
| `npm run typecheck` / `npm run lint` | Static checks |
| `npm run build` | Production build (prebuild syncs the catalog) |

Material-list ordering and search work without a Gemini key. The shared workspace reports when conversation is unavailable, retains failed messages for retry, and leaves the active order untouched. Conversation includes up to 100 current lines; longer orders should be clarified in smaller groups. AI response quality and catalog compatibility still require verification; combining the input does not certify substitutions.

Receptacle requests require explicitly listed GFCI/AFCI protection and requested TR/WR features. Ordinary NM-B conductor configurations cannot select extra-neutral or MC cable, and rigid-metal elbow requests reject PVC, EMT and different trade sizes. These requirements also filter related alternatives. AI-supplied SKUs do not override the catalog matcher or automatically earn high confidence.

## Deploying to Vercel

1. Import the repository and set **Root Directory** to `blazer-ai-order-app`.
2. Keep **Include files outside the root directory in the Build Step** enabled. `prebuild` copies `../blazer_web_catalog.json` from the repo root.
3. Set these environment variables:

   | Variable | Required | Purpose |
   | --- | --- | --- |
   | `GEMINI_API_KEY` | yes | AI chat and summaries |
   | `NEXT_PUBLIC_BILLTRUST_ORIGINS` | — | Parent pages allowed to talk to the app. Default: `https://buyblazer.com,https://www.buyblazer.com` |
   | `FRAME_ANCESTORS` | — | CSP `frame-ancestors`. Default: `'self' https://buyblazer.com https://*.buyblazer.com` |
   | `GEMINI_MODEL` | — | Model override. Default: `gemini-3.1-flash-lite` |

4. Re-deploy after every catalog refresh so the new `blazer_web_catalog.json` ships.

For the existing CLI-linked project (which is not Git-connected), deploy from this app directory with `npm run sync-catalog` followed by `npx vercel --prod --yes`. The local `data/` catalog must exist before upload; `.vercelignore` excludes environment files. Configure secrets in Vercel, not in uploaded files. Pushing a Git branch alone does not deploy this project.

## Embedding in Billtrust (HTML editor)

Paste this on the page that hosts the order desk:

```html
<iframe id="blazer-order-app" data-blazer-order-app
        src="https://YOUR-APP.vercel.app/"
        title="Blazer Electric AI Order Desk"
        style="width:100%;min-height:640px;border:0"
        allow="clipboard-write"></iframe>
<script>
  // Optional overrides; see DEFAULTS in billtrust-bridge.js
  window.BLAZER_BRIDGE_CONFIG = {
    quickPad: { pageUrl: "/quickpad", autoSubmit: false }
  };
</script>
<script src="https://YOUR-APP.vercel.app/billtrust-bridge.js"
        data-app-origin="https://YOUR-APP.vercel.app" defer></script>
```

Also load `billtrust-bridge.js` on the **QuickPad** and **cart** pages, for example in a site-wide footer. When QuickPad isn't on the current page, the bridge saves the order in `sessionStorage` and redirects to `quickPad.pageUrl`. On the cart page it fills each line comment and appends a `[Blazer AI Order]` block to the shipping instructions. Re-submitting replaces that block instead of duplicating it.

How a submission flows:

1. The app sends `SUBMIT_ORDER` to the exact parent origin, never `"*"`. Duplicate SKUs are merged and comments are joined with ` | `.
2. The bridge only accepts messages that come from an allowlisted origin **and** from the order-app iframe window. It re-checks SKU format, quantities and lengths.
3. Fields are set through the native value setter, followed by `input`/`change` events, so Billtrust's bindings notice the change. No HTML is ever injected. QuickPad is not submitted for the user unless `autoSubmit: true`.
4. If the bridge isn't detected, **Copy for QuickPad** opens a dialog with copy-ready lines (`SKU,QTY`), line comments and shipping text.

> ⚠️ **Verify on the live site before go-live.** The Billtrust DOM selectors (`quickPad.inputSelectors`, `cart.rowSelectors`, `cart.rowSkuSelectors`, `cart.rowCommentSelectors`, `shipping.selectors`) and the QuickPad line format (`lineTemplate`, default `{sku},{qty}`) are best guesses. Inspect buyblazer.com and override them in `BLAZER_BRIDGE_CONFIG`. If the line format changes, also update `formatQuickPad` in `lib/billtrust/protocol.ts`. Use `debug: true` to log bridge activity.
