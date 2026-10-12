# BLAZER.PROD

## Accessory recommendation workflow

Before generating recommendations, preview the deterministic candidate pools:

```sh
python generate_accessories.py --dry-run
```

The preview does not call Gemini or write files. It reports how many products
have eligible candidates and which items appear in the most candidate pools.
Candidate pools use explicit target/accessory category relationships; products
with no matching relationship are left without candidates rather than falling
back to every product in the same broad category.

Conduit and weatherhead targets are additionally filtered by trade size: a
candidate with a different inch size is excluded before the model sees it
(candidates with no parseable size, such as duct seal, are kept).

Taxonomy rules live in `accessory_rules.csv` (edit it, no code changes needed).
Each row maps a `Main Category > Subcategory 1` node (from `product_taxonomy.csv`,
produced by `map_taxonomy.py` against `CATEGORY.csv`) to an accessory node, with
`same_brand`/`same_color` (yes/no; brand is only used where parts are brand-specific, such as tool batteries, and color only for device/wall plate pairs) and `size_match` (none/loose/strict) filters.
Rows with notes `coverage` were added to fill gaps. `TAXONOMY_OVERRIDES` in the script re-files blades/bits that the mapper left under tool nodes. Large rules rotate among the top 5x-12 sellers per target (`_spread`) so the same products are not suggested everywhere.

Optional columns: `match` (none/series/gauge/platform/amps/thread/box), and
`target_keywords`/`accessory_keywords` (case-insensitive regexes on the product
text).

`BLAZER.RANK.csv` (PROD_ID, PRODUCT, orders in the past 6 months) filters and
ranks candidates: products with fewer than 2 orders or "obsolete" in the name
are never suggested, each rule offers at most its 12 most-ordered matches, and
capped pools keep the most-ordered first. Products with fewer than 5 rule-based
candidates are topped up to 8 from their own taxonomy node (same size/brand and
most-ordered first); the model still decides what is actually complementary.

The default checkpoint is versioned (`product_accessories.v3.checkpoint.csv`)
so rows generated under the previous broad-fallback rules are not silently
reused. Review the preview before running without `--dry-run`. Gemini is called
only for products that have eligible candidates and are not already in that
versioned checkpoint; other products receive an empty recommendation list.
### Recent rule behaviour

- The single best seller of each large rule is always kept (`TOP_ANCHORS`); the rest rotate.
- Cross-sell wire connectors are limited to IDE/3M wire nuts (WAGO/lever/push-in are not suggested).
- The `box` match mode pairs boxes with covers/rings by box kind (square, non-metallic, etc.); `gauge` also parses NM/MC cable gauge.
- Device rules are type-specific: receptacles/switches get only 1-gang boxes and same-colour 1-gang plates of the matching type (duplex vs Decora), and plugs by amp rating.

### Testing with a small Gemini sample

```sh
python generate_accessories.py --sample 50
```

Calls Gemini for 50 random products that have candidates and writes
`product_accessories.sample.csv` with a separate checkpoint, so the full-run
checkpoint is untouched.

### Job kits

`job_kits.csv` describes install jobs (EV charger, service upgrade, rough-in,
motor circuit, security camera, van stock, ...). Columns: `job`, `slot`, `path`
(taxonomy prefix, optional), `keywords` (regex on product text), `max` (candidates
per slot, default 3). A product that matches a slot gets top sellers from the
job's *other* slots as extra candidates, and Gemini is told the job names
(`install_jobs`) so it favours items needed for the same job.

Kits only top up products with fewer than 12 rule-based candidates, rotate
the top 8 per slot by product ID so the same best sellers are not everywhere,
and never add wallplates (colour matching stays with the device rules).
Add jobs or slots by editing the CSV; no code changes are needed.

### Fallbacks and thin pools

- Rows tagged `thin` in `accessory_rules.csv` give extra, looser candidates for
  categories that otherwise end up with very few (bits, hand tools, breakers).
- Products still under 5 candidates are topped up from sibling subcategories of
  the same main category, but only with the same brand or a shared keyword.

### Checking pools without Gemini

`python check_pools.py [--csv issues.csv]` builds every pool and reports target/candidate
pairs that conflict (gang count, colour, trade size), ranked by target and candidate node,
plus candidates that appear in more than 5% of pools. Gang and colour conflicts between
wiring devices and boxes are removed automatically (`hard_conflict` in
`generate_accessories.py`), so those counts should stay at zero. Run it after editing rules.
