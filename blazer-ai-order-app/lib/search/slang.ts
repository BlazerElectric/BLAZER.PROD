/**
 * Electrician slang dictionary ("colorado jim", "asshole", "smurf tube") loaded from
 * config/trade_slang.csv. Slang phrases are rewritten into catalog wording before search, and
 * optional pinned SKUs (first = default pick) are boosted to the top of the results.
 */

export interface SlangEntry {
  terms: string[];
  searchAs: string;
  skus: string[];
  notes: string;
}

export interface SlangRewrite {
  query: string;
  pinned: string[];
  applied: Array<{ term: string; entry: SlangEntry }>;
}

function splitCsvLine(line: string): string[] {
  const cells: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") {
      cells.push(cell);
      cell = "";
    } else cell += ch;
  }
  cells.push(cell);
  return cells.map((c) => c.trim());
}

const list = (value: string | undefined) =>
  (value ?? "")
    .split("|")
    .map((v) => v.trim())
    .filter(Boolean);

/** Parse `slang,search_as,skus,notes` CSV; `slang` and `skus` are pipe-separated lists. */
export function parseSlangCsv(text: string): SlangEntry[] {
  const rows = text.replace(/^\uFEFF/, "").split(/\r?\n/).filter((l) => l.trim() && !l.trim().startsWith("#"));
  if (rows.length === 0) return [];
  const header = splitCsvLine(rows[0]).map((h) => h.toLowerCase());
  const col = (name: string) => header.indexOf(name);
  const [iSlang, iSearch, iSkus, iNotes] = [col("slang"), col("search_as"), col("skus"), col("notes")];
  if (iSlang < 0 || iSearch < 0) throw new Error("trade_slang.csv must have 'slang' and 'search_as' columns");
  return rows.slice(1).flatMap((row) => {
    const cells = splitCsvLine(row);
    const terms = list(cells[iSlang]).map((t) => t.toLowerCase());
    const searchAs = cells[iSearch] ?? "";
    if (terms.length === 0 || !searchAs) return [];
    return [{ terms, searchAs, skus: iSkus >= 0 ? list(cells[iSkus]) : [], notes: iNotes >= 0 ? (cells[iNotes] ?? "") : "" }];
  });
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

interface CompiledTerm {
  term: string;
  re: RegExp;
  entry: SlangEntry;
}

export class SlangDictionary {
  private readonly compiled: CompiledTerm[];

  constructor(readonly entries: SlangEntry[] = []) {
    this.compiled = entries
      .flatMap((entry) =>
        entry.terms.map((term) => {
          // Whole words, flexible spacing/hyphens, plural tolerant ("colorado jims", "red-heads").
          const body = term.split(/[\s-]+/).map(escape).join("[\\s-]*");
          return { term, entry, re: new RegExp(`(^|[^a-z0-9])${body}(?:e?s)?(?![a-z0-9])`, "i") };
        }),
      )
      .sort((a, b) => b.term.length - a.term.length);
  }

  get size(): number {
    return this.entries.length;
  }

  rewrite(query: string): SlangRewrite {
    let text = query;
    const pinned: string[] = [];
    const applied: SlangRewrite["applied"] = [];
    for (const { term, re, entry } of this.compiled) {
      if (applied.some((a) => a.entry === entry)) continue;
      if (!re.test(text)) continue;
      text = text.replace(re, (_m, lead: string) => `${lead}${entry.searchAs}`);
      applied.push({ term, entry });
      for (const sku of entry.skus) if (!pinned.includes(sku)) pinned.push(sku);
    }
    return { query: text, pinned, applied };
  }
}
