import type { CatalogProduct, MatchResult, ProductSummary, SearchHit } from "../types";
import { boundedEditDistance, maxTypoDistance } from "./fuzzy";
import { compactCode, isSpecToken, tokenize } from "./normalize";
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
const BULK_UNITS = new Set(["roll", "coil", "spool", "reel", "ft"]);
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
const WIRE_CATEGORY = "Wire, Cable & Cord";

export interface SearchOptions {
  limit?: number;
  /** Treat the final token as a prefix (type-ahead). */
  prefixLast?: boolean;
  /** Ordered unit from the request ("roll", "ft"), used to demote piece goods for bulk units. */
  unit?: string;
}

export interface MatchHints {
  unit?: string;
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
    const { hits, rewrite, tokens } = this.run(query, { limit: 12, unit: hints.unit });
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
        ...(slangNote ? { note: slangNote } : {}),
      };
    }
    const second = hits[1];
    const dominant =
      !second ||
      top.score - second.score >= 0.05 ||
      (top.product.rank <= 200 && top.product.rank * 8 <= second.product.rank);
    let confidence: MatchResult["confidence"] = top.exact || (top.quality >= 0.8 && dominant) ? "high" : "medium";
    const notes = slangNote ? [slangNote] : [];
    // "a roll of romex": the cable type is clear but the gauge is a guess.
    if (!top.exact && top.product.category === WIRE_CATEGORY && !tokens.some(isSpecToken)) {
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
