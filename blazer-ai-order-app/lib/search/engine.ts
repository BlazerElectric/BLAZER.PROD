import type { CatalogProduct, MatchResult, ProductSummary, SearchHit } from "../types";
import { boundedEditDistance, maxTypoDistance } from "./fuzzy";
import { compactCode, isSpecToken, tokenize } from "./normalize";
import { MATERIAL_TOKENS, type QuoteMaterialContext } from "./quoteContext";
import { SlangDictionary, type SlangEntry, type SlangRewrite } from "./slang";

const FIELD_WEIGHTS = {
  mpn: 5,
  title: 3,
  specs: 2,
  keywords: 2,
  brand: 1.5,
  misspellings: 1.5,
  category: 0.75,
  prefix: 0.5,
} as const;

/** Field weight at which a token counts as a full-strength match. */
const FULL_WEIGHT = 3;
const PREFIX_FACTOR = 0.85;
const FUZZY_FACTOR = 0.75;
const SPEC_MISS_PENALTY = 0.55;
const UNKNOWN_TOKEN_IDF = 1;
const UNRANKED = 999999;
/** Protective/specialty variants that should not outrank the plain part unless requested. */
const SPECIALTY_TOKENS = new Set(["gfci", "afci", "df", "gfp", "epd"]);
const SPECIALTY_PENALTY = 0.85;
/** Slang entries with pinned SKUs: floor quality and a bonus that favors earlier-listed SKUs. */
const PIN_QUALITY = 0.9;
const PIN_BONUS = 0.3;
const PIN_STEP = 0.08;
/** Units that only make sense for wire, cable, tubing and other bulk goods. */
const BULK_UNITS = new Set(["roll", "coil", "spool", "reel", "ft", "stick"]);
const PIECE_CATEGORIES = new Set([
  "Fittings",
  "Connectors & Crimps",
  "Boxes & Enclosures",
  "Wiring Devices",
  "Power Distribution",
  "Fuses & Accessories",
  "Lighting Fixtures, Control",
  "Lamps, Bulbs & Ballast",
]);
const BULK_UNIT_PENALTY = 0.6;
const QUOTE_CONTEXT_BONUS = 0.22;
const WIRE_CATEGORY = "Wire, Cable & Cord";
const WIRE_SPEC_TOKENS = new Set(["thhn", "thwn", "xhhw", "mcm", "kcmil", "nmb"]);
const RECEPTACLE_CATEGORY = "Wiring Devices";
const RECEPTACLE_SUBCATEGORY = "Receptacles";
const FITTING_CATEGORIES = new Set(["Fittings", "Connectors & Crimps"]);

type ReceptacleForm = "single" | "duplex";
type AdapterGender = "male" | "female";

function receptacleForm(tokens: string[]): ReceptacleForm | undefined {
  if (tokens.includes("duplex") || tokens.includes("dup")) return "duplex";
  if (tokens.some((token) => token === "single" || token === "sgl" || token === "sngl")) return "single";
  return undefined;
}

function packageLength(product: ProductSummary): number | undefined {
  const match = `${product.title} ${product.specs}`.match(/\b([\d,]+)\s*(?:feet|foot|ft)\b/i);
  return match ? Number.parseInt(match[1].replace(/,/g, ""), 10) : undefined;
}

function adapterGender(tokens: string[]): AdapterGender | undefined {
  if (tokens.includes("maleadapter") || (tokens.includes("male") && tokens.includes("adapter"))) return "male";
  if (tokens.includes("femaleadapter") || (tokens.includes("female") && tokens.includes("adapter"))) return "female";
  return undefined;
}

export interface SearchOptions {
  limit?: number;
  /** Treat the final token as a prefix (type-ahead). */
  prefixLast?: boolean;
  /** Ordered unit from the request ("roll", "ft"), used to demote piece goods for bulk units. */
  unit?: string;
}

export interface MatchHints {
  unit?: string;
  materialContext?: QuoteMaterialContext;
}

export interface EngineOptions {
  slang?: SlangEntry[];
}

interface Posting {
  docs: Int32Array;
  weights: Float32Array;
}

export function toSummary(p: CatalogProduct): ProductSummary {
  return {
    sku: p.sku,
    title: p.title,
    brand: p.brand,
    mpn: p.mpn,
    category: p.category,
    subcategory: p.subcategory,
    specs: p.specs,
    rank: p.rank,
  };
}

function popularity(rank: number): number {
  if (!rank || rank >= UNRANKED) return 0;
  return Math.max(0, 1 - Math.log10(rank) / 6);
}

/**
 * In-memory catalog search: weighted inverted index with IDF scoring, synonym/shorthand
 * normalization, prefix and typo tolerance, exact SKU/MPN lookups, and popularity (rank) boosting.
 */
export class SearchEngine {
  readonly size: number;
  private readonly docs: CatalogProduct[];
  private readonly summaries: ProductSummary[];
  private readonly bySku = new Map<string, number>();
  private readonly byMpn = new Map<string, number[]>();
  private readonly postings = new Map<string, Posting>();
  private readonly specialty: Uint8Array;
  private readonly sortedVocab: string[];
  private readonly vocabByLength = new Map<number, string[]>();
  private readonly pieceGoods: Uint8Array;
  readonly slang: SlangDictionary;

  constructor(products: CatalogProduct[] | Record<string, CatalogProduct>, options: EngineOptions = {}) {
    this.slang = new SlangDictionary(options.slang ?? []);
    this.docs = Array.isArray(products) ? products : Object.values(products);
    this.size = this.docs.length;
    this.summaries = this.docs.map(toSummary);
    this.specialty = new Uint8Array(this.size);
    this.pieceGoods = new Uint8Array(this.size);

    const building = new Map<string, Map<number, number>>();
    const add = (term: string, doc: number, weight: number) => {
      let m = building.get(term);
      if (!m) building.set(term, (m = new Map()));
      const prev = m.get(doc) ?? 0;
      if (weight > prev) m.set(doc, weight);
    };
    const addText = (text: string, doc: number, weight: number) => {
      if (!text) return;
      for (const t of tokenize(text)) add(t, doc, weight);
    };

    this.docs.forEach((p, i) => {
      this.bySku.set(p.sku.toLowerCase(), i);
      if (PIECE_CATEGORIES.has(p.category)) this.pieceGoods[i] = 1;
      const mpn = p.mpn && p.mpn.toUpperCase() !== "N/A" ? p.mpn : "";
      if (mpn) {
        const code = compactCode(mpn);
        if (code) {
          const list = this.byMpn.get(code) ?? [];
          list.push(i);
          this.byMpn.set(code, list);
          add(code, i, FIELD_WEIGHTS.mpn);
        }
        addText(mpn, i, FIELD_WEIGHTS.mpn);
        // "QO120GFI" -> "qo 120 gfi" so partial/spaced part numbers and suffixes still match.
        addText(mpn.replace(/([A-Za-z])(?=\d)|(\d)(?=[A-Za-z])/g, "$1$2 "), i, FIELD_WEIGHTS.specs);
      }
      add(p.sku.toLowerCase(), i, FIELD_WEIGHTS.mpn);

      const title = p.title ?? "";
      if (tokenize(`${title} ${mpn}`).some((t) => SPECIALTY_TOKENS.has(t))) this.specialty[i] = 1;
      const firstSpace = title.indexOf(" ");
      const first = firstSpace > 0 ? title.slice(0, firstSpace) : "";
      if (/^[A-Z&]{2,4}$/.test(first)) {
        addText(first, i, FIELD_WEIGHTS.prefix);
        addText(title.slice(firstSpace + 1), i, FIELD_WEIGHTS.title);
      } else {
        addText(title, i, FIELD_WEIGHTS.title);
      }
      addText(p.specs, i, FIELD_WEIGHTS.specs);
      addText(p.brand, i, FIELD_WEIGHTS.brand);
      addText(p.category, i, FIELD_WEIGHTS.category);
      addText(p.subcategory, i, FIELD_WEIGHTS.category);
      for (const kw of p.search_keywords ?? []) addText(kw, i, FIELD_WEIGHTS.keywords);
      for (const ms of p.common_misspellings ?? []) addText(ms, i, FIELD_WEIGHTS.misspellings);
    });

    for (const [term, m] of building) {
      const docs = new Int32Array(m.size);
      const weights = new Float32Array(m.size);
      let k = 0;
      for (const [doc, w] of m) {
        docs[k] = doc;
        weights[k] = w;
        k++;
      }
      this.postings.set(term, { docs, weights });
      const bucket = this.vocabByLength.get(term.length) ?? [];
      bucket.push(term);
      this.vocabByLength.set(term.length, bucket);
    }
    this.sortedVocab = [...this.postings.keys()].sort();
  }

  get(sku: string): CatalogProduct | undefined {
    const i = this.bySku.get(sku.toLowerCase());
    return i === undefined ? undefined : this.docs[i];
  }

  summary(sku: string): ProductSummary | undefined {
    const i = this.bySku.get(sku.toLowerCase());
    return i === undefined ? undefined : this.summaries[i];
  }

  summariesFor(skus: string[]): ProductSummary[] {
    return skus.map((s) => this.summary(s)).filter((p): p is ProductSummary => Boolean(p));
  }

  private idf(term: string): number {
    const p = this.postings.get(term);
    if (!p) return 0;
    return Math.log(1 + this.size / p.docs.length);
  }

  private prefixTerms(prefix: string, limit = 40): string[] {
    let lo = 0;
    let hi = this.sortedVocab.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.sortedVocab[mid] < prefix) lo = mid + 1;
      else hi = mid;
    }
    const out: string[] = [];
    for (let i = lo; i < this.sortedVocab.length && out.length < limit; i++) {
      const term = this.sortedVocab[i];
      if (!term.startsWith(prefix)) break;
      if (term !== prefix) out.push(term);
    }
    return out;
  }

  private fuzzyTerms(token: string, limit = 12): Array<[string, number]> {
    const maxDist = maxTypoDistance(token);
    if (maxDist === 0) return [];
    const found: Array<[string, number]> = [];
    for (let len = token.length - maxDist; len <= token.length + maxDist; len++) {
      for (const term of this.vocabByLength.get(len) ?? []) {
        if (term[0] !== token[0] && maxDist < 2) continue;
        const d = boundedEditDistance(token, term, maxDist);
        if (d <= maxDist) found.push([term, d]);
      }
    }
    found.sort((a, b) => a[1] - b[1] || this.idf(a[0]) - this.idf(b[0]));
    return found.slice(0, limit).map(([t, d]) => [t, d === 1 ? FUZZY_FACTOR : FUZZY_FACTOR - 0.15]);
  }

  /** Expand a query token into [indexTerm, matchFactor] alternatives. */
  private expand(token: string, prefix: boolean): Array<[string, number]> {
    const alts: Array<[string, number]> = [];
    const exists = this.postings.has(token);
    if (exists) alts.push([token, 1]);
    const alnum = /^[a-z0-9]+$/.test(token) && !/^\d+$/.test(token);
    const partNumberLike = /[a-z]/.test(token) && /\d/.test(token);
    if ((prefix || !exists || partNumberLike) && alnum && token.length >= 3) {
      for (const t of this.prefixTerms(token)) alts.push([t, PREFIX_FACTOR]);
    }
    if (!exists && alts.length === 0 && /^[a-z]+$/.test(token)) {
      alts.push(...this.fuzzyTerms(token));
    }
    return alts;
  }

  search(query: string, options: SearchOptions = {}): SearchHit[] {
    return this.run(query, options).hits;
  }

  private run(query: string, options: SearchOptions): { hits: SearchHit[]; rewrite: SlangRewrite; tokens: string[] } {
    const limit = options.limit ?? 10;
    const trimmed = query.trim();
    const rewrite = this.slang.rewrite(trimmed);
    if (!trimmed) return { hits: [], rewrite, tokens: [] };

    const exactDocs = new Set<number>();
    const code = compactCode(trimmed);
    const skuDoc = this.bySku.get(trimmed.toLowerCase());
    if (skuDoc !== undefined) exactDocs.add(skuDoc);
    for (const d of this.byMpn.get(code) ?? []) exactDocs.add(d);

    const tokens = [...new Set(tokenize(rewrite.query))];
    const N = this.size;
    const score = new Float64Array(N);
    const specMisses = new Uint8Array(N);
    const touched = new Set<number>(exactDocs);
    let totalIdf = 0;
    const specTokenCount = tokens.filter(isSpecToken).length;
    const specHits = new Uint8Array(N);
    const wantsSpecialty = tokens.some((t) => SPECIALTY_TOKENS.has(t));
    const requestedSpecialty = tokens.filter((token) => SPECIALTY_TOKENS.has(token));
    const wantsReceptacle = tokens.includes("receptacle");
    const wantsConnector = tokens.includes("connector");
    const wantsCoupling = tokens.includes("coupling");
    const wantsStrap = tokens.includes("strap");
    const wantsClip = tokens.includes("clip");
    const wantsHoleCount = tokens.includes("hole") || tokens.some((token) => /^\d+hole$/.test(token));
    const wantsMcAccessory =
      tokens.includes("mc") &&
      tokens.some((token) => token === "connector" || token === "coupling" || token === "strap" || token === "clip");
    const requestedReceptacleForm = receptacleForm(tokens);
    const requestedVoltage = tokens.some((token) => /^\d{2,3}v$/.test(token));
    const requestedAdapterGender = adapterGender(tokens);
    const wantsIndoor = tokens.some((token) => token === "indoor" || token === "interior");
    const wantsOutdoor = tokens.includes("outdoor");
    const pinRank = new Map<number, number>();
    rewrite.pinned.forEach((sku, k) => {
      const doc = this.bySku.get(sku.toLowerCase());
      if (doc !== undefined && !pinRank.has(doc)) {
        pinRank.set(doc, k);
        touched.add(doc);
      }
    });
    const bulkUnit = Boolean(options.unit && BULK_UNITS.has(options.unit));

    tokens.forEach((token, idx) => {
      const alts = this.expand(token, Boolean(options.prefixLast) && idx === tokens.length - 1);
      if (alts.length === 0) {
        totalIdf += UNKNOWN_TOKEN_IDF;
        return;
      }
      const tokenIdf = Math.max(...alts.map(([t]) => this.idf(t)));
      totalIdf += tokenIdf;
      const best = new Map<number, number>();
      for (const [term, factor] of alts) {
        const p = this.postings.get(term)!;
        for (let k = 0; k < p.docs.length; k++) {
          const v = Math.min(1, p.weights[k] / FULL_WEIGHT) * factor;
          const doc = p.docs[k];
          if (v > (best.get(doc) ?? 0)) best.set(doc, v);
        }
      }
      const spec = isSpecToken(token);
      for (const [doc, v] of best) {
        score[doc] += tokenIdf * v;
        touched.add(doc);
        if (spec) specHits[doc]++;
      }
    });

    if (totalIdf === 0 && exactDocs.size === 0 && pinRank.size === 0) return { hits: [], rewrite, tokens };

    const hits: SearchHit[] = [];
    for (const doc of touched) {
      const product = this.docs[doc];
      const productTokens = tokenize(`${product.title} ${product.specs}`);
      if (!exactDocs.has(doc)) {
        const text = `${product.title} ${product.mpn} ${product.specs}`;
        if (tokens.includes("mc") && tokens.some((token) => /^\d+\/[234]$/.test(token))) {
          if (product.category !== WIRE_CATEGORY || !/\bmc\b/i.test(text)) continue;
          if (!tokens.some((token) => ["pcs", "luminary", "hcf"].includes(token)) &&
              /\b(?:pcs|luminary|hcf)\b/i.test(text)) continue;
          if (!tokens.some((token) => ["pvc", "coated", "jacketed"].includes(token)) &&
              /\b(?:pvc coated|jacketed)\b/i.test(text)) continue;
          const cableSize = tokens.find((token) => /^\d+\/[234]$/.test(token));
          if (cableSize && !text.includes(cableSize)) continue;
        }
        if (tokens.includes("disconnect")) {
          if (!/\b(?:disconnect|safety switch)\b/i.test(text)) continue;
          if (tokens.includes("nonfusible") && !productTokens.includes("nonfusible")) continue;
          if (tokens.filter((token) => /^\d+a$|^[1-4]p$|^\d+v$|^3r$/.test(token)).some((token) => !productTokens.includes(token))) continue;
        }
        if (tokens.includes("cover") && tokens.includes("1toggle")) {
          if (!productTokens.includes("1toggle") || productTokens.includes("2toggle")) continue;
          if (/\b(?:duplex|receptacle)\b/i.test(text) || /\btog\s*&/i.test(text)) continue;
        }
        if (tokens.includes("cover") && tokens.includes("blank") && !productTokens.includes("blank")) continue;
        if (/\braised ground screws?\b/i.test(query) && !/\braised ground(?:ing)? screws?\b/i.test(text)) continue;
        if (tokens.includes("toggle") && !tokens.includes("cover")) {
          if (!/\b(?:toggle|switch)\b/i.test(text) || product.category !== RECEPTACLE_CATEGORY) continue;
          if (tokens.filter((token) => /^\d+a$|^[1-4]p$|^ivory$/.test(token)).some((token) => !productTokens.includes(token))) continue;
        }
        if (tokens.includes("homeline") && !/\bhom(?:eline|\d)/i.test(text)) continue;
        if (tokens.includes("myers") && !/\bmyers\b/i.test(text)) continue;
        if (tokens.includes("hub") && !/\bhubs?\b/i.test(text)) continue;
        if (tokens.includes("tapcon") && !/\btapcons?\b/i.test(text)) continue;
        if (tokens.some((token) => token === "surge" || token === "spd") && !/\b(?:surge|spd|surgebreaker)\b/i.test(text)) continue;
        if (tokens.includes("panel") && product.subcategory !== "Load Centers / Panelboards" && !tokens.includes("feedthru")) continue;
        if (tokens.includes("breaker") && !tokens.includes("panel") && product.subcategory !== "Circuit Breakers") continue;
        if (tokens.includes("meter") && tokens.includes("socket")) {
          if (!/\bmeter(?:ed)?\s+socket\b/i.test(text)) continue;
          if (tokens.includes("horn") && !/\bhorn bypass\b/i.test(text)) continue;
          if (tokens.includes("ringless") && !productTokens.includes("ringless")) continue;
        }
        if (tokens.includes("rigid") && tokens.includes("mast") && !tokens.includes("kit")) {
          if (product.category !== "Conduit, Strut & Raceways" || !productTokens.includes("rigid")) continue;
        }
        if (tokens.includes("rigid") && tokens.includes("conduit")) {
          if (product.subcategory !== "Conduit - Metallic" || !productTokens.includes("rigid")) continue;
          if (productTokens.some((token) => ["nipple", "coupling", "elbow"].includes(token))) continue;
          if (productTokens.includes("aluminum") && !tokens.includes("aluminum")) continue;
        }
        if (tokens.includes("groundrod") && !productTokens.includes("groundrod")) continue;
        if (tokens.includes("locknut") && !productTokens.includes("locknut")) continue;
        if (tokens.includes("bushing") && !productTokens.includes("bushing")) continue;
        if (tokens.includes("staples") && !/\bstaples?\b/i.test(text)) continue;
        if (tokens.includes("thhn")) {
          if (product.category !== WIRE_CATEGORY || !productTokens.includes("thhn")) continue;
          if (tokens.filter((token) => /^(?:\d+\/0|\d+)$/.test(token)).some((token) => !productTokens.includes(token))) continue;
          if (tokens.filter((token) => ["black", "white", "green"].includes(token)).some((token) => !productTokens.includes(token))) continue;
          if (tokens.includes("copper") && productTokens.includes("aluminum")) continue;
          if (tokens.includes("aluminum") && productTokens.includes("copper")) continue;
          if (tokens.includes("solid") && productTokens.includes("stranded")) continue;
          if (tokens.includes("stranded") && productTokens.includes("solid")) continue;
        }
      }
      if (wantsReceptacle && !exactDocs.has(doc) && (product.category !== RECEPTACLE_CATEGORY || product.subcategory !== RECEPTACLE_SUBCATEGORY)) {
        continue;
      }
      if (wantsMcAccessory && !exactDocs.has(doc)) {
        const mcCompatible =
          productTokens.includes("mc") ||
          product.subcategory.toLowerCase().includes("mc") ||
          /\barmored cable\b/i.test(product.specs);
        if (!mcCompatible) continue;
      }
      if (FITTING_CATEGORIES.has(product.category) && !exactDocs.has(doc)) {
        const isConnector = productTokens.includes("connector");
        const isCoupling = productTokens.includes("coupling");
        if (wantsConnector && isCoupling && !isConnector) continue;
        if (wantsCoupling && isConnector && !isCoupling) continue;
      }
      if (!exactDocs.has(doc)) {
        const isStrap = productTokens.includes("strap") || product.subcategory.toLowerCase().includes("strap");
        const isClip = productTokens.includes("clip") || product.subcategory.toLowerCase().includes("clip");
        if (wantsStrap && !isStrap) continue;
        if (wantsClip && !isClip) continue;
        if (wantsStrap && wantsHoleCount && !productTokens.includes("hole")) continue;
      }
      if (product.subcategory === "Circuit Breakers" && !exactDocs.has(doc)) {
        const ratings = tokens.filter((token) => /^\d+a$|^[1-4]p$/.test(token));
        if (ratings.some((token) => !productTokens.includes(token))) continue;
        if (!tokens.includes("tandem") && productTokens.includes("tandem")) continue;
        const candidateSpecialty = productTokens.filter((token) => SPECIALTY_TOKENS.has(token));
        if (
          (requestedSpecialty.length === 0 && candidateSpecialty.length > 0) ||
          requestedSpecialty.some((token) => !candidateSpecialty.includes(token))
        ) {
          continue;
        }
      }
      if (tokens.includes("feedthru") && !productTokens.includes("feedthru") && !exactDocs.has(doc)) continue;
      if (tokens.includes("cover") && !productTokens.includes("cover") && !exactDocs.has(doc)) continue;
      if (product.subcategory === "Load Centers / Panelboards") {
        const nema1 = productTokens.includes("nema") && productTokens.includes("1");
        const nema3r = productTokens.includes("nema") && productTokens.includes("3r");
        if (wantsIndoor && (productTokens.includes("outdoor") || nema3r) && !exactDocs.has(doc)) continue;
        if (wantsOutdoor && (productTokens.includes("indoor") || nema1) && !exactDocs.has(doc)) continue;
      }
      if (requestedReceptacleForm && product.category === RECEPTACLE_CATEGORY && product.subcategory === "Receptacles") {
        const candidateForm = receptacleForm(productTokens);
        if (candidateForm && candidateForm !== requestedReceptacleForm && !exactDocs.has(doc)) continue;
      }
      if (
        wantsReceptacle &&
        !requestedReceptacleForm &&
        !requestedVoltage &&
        product.category === RECEPTACLE_CATEGORY &&
        product.subcategory === RECEPTACLE_SUBCATEGORY &&
        receptacleForm(productTokens) === "single" &&
        !exactDocs.has(doc)
      ) {
        continue;
      }
      if (requestedAdapterGender && product.category === "Fittings") {
        const candidateGender = adapterGender(productTokens);
        if (candidateGender && candidateGender !== requestedAdapterGender && !exactDocs.has(doc)) continue;
      }
      if (
        bulkUnit &&
        tokens.some((token) => WIRE_SPEC_TOKENS.has(token)) &&
        product.category !== WIRE_CATEGORY &&
        !exactDocs.has(doc)
      ) {
        continue;
      }
      specMisses[doc] = specTokenCount - specHits[doc];
      const specFactor = Math.pow(SPEC_MISS_PENALTY, specMisses[doc]);
      let quality = totalIdf > 0 ? score[doc] / totalIdf : 0;
      quality *= specFactor;
      if (!wantsSpecialty && this.specialty[doc]) quality *= SPECIALTY_PENALTY;
      const pin = pinRank.get(doc);
      let bonus = 0;
      if (pin !== undefined) {
        quality = Math.max(quality, PIN_QUALITY * specFactor);
        bonus = Math.max(0.05, PIN_BONUS - PIN_STEP * pin) * specFactor;
      }
      if (bulkUnit && this.pieceGoods[doc]) quality *= BULK_UNIT_PENALTY;
      const exact = exactDocs.has(doc);
      if (exact) quality = 1;
      const p = this.summaries[doc];
      hits.push({
        product: p,
        quality,
        score: quality * (0.88 + 0.12 * popularity(p.rank)) + bonus + (exact ? 1 : 0),
        exact,
      });
    }
    hits.sort((a, b) => b.score - a.score || a.product.rank - b.product.rank);
    return { hits: hits.slice(0, limit), rewrite, tokens };
  }

  /** Resolve a free-text request to a product with a confidence grade and alternatives. */
  match(query: string, hints: MatchHints = {}): MatchResult {
    const { hits: searchHits, rewrite, tokens } = this.run(query, { limit: 12, unit: hints.unit });
    const adapterRequest = tokens.includes("maleadapter") || tokens.includes("femaleadapter");
    const contextualRequest = adapterRequest || tokens.some((token) => ["strap", "connector", "coupling", "cover"].includes(token));
    const materialSpecified = tokens.some((token) => MATERIAL_TOKENS.has(token));
    const context = hints.materialContext;
    const hits =
      context && contextualRequest && !materialSpecified
        ? searchHits
            .map((hit) => {
              const productTokens = tokenize(`${hit.product.title} ${hit.product.specs}`);
              const contextMatch =
                productTokens.includes(context.material) && productTokens.includes(context.tradeSize);
              return {
                ...hit,
                score: hit.score + (contextMatch && hit.quality >= 0.4 ? QUOTE_CONTEXT_BONUS : 0),
              };
            })
            .sort((a, b) => b.score - a.score || a.product.rank - b.product.rank)
        : searchHits;
    const slangNote = rewrite.applied
      .map(({ term, entry }) => `Trade term "${term}" → ${entry.notes || entry.searchAs}`)
      .join("; ");
    const top = hits[0];
    if (!top || top.quality < 0.4) {
      return {
        confidence: "none",
        product: null,
        alternatives: hits.filter((h) => h.quality >= 0.2).slice(0, 8).map((h) => h.product),
        similar: [],
        ...(/\braised ground screws?\b/i.test(query)
          ? { note: "Catalog matching does not verify the requested raised grounding screw provision; confirm the exact box" }
          : slangNote ? { note: slangNote } : {}),
      };
    }
    if (context?.material === "steel" && tokens.includes("cover") &&
        /\b(?:non-metallic|pvc)\b/i.test(`${top.product.title} ${top.product.specs}`)) {
      return {
        confidence: "none",
        product: null,
        alternatives: hits.map((hit) => hit.product),
        similar: [],
        note: "Nearby cover specifies steel, but this candidate is non-metallic; confirm the correct metal cover rather than substituting it",
      };
    }
    const second = hits[1];
    const dominant =
      !second ||
      top.score - second.score >= 0.05 ||
      (top.product.rank <= 200 && top.product.rank * 8 <= second.product.rank);
    const exactSku = top.product.sku.toLowerCase() === query.trim().toLowerCase();
    const topMpn = compactCode(top.product.mpn);
    const packageLengths =
      top.product.category === WIRE_CATEGORY && topMpn
        ? new Set(
            hits
              .filter((hit) => compactCode(hit.product.mpn) === topMpn)
              .map((hit) => packageLength(hit.product))
              .filter((length): length is number => length !== undefined),
          )
        : new Set<number>();
    const packageAmbiguous =
      !exactSku && packageLengths.size > 1 && ![...packageLengths].some((length) => tokens.includes(String(length)));
    let confidence: MatchResult["confidence"] =
      (top.exact && !packageAmbiguous) || (top.quality >= 0.8 && dominant && !packageAmbiguous) ? "high" : "medium";
    const notes = slangNote ? [slangNote] : [];
    if (!top.exact && /^[a-z]{2,}\d{3,}[a-z]*$/i.test(query.trim()) &&
        topMpn.startsWith(compactCode(query)) && topMpn !== compactCode(query)) {
      confidence = "medium";
      notes.push(`Exact requested part number is not available in the catalog; candidate ${top.product.mpn} has a suffix or variant that requires confirmation`);
    }
    if (!top.exact && tokens.includes("thhn") && tokens.some((token) => ["solid", "stranded"].includes(token))) {
      const productTokens = tokenize(`${top.product.title} ${top.product.specs}`);
      if (!productTokens.some((token) => ["solid", "stranded"].includes(token))) {
        confidence = "medium";
        notes.push("Confirm conductor stranding; catalog metadata does not establish solid versus stranded");
      }
    }
    if (!top.exact && tokens.includes("mc") && tokens.some((token) => /^\d+\/[234]$/.test(token))) {
      confidence = "medium";
      notes.push("Confirm MC cable armor, conductor colors and requested coil length; lighting-control MC is not an automatic substitute");
    }
    if (!top.exact && tokens.includes("mc") && tokens.includes("connector")) {
      confidence = "medium";
      notes.push("Confirm connector cable range, knockout size and requested package count");
    }
    if (!top.exact && tokens.includes("cover")) {
      confidence = "medium";
      notes.push("Confirm cover material, device cutout and box mounting compatibility");
    }
    if (!top.exact && tokens.includes("groundrod") && !tokens.includes("clamp")) {
      confidence = "medium";
      notes.push("Confirm rod diameter and copper-clad versus solid copper construction");
    }
    if (!top.exact && tokens.includes("thhn") && tokens.some((token) => ["copper", "aluminum"].includes(token))) {
      const productTokens = tokenize(`${top.product.title} ${top.product.specs}`);
      if (!productTokens.some((token) => ["copper", "aluminum"].includes(token))) {
        confidence = "medium";
        notes.push("Catalog does not specify conductor material; confirm copper/aluminum before ordering");
      }
    }
    if (!top.exact && tokens.includes("clamps") && tokens.includes("water")) {
      confidence = "medium";
      notes.push("Confirm water pipe diameter and grounding conductor range");
    }
    if (!top.exact && tokens.includes("meter") && tokens.includes("socket")) {
      confidence = "medium";
      notes.push("Confirm utility approval, terminal count and overhead/underground configuration");
    }
    if (!top.exact && tokens.includes("hub")) {
      confidence = "medium";
      notes.push("Confirm equipment model and hub mounting pattern; plate hubs and threaded hubs are not interchangeable");
    }
    if (!top.exact && tokens.includes("staples")) {
      confidence = "medium";
      notes.push("Staples must fit the final cable size and type, not the service amp rating; confirm package quantity");
    }
    if (packageAmbiguous) {
      notes.push("Multiple package lengths are available — confirm the coil or spool length");
    }
    if (
      !top.exact &&
      tokens.some((token) => token === "breaker" || token === "br") &&
      top.product.category === "Power Distribution" &&
      top.product.subcategory === "Circuit Breakers" &&
      !tokens.some((token) => /^[1-4]p$/.test(token))
    ) {
      confidence = "medium";
      notes.push("Confirm breaker pole count and panel compatibility");
    }
    if (
      !top.exact &&
      tokens.includes("receptacle") &&
      top.product.category === RECEPTACLE_CATEGORY &&
      top.product.subcategory === RECEPTACLE_SUBCATEGORY &&
      (!receptacleForm(tokens) || !tokens.some((token) => /^\d{2,3}v$/.test(token)))
    ) {
      confidence = "medium";
      notes.push("Confirm receptacle voltage and single/duplex configuration");
    }
    if (contextualRequest && !materialSpecified && (context || adapterRequest)) {
      confidence = "medium";
      if (context) {
        const productTokens = tokenize(`${top.product.title} ${top.product.specs}`);
        const matchesContext =
          productTokens.includes(context.material) && productTokens.includes(context.tradeSize);
        const candidateMaterial = productTokens.find(
          (token) => MATERIAL_TOKENS.has(token) && token !== context.material,
        );
        if (matchesContext) {
          notes.push(
            `${context.material.toUpperCase()} is suggested by nearby ${context.tradeSize} items in this quote — confirm ${adapterRequest ? "adapter" : "fitting/cover"} material`,
          );
        } else {
          notes.push(
            `${context.material.toUpperCase()} appears on nearby ${context.tradeSize} items, but the catalog match${
              candidateMaterial ? ` is ${candidateMaterial.toUpperCase()}` : " does not confirm the material"
            } — confirm ${adapterRequest ? "adapter" : "fitting/cover"} material`,
          );
        }
      } else {
        notes.push("Raceway material is unspecified — confirm the adapter type");
      }
    }
    if (
      tokens.includes("barrel") &&
      tokens.includes("connector") &&
      !tokenize(`${top.product.title} ${top.product.specs}`).includes("barrel")
    ) {
      confidence = "medium";
      notes.push("Confirm the double-barrel fitting style; the catalog match is a different connector design");
    }
    if (/\bsplices?\b/i.test(query) && /\b\d+\s+splices?\/kit\b/i.test(`${top.product.title} ${top.product.specs}`)) {
      confidence = "medium";
      notes.push("Catalog package has multiple splices per kit — confirm whether quantity means kits or individual splices");
    }
    if (/\bsmall\b/i.test(query) && /\bheat\s*shrink/i.test(`${top.product.title} ${top.product.specs}`)) {
      confidence = "medium";
      notes.push("Confirm heat-shrink conductor range and length");
    }
    if (/lamps?\s+(?:sold\s+)?sep/i.test(`${top.product.title} ${top.product.specs}`) && !tokens.some((token) => token === "lamp" || token === "tube")) {
      confidence = "medium";
      notes.push("Lamps or tubes are sold separately — confirm whether they should be added");
    }
    if (
      tokens.includes("feedthru") &&
      top.product.subcategory === "Circuit Breakers" &&
      tokenize(top.product.title).includes("meter")
    ) {
      confidence = "medium";
      notes.push("Confirm this meter-breaker assembly is the requested feed-through panel");
    }
    // "a roll of romex": the cable type is clear but the gauge is a guess.
    if (!top.exact && top.product.category === WIRE_CATEGORY && !tokens.includes("holder") && !tokens.some(isSpecToken)) {
      confidence = "medium";
      notes.push("No wire size given — confirm the gauge/conductor count");
    }

    const similarSkus = this.get(top.product.sku)?.similar ?? [];
    return {
      confidence,
      product: top.product,
      alternatives: hits.slice(1, 9).map((h) => h.product),
      similar: this.summariesFor(similarSkus),
      ...(notes.length ? { note: notes.join(". ") } : {}),
    };
  }
}
