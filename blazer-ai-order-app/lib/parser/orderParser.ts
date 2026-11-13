import type { ParsedLine } from "../types";

const UNIT_WORDS: Record<string, string> = {
  ea: "ea", each: "ea", pc: "ea", pcs: "ea", piece: "ea", pieces: "ea", unit: "ea", units: "ea", qty: "ea", x: "ea",
  stick: "stick", sticks: "stick",
  box: "box", boxes: "box", bx: "box",
  roll: "roll", rolls: "roll", coil: "coil", coils: "coil", spool: "spool", spools: "spool", reel: "reel", reels: "reel",
  ft: "ft", feet: "ft", foot: "ft", "'": "ft", lf: "ft",
};
const UNIT_ALT = "ea|each|pcs?|pieces?|units?|qty|x|sticks?|boxes|bx|bags?|bundles?|cases?|packs?|rolls?|coils?|spools?|reels?";

/**
 * Words that, directly after a leading number, mean the number is a size/rating
 * ("12 thhn", "4 square box", "2 gang", "3 phase") rather than a quantity.
 */
const SPEC_CONTEXT =
  /^(?:thhn|thwn|xhhw|awg|ga|gauge|str|strand|stranded|sol|solid|cu|al|romex|nm|nmb|mc|ser|seu|use|so|square|sq|oct|octagon|gang|g|phase|ph|pole|p|hole|screw|amps?|a|volts?|v|way|inch|in|deep|dp|lb|ton|kcmil|mcm|\/0|o)\b/i;

const MAX_QTY = 100000;

const WORD_NUMBERS: Record<string, number> = {
  a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, dozen: 12, fifteen: 15, twenty: 20, fifty: 50, hundred: 100,
};
const WORD_NUMBER_ALT = Object.keys(WORD_NUMBERS).join("|");
const BULK_UNIT_ALT = "rolls?|coils?|spools?|reels?|boxes|box|bags?|sticks?|bundles?|cases?|packs?";
const EXTRA_UNITS: Record<string, string> = { bag: "bag", bags: "bag", bundle: "bundle", bundles: "bundle", case: "case", cases: "case", pack: "pack", packs: "pack" };

function toInt(value: string): number {
  return Number.parseInt(value.replace(/,/g, ""), 10);
}

function unitFor(word: string | undefined): string {
  if (!word) return "ea";
  const w = word.toLowerCase();
  return UNIT_WORDS[w] ?? EXTRA_UNITS[w] ?? "ea";
}

function cleanQuery(s: string): string {
  return s
    .replace(/^[\s\-:–—,*•·]+|[\s\-:–—,*•·]+$/g, "")
    .replace(/\s{2,}/g, " ")
    .trim();
}

interface QtyMatch {
  quantity: number;
  unit: string;
  rest: string;
}

/** Leading quantity, if the line clearly starts with one. */
function leadingQuantity(s: string): QtyMatch | null {
  // "a roll of romex", "two boxes of wire nuts", "roll of 12/2"
  let m = s.match(new RegExp(`^(?:(${WORD_NUMBER_ALT}|\\d[\\d,]*)\\s+)?(${BULK_UNIT_ALT})\\s+of\\s+(.+)$`, "i"));
  if (m) {
    const n = m[1] ? (WORD_NUMBERS[m[1].toLowerCase()] ?? toInt(m[1])) : 1;
    return { quantity: n, unit: unitFor(m[2]), rest: m[3] };
  }
  // "two 20a breakers", "ten sticks 3/4 emt" (but not "three phase", "two gang")
  m = s.match(new RegExp(`^(${WORD_NUMBER_ALT})\\s+(.+)$`, "i"));
  if (m && !/^an?$/i.test(m[1]) && !SPEC_CONTEXT.test(m[2])) {
    const inner = leadingQuantity(`1 ${m[2]}`);
    const n = WORD_NUMBERS[m[1].toLowerCase()];
    if (inner) return { ...inner, quantity: n };
    return { quantity: n, unit: "ea", rest: m[2] };
  }

  m = s.match(/^\(?\s*(\d[\d,]*)\s*\)?\s*('|ft\b|feet\b|foot\b|lf\b)\s*(?:of\s+)?(.+)$/i);
  if (m) return { quantity: toInt(m[1]), unit: "ft", rest: m[3] };

  m = s.match(new RegExp(`^\\(?\\s*(\\d[\\d,]*)\\s*\\)?\\s*(${UNIT_ALT})\\b\\.?\\s*(?:of\\s+)?[-:]?\\s*(.+)$`, "i"));
  if (m) return { quantity: toInt(m[1]), unit: unitFor(m[2]), rest: m[3] };

  m = s.match(/^qty\s*[:#]?\s*(\d[\d,]*)\s*[-:,]?\s*(.+)$/i);
  if (m) return { quantity: toInt(m[1]), unit: "ea", rest: m[2] };

  m = s.match(/^\(\s*(\d[\d,]*)\s*\)\s*(.+)$/);
  if (m) return { quantity: toInt(m[1]), unit: "ea", rest: m[2] };

  m = s.match(/^(\d[\d,]*)\s*[-–:=]\s+(.+)$/);
  if (m) return { quantity: toInt(m[1]), unit: "ea", rest: m[2] };

  m = s.match(/^(\d[\d,]*)\s+(.+)$/);
  if (m) {
    const n = toInt(m[1]);
    const rest = m[2];
    if (SPEC_CONTEXT.test(rest)) return null;
    // "1 1/4 emt" / "2 1/2 rigid" are trade sizes, not quantities.
    if (n <= 3 && /^(?:1\/4|1\/2|3\/4)\b/.test(rest)) return null;
    return { quantity: n, unit: "ea", rest };
  }
  return null;
}

/** Trailing quantity: "... x 10", "... qty 10", "... - 10", "... (10)", "... 10 ea", "... 500'". */
function trailingQuantity(s: string): QtyMatch | null {
  const patterns: Array<[RegExp, (m: RegExpMatchArray) => string]> = [
    [/^(.+?)\s*[-,]?\s*(?:(?<![a-z])x|qty[:#]?|\*)\s*(\d[\d,]*)\s*(ea|pcs?|each)?\s*$/i, () => "ea"],
    [new RegExp(`^(.+?)\\s+(\\d[\\d,]*)\\s*(${UNIT_ALT}|ft|feet|foot|lf|')\\.?\\s*$`, "i"), (m) => unitFor(m[3])],
    [/^(.+?)\s*\(\s*(\d[\d,]*)\s*\)\s*$/, () => "ea"],
    [/^(.+?)\s+[-–]\s*(\d[\d,]*)\s*$/, () => "ea"],
  ];
  for (const [re, unit] of patterns) {
    const m = s.match(re);
    if (m && /[a-z]/i.test(m[1])) return { quantity: toInt(m[2]), unit: unit(m), rest: m[1] };
  }
  return null;
}

/** Strip list markers ("-", "•", "1.", "2)") that are numbering rather than quantities. */
function stripListMarker(s: string): string {
  const bullet = s.replace(/^\s*[-*•·▪►]\s+/, "");
  if (bullet !== s) return bullet;
  const m = s.match(/^\s*\d{1,3}[.)]\s+(.+)$/);
  if (m && (leadingQuantity(m[1]) || /^\(/.test(m[1]))) return m[1];
  return s;
}

/** Pull cut instructions and free-form notes out of a line into a comment. */
function extractComment(s: string): { text: string; comment: string[]; cutFeet: number | null } {
  const comment: string[] = [];
  let cutFeet: number | null = null;
  let text = s;

  text = text.replace(
    /\(?\s*(\d+)\s*(?:cuts?|pcs?|pieces?)\s*(?:@|at|of|x)\s*(\d[\d,]*)\s*(?:'|ft\b|feet\b|foot\b)?\s*(?:each|ea)?\s*\)?/gi,
    (_m, cuts: string, len: string) => {
      comment.push(`${cuts} cuts @ ${toInt(len)}ft`);
      cutFeet = (cutFeet ?? 0) + toInt(cuts) * toInt(len);
      return " ";
    },
  );
  text = text.replace(
    /\bcut\s*(?:in)?(?:to)?\s*(\d+)\s*(?:x|@|at)\s*(\d[\d,]*)\s*(?:'|ft\b|feet\b|foot\b)?/gi,
    (_m, cuts: string, len: string) => {
      comment.push(`${cuts} cuts @ ${toInt(len)}ft`);
      cutFeet = (cutFeet ?? 0) + toInt(cuts) * toInt(len);
      return " ";
    },
  );
  text = text.replace(/\bcut\s+(?:in)?to\s+[^,;()]+/gi, (m) => {
    comment.push(m.trim());
    return " ";
  });
  text = text.replace(/\s(?:--|\/\/|note:|notes:|comment:)\s*(.+)$/i, (_m, note: string) => {
    comment.push(note.trim());
    return " ";
  });
  // Parenthetical notes (but not "(10)" quantities or "(2)" counts).
  text = text.replace(/\(([^()]*[a-z][^()]*)\)/gi, (m, inner: string) => {
    if (/^\s*\d+\s*(?:ea|pcs?)?\s*$/i.test(inner)) return m;
    if (/^\s*(?:for|to|by|at|loc|location|room|panel|deliver|tag|label|mark)\b/i.test(inner) || inner.split(/\s+/).length >= 3) {
      comment.push(inner.trim());
      return " ";
    }
    return m;
  });
  return { text, comment: comment.filter(Boolean), cutFeet };
}

const HEADER_LINE = /^(?:qty|quantity|item|description|desc|part|material|materials|bill of materials|bom|panel schedule|ckt|circuit)\b[\s:|,\t-]*(?:(?:qty|quantity|item|desc|description|part|#|no|uom|notes?)\b[\s:|,\t-]*)*$/i;

const CONTEXT_LINE =
  /^(?:material(?:s)?\s+list|job|project|customer|po\s*#?|p\.o\.|ship\s*to|deliver(?:y)?|attn|subject|date|from|to|thanks|thank you|regards)\b.*?(?::|-|$)/i;

export function parseLine(raw: string): ParsedLine | null {
  const original = raw.replace(/\t+/g, " ").trim();
  if (!original || !/[a-z0-9]/i.test(original) || HEADER_LINE.test(original)) return null;
  if (CONTEXT_LINE.test(original) && !/\d/.test(original.replace(/^po\s*#?\s*\d+/i, ""))) return null;
  if (!/[a-z]/i.test(original) && !/\d{3,}/.test(original)) return null;

  const { text, comment, cutFeet } = extractComment(stripListMarker(original));
  let working = cleanQuery(text);
  let quantity = 1;
  let unit = "ea";
  let quantityExplicit = false;

  const lead = leadingQuantity(working);
  if (lead && /[a-z0-9]/i.test(lead.rest)) {
    ({ quantity, unit } = lead);
    working = cleanQuery(lead.rest);
    quantityExplicit = true;
  } else {
    const trail = trailingQuantity(working);
    if (trail) {
      ({ quantity, unit } = trail);
      working = cleanQuery(trail.rest);
      quantityExplicit = true;
    }
  }

  if (!quantityExplicit && cutFeet) {
    quantity = cutFeet;
    unit = "ft";
    quantityExplicit = true;
  }

  if (!Number.isFinite(quantity) || quantity < 1) quantity = 1;
  quantity = Math.min(quantity, MAX_QTY);

  if (!working) return null;
  return { raw: original, query: working, quantity, unit, quantityExplicit, comment: comment.join("; ") };
}

/**
 * Job names and section headings ("Smith residence - rough in") have no quantity, no sizes,
 * and no catalog match; keep them out of the BOM.
 */
export function isLikelyHeading(line: ParsedLine, confidence: string): boolean {
  return confidence === "none" && !line.quantityExplicit && !/\d/.test(line.raw);
}

/** Split pasted text (emails, panel schedules, takeoff lists) into order lines. */
export function parseOrderText(input: string, maxLines = 300): ParsedLine[] {
  const lines: ParsedLine[] = [];
  for (const block of input.split(/\r?\n|;/)) {
    // "10 1/2 emt, 20 1/2 conn" -> separate items when every comma segment starts with a quantity.
    const segments = block.split(",");
    const parts =
      segments.length > 1 && segments.every((seg) => /^\s*\(?\d/.test(seg)) ? segments : [block];
    for (const part of parts) {
      const parsed = parseLine(part);
      if (parsed) lines.push(parsed);
      if (lines.length >= maxLines) return lines;
    }
  }
  return lines;
}
