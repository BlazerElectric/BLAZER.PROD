import type { ParsedLine } from "../types";
import { normalizeSymbols } from "../search/normalize";

const UNIT_WORDS: Record<string, string> = {
  ea: "ea", each: "ea", pc: "ea", pcs: "ea", piece: "ea", pieces: "ea", unit: "ea", units: "ea", qty: "ea", x: "ea",
  stick: "stick", sticks: "stick",
  box: "box", boxes: "box", bx: "box",
  roll: "roll", rolls: "roll", coil: "coil", coils: "coil", spool: "spool", spools: "spool", reel: "reel", reels: "reel",
  ft: "ft", feet: "ft", foot: "ft", "'": "ft", lf: "ft",
  jar: "jar", jars: "jar", can: "can", cans: "can", tube: "tube", tubes: "tube",
};
const UNIT_ALT = "ea|each|pcs?|pieces?|units?|qty|x|sticks?|boxes|bx|bags?|bundles?|cases?|packs?|rolls?|coils?|spools?|reels?|jars?|cans?|tubes?";

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
const BULK_UNIT_ALT = "rolls?|coils?|spools?|reels?|boxes|box|bags?|sticks?|bundles?|cases?|packs?|jars?|cans?|tubes?";
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
    if (n <= 3 && /^1\/2\b\s*"?\s*lb\b/i.test(rest)) {
      return { quantity: n, unit: "ea", rest };
    }
    // "1 1/4 emt" / "2 1/2 rigid" are trade sizes, not quantities.
    if (n <= 3 && /^(?:1\/4|1\/2|3\/4)\b/.test(rest)) return null;
    return { quantity: n, unit: "ea", rest };
  }
  return null;
}

/** Trailing quantity: "... x 10", "... qty 10", "... - 10", "... (10)", "... 10 ea", "... 500'". */
function trailingQuantity(s: string): QtyMatch | null {
  const patterns: Array<[RegExp, (m: RegExpMatchArray) => string]> = [
    [/^(.+?)\s*\(\s*x\s*(\d[\d,]*)\s*\)\s*$/i, () => "ea"],
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

const QUOTE_SECTION = /^quote\s*(?:#\s*)?\d+\s*:?\s*$/i;
const DOCUMENT_TITLE = /^(?:[a-z][a-z0-9&'.-]*\s+){1,4}quotes?$/i;

export function isQuoteSection(line: ParsedLine): boolean {
  return QUOTE_SECTION.test(line.raw);
}

export function parseLine(raw: string): ParsedLine | null {
  const original = raw.replace(/\t+/g, " ").trim();
  if (/^(?:oh|hey)$/i.test(original)) return null;
  if (!original || !/[a-z0-9]/i.test(original) || HEADER_LINE.test(original)) return null;
  if (CONTEXT_LINE.test(original) && !/\d/.test(original.replace(/^po\s*#?\s*\d+/i, ""))) return null;
  if (!/[a-z]/i.test(original) && !/\d{3,}/.test(original)) return null;

  const { text, comment, cutFeet } = extractComment(stripListMarker(normalizeSymbols(original)));
  let working = cleanQuery(text)
    .replace(/^(?:(?:hey|oh)\s+)?(?:also\s+)?(?:need|throw in|gimme)\s+/i, "")
    .replace(/^for the riser\s+gimme\s+/i, "")
    .replace(/^(?:give me|grab|add|plus|with)\s+/i, "")
    .replace(/^for wire pull i need\s+/i, "")
    .replace(/^for the panel\b.*?\bso send\s+/i, "")
    .replace(/^same thing for (?:the\s+)?/i, "")
    .replace(/^let me know if (?:u|you) have (?:the\s+)?/i, "")
    .replace(/\s+in stock(?:\s+too)?$/i, "")
    .replace(/^(?:oh\s+)?and\s+/i, "")
    .replace(/\btwo[\s-]+hole\b/gi, "2-hole")
    .replace(/^a tube of\s+/i, "")
    .replace(/^a pack of\s+/i, "");
  const revised = /\b(?:or actually|make it|wait no)\b/i.test(working);
  const starterAssembly = /\bstarter\b.*\boverload\b/i.test(working);
  const tapePack = /\bphasing tape pack\b/i.test(working);
  const availability = /\bin stock\b/i.test(original);
  if (/\ba tube of\b/i.test(original)) comment.push("Tube requested; confirm product form and package size");
  const packaged = working.match(/^(.+?)\s+box of\s+(\d+)\s*$/i);
  if (packaged) {
    working = packaged[1];
    comment.push(`Box of ${packaged[2]} requested; confirm catalog package count`);
  }
  const stockLength = working.match(/^(.+?)\s+(\d+)\s*ft\s+stick$/i);
  if (stockLength) {
    working = `${stockLength[1]} ${stockLength[2]}ft`;
    comment.push(`${stockLength[2]}ft stick requested`);
    if (/\brigid mast\b/i.test(stockLength[1])) {
      working = stockLength[1].replace(/\brigid mast\b/i, "rigid conduit");
    }
  }
  let quantity = 1;
  let unit = "ea";
  let quantityExplicit = false;

  const cablePackage = working.match(/^a\s+(\d+)\s*ft\s+(coil|roll|spool)\s+of\s+(.+)$/i);
  if (cablePackage) {
    working = `1 ${cablePackage[2]} of ${cablePackage[3]} ${cablePackage[1]}ft`;
    comment.push(`${cablePackage[1]}ft ${cablePackage[2]} requested; confirm stocked package length`);
  }
  const containerCount = working.match(/^(?:a|one|1)\s+(box|jar|bag|pack)\s+of\s+(\d+)\s+(.+)$/i);
  if (containerCount) {
    working = `1 ${containerCount[1]} of ${containerCount[3]}`;
    comment.push(`${containerCount[2]} pieces per ${containerCount[1]} requested; confirm catalog package count`);
  }
  const lead = leadingQuantity(working);
  if (lead && /[a-z0-9]/i.test(lead.rest)) {
    ({ quantity, unit } = lead);
    working = cleanQuery(lead.rest);
    quantityExplicit = true;
  } else if (!stockLength) {
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
  const reviewNote = starterAssembly
    ? "Verify starter and overload selection against the motor nameplate, coil voltage, enclosure and manufacturer ratings; no automatic assembly substitution"
    : tapePack
      ? "Confirm the phasing tape assortment and pack quantity; a single-color roll is not an assorted pack"
      : revised
    ? "Customer revised cable size, material or conductor count; confirm final cable type and conductor configuration before selecting a SKU"
    : availability
      ? "Catalog matching does not verify stock; confirm branch availability and panel compatibility"
      : packaged
        ? "Confirm requested box count against the catalog package quantity"
        : stockLength
          ? `Confirm ${stockLength[2]}ft stock length and suitability for the requested use`
          : /\ba tube of\b/i.test(original)
            ? "Tube requested; confirm catalog product form and package size"
            : cablePackage || containerCount
              ? "Confirm requested package length or piece count against the catalog package"
              : /\bper-color quantity\b/i.test(original)
                ? "Quantity repeated for each listed color; confirm per-color footage and package length"
                : ["box", "bag", "jar", "pack"].includes(unit)
                  ? "Confirm the number of pieces supplied per requested package"
                  : undefined;
  return {
    raw: original, query: working, quantity, unit: packaged ? "box" : unit,
    quantityExplicit, comment: comment.join("; "),
    ...(reviewNote ? { reviewNote } : {}),
    ...(revised || starterAssembly || tapePack ? { needsClarification: true } : {}),
  };
}

/**
 * Job names and section headings ("Smith residence - rough in") have no quantity, no sizes,
 * and no catalog match; keep them out of the BOM.
 */
export function isLikelyHeading(line: ParsedLine, confidence: string): boolean {
  return (
    isQuoteSection(line) ||
    (!line.quantityExplicit && DOCUMENT_TITLE.test(line.raw)) ||
    (!line.quantityExplicit && /\bservice upgrade quote\b/i.test(line.raw)) ||
    (confidence === "none" &&
      !line.quantityExplicit &&
      !/\d/.test(line.raw) &&
      /\b(?:residence|jobsite)\b|-\s*rough[\s-]*in\b/i.test(line.raw))
  );
}

function expandWireColors(input: string): string {
  const color = "(?:black|blk|red|white|wht|green|grn)";
  const pattern = new RegExp(
    `\\b(\\d+)ft\\s+(spools?\\s+)?of\\s+(#\\d+\\s+thhn\\s+(?:solid|stranded))\\s*(?:-\\s*)?(${color}(?:\\s*,\\s*(?:and\\s+)?${color})+)`,
    "gi",
  );
  return input.replace(pattern, (_match, length: string, spool: string | undefined, wire: string, colors: string) =>
    colors.split(/\s*,\s*(?:and\s+)?/i).map((value) =>
      `${spool ? `1 spool of ${wire} ${value} ${length}ft` : `${length}ft of ${wire} ${value}`} -- per-color quantity from wire list`,
    ).join("; "),
  );
}

/** Split pasted text (emails, panel schedules, takeoff lists) into order lines. */
export function parseOrderText(input: string, maxLines = 300): ParsedLine[] {
  const lines: ParsedLine[] = [];
  const conversational = /\b(?:throw in|gimme|give me|grab|same thing|oh and|add \d|or actually|wait no|let me know if)\b/i.test(input);
  if (conversational) input = expandWireColors(input);
  for (const block of input.split(conversational ? /\r?\n|;|\.\s+(?=[a-z0-9])/i : /\r?\n|;/)) {
    // "10 1/2 emt, 20 1/2 conn" -> separate items when every comma segment starts with a quantity.
    const segments = block
      .replace(/,\s*(?=(?:or actually|actually|wait no|make it)\b)/gi, " ")
      .split(/,(?!\d{3}(?:ft\b|'|\b))/);
    // Long comma-delimited blocks are often pasted BOMs with implicit quantities.
    const commaSeparatedOrder = segments.length >= 8;
    const parts = segments.length > 1 && (conversational || commaSeparatedOrder || segments.every((seg) => /^\s*\(?\d/.test(seg)))
      ? segments
      : [block];
    for (const part of parts) {
      const rodWithClamps = conversational && /\bground rods with acorns\b/i.test(part);
      const items = conversational
        ? part.replace(/\bground rods with acorns\b/i, "ground rods and acorns")
            .split(/\s+(?:and|with)\s+(?=(?:\d+\s|\d+(?:ft|')\s|a\s+(?:\d|mast|pack|box|jar|bag|roll)|two\s+\d+in\s+myers|plastic bushings|acorns|coup\b))/i)
        : [part];
      const breakerGroup = items.some((item) => /\bbreakers?\b/i.test(item)) ||
        items.every((item) => /\b\d+\s*a\b/i.test(item) && /\b(?:single pole|[12]\s*p(?:ole)?)\b/i.test(item));
      const sharedQuantity = part.match(/\(\s*x\s*(\d+)\s*\)\s*$/i)?.[1] ??
        part.match(/\(\s*need\s+(\d+)\s+each\s*\)\s*$/i)?.[1];
      for (const item of items) {
        const parsed = parseLine(item.replace(/\(\s*need\s+\d+\s+each\s*\)/i, ""));
        if (parsed) {
          if (/^(?:coup(?:lings?)?)$/i.test(parsed.query)) {
            const previous = lines.at(-1);
            const fitting = previous?.query.match(/^((?:\d+in|\d+\/\d+)\s+ss)\s+conn(?:ectors?)?$/i);
            if (fitting) {
              parsed.query = `${fitting[1]} couplings`;
              parsed.reviewNote = "Coupling size and set-screw style carried from paired connector request; confirm package quantity";
            }
          }
          if (breakerGroup && !/\bbreakers?\b/i.test(parsed.query) && /\b\d+\s*a\b/i.test(parsed.query)) {
            parsed.query += " breaker";
            parsed.reviewNote = "Breaker type carried from the same phrase; confirm panel compatibility";
          }
          if (breakerGroup && /\bsingle pole\b/i.test(part) && /\bbreakers?\b/i.test(parsed.query) &&
              !/\b(?:single pole|[12]\s*p(?:ole)?)\b/i.test(parsed.query)) {
            parsed.query += " single pole";
            parsed.reviewNote = "Single-pole configuration carried from the same breaker phrase; confirm before ordering";
          }
          if (sharedQuantity && items.length > 1 && !parsed.quantityExplicit) {
            parsed.quantity = Math.min(toInt(sharedQuantity), MAX_QTY);
            parsed.quantityExplicit = true;
            parsed.reviewNote = "Quantity applies to a grouped request; confirm quantity for each item";
          }
          if (/^plastic bushings\b/i.test(parsed.query)) {
            const size = items[0].match(/^\s*(\d+(?:-\d+\/\d+)?(?:\/\d+)?)\s*(?:in\b|")/i)?.[1];
            if (size) {
              parsed.query = `${size}in ${parsed.query}`;
              parsed.reviewNote = "Size and quantity carried from grouped locknut/bushing request; confirm both";
            }
          }
          if (rodWithClamps && /^acorns$/i.test(parsed.query)) {
            parsed.quantity = lines.at(-1)?.quantity ?? 1;
            parsed.reviewNote = "One clamp per requested rod assumed; confirm clamp conductor range and rod diameter";
          }
          if (/^\s*(?:a\s+)?pack of\b/i.test(item)) parsed.unit = "pack";
          const previous = lines.at(-1);
          if (conversational && item !== items[0] && parsed.unit === "ft" && /^(?:white|black|green)$/i.test(parsed.query) && previous?.unit === "ft") {
            const wire = previous.query.match(/^(.+?\bthhn\b.*?)\s+(?:black|white|green)$/i);
            if (wire) {
              parsed.query = `${wire[1]} ${parsed.query}`;
              parsed.reviewNote = "Wire size, insulation and material carried from preceding footage; confirm before ordering";
            }
          }
          lines.push(parsed);
        }
        if (lines.length >= maxLines) return lines;
      }
    }
  }
  return lines;
}
