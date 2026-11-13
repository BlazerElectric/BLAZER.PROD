/** Raw product record as exported by export_web_catalog.py. */
export interface CatalogProduct {
  sku: string;
  title: string;
  brand: string;
  mpn: string;
  category: string;
  subcategory: string;
  specs: string;
  rank: number;
  search_keywords: string[];
  common_misspellings: string[];
  accessories: string[];
  similar: string[];
}

export interface CatalogFile {
  last_updated: string;
  total_products: number;
  products: Record<string, CatalogProduct>;
}

/** Lightweight product shape sent to the browser. */
export interface ProductSummary {
  sku: string;
  title: string;
  brand: string;
  mpn: string;
  category: string;
  subcategory: string;
  specs: string;
  rank: number;
}

export type Confidence = "high" | "medium" | "none";

export interface SearchHit {
  product: ProductSummary;
  /** Normalized 0..1 text-match quality before popularity weighting. */
  quality: number;
  /** Final ranking score (quality weighted by popularity). */
  score: number;
  exact: boolean;
}

export interface MatchResult {
  confidence: Confidence;
  product: ProductSummary | null;
  /** Other search candidates for the same request. */
  alternatives: ProductSummary[];
  /** Catalog substitutes (similar_products.csv) for the matched product. */
  similar: ProductSummary[];
  /** Why the match was made or what to double-check (slang translation, missing size). */
  note?: string;
}

export interface ParsedLine {
  raw: string;
  query: string;
  quantity: number;
  unit: string;
  quantityExplicit: boolean;
  comment: string;
}

export interface ResolvedLine extends ParsedLine, MatchResult {}

export type LineSource = "paste" | "chat" | "search";

export interface OrderLine {
  id: string;
  source: LineSource;
  rawText: string;
  quantity: number;
  unit: string;
  comment: string;
  confidence: Confidence;
  /** True once a person has explicitly picked or verified the product. */
  confirmed: boolean;
  product: ProductSummary | null;
  alternatives: ProductSummary[];
  similar: ProductSummary[];
  /** Free-text fallback for items not found in the catalog. */
  manualText: string;
  reason?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatSuggestion {
  description: string;
  quantity: number;
  unit: string;
  note: string;
  reason: string;
  match: MatchResult;
}

export interface ChatResponse {
  reply: string;
  suggestions: ChatSuggestion[];
}
