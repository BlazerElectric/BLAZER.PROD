/**
 * Text normalization shared by the catalog index and incoming queries.
 * Both sides must use the same pipeline so contractor shorthand ("3/4 emt sscr conn",
 * "#12 thhn blk", "romex 12-2") lands on the same tokens as catalog titles.
 */

const PHRASES: Array<[RegExp, string]> = [
  [/\bfa(?=\s+pvc\b)/g, "female adapter"],
  [/\b1[\s-]*home\b/g, "1 hole"],
  [/\b1[\s-]*hole\b/g, "1 hole"],
  [/\bset[\s-]*screw\b/g, "setscrew"],
  [/\bweather[\s-]*proof\b/g, "weatherproof"],
  [/\btamper[\s-]*(?:resistant|resistance|proof)\b/g, "tr"],
  [/\bliquid[\s-]*tight\b/g, "liquidtight"],
  [/\bfeed[\s-]*(?:thru|through)\b/g, "feedthru"],
  [/\block[\s-]*rings?\b/g, "lockring"],
  [/\block[\s-]*nuts?\b/g, "locknut"],
  [/\bmale[\s-]*adapters?\b/g, "maleadapter"],
  [/\bfemale[\s-]*adapters?\b/g, "femaleadapter"],
  [/\bwire[\s-]*nuts?\b/g, "wirenut"],
  [/\bwall[\s-]*plates?\b/g, "plate"],
  [/\bjunction[\s-]*box(?:es)?\b/g, "jbox"],
  [/\bj[\s-]*box(?:es)?\b/g, "jbox"],
  [/\bschedule\s*(40|80)\b/g, "sch$1"],
  [/\bsch\.?\s*(40|80)\b/g, "sch$1"],
  [/\b(?:single|one)[\s-]*pole\b/g, "1p"],
  [/\b(?:double|two)[\s-]*pole\b/g, "2p"],
  [/\b(?:triple|three)[\s-]*pole\b/g, "3p"],
  [/\b(?:single|one)[\s-]*gang\b/g, "1g"],
  [/\b(?:double|two)[\s-]*gang\b/g, "2g"],
  [/\b(?:three|triple)[\s-]*gang\b/g, "3g"],
  [/\b(?:single|one)[\s-]*phase\b/g, "1ph"],
  [/\bthree[\s-]*phase\b/g, "3ph"],
  [/\bground(?:ing)?[\s-]*rod\b/g, "groundrod"],
  [/\bgrd[\s-]*rod\b/g, "groundrod"],
];

/** Token -> canonical token. Applied to both index and query tokens. */
const SYNONYMS: Record<string, string> = {};
const GROUPS: Record<string, string[]> = {
  receptacle: ["recp", "recpt", "rec", "recep", "recept", "receptacles", "outlet", "outlets", "recpts", "receipts"],
  connector: ["conn", "connectors", "cnctr", "connecter", "conns"],
  coupling: ["cplg", "couplings", "coup", "cplgs", "coupler"],
  clip: ["clips"],
  breaker: ["bkr", "breakers", "brkr", "bkrs"],
  ground: ["grd", "gnd", "grnd", "grounding"],
  white: ["wht", "wh"],
  black: ["blk", "bk"],
  green: ["grn"],
  blue: ["blu"],
  gray: ["grey", "gry"],
  solid: ["sol"],
  stranded: ["str", "strand", "19str", "strd"],
  setscrew: ["sscr", "ssc"],
  compression: ["comp", "compr"],
  weatherproof: ["wp"],
  gfci: ["gfi", "gfcis"],
  emt: ["thinwall"],
  rigid: ["grc", "rmc"],
  nmb: ["romex", "nm"],
  fmc: ["flex", "flexible"],
  liquidtight: ["lfmc", "sealtite", "sealtight"],
  channel: ["strut", "unistrut"],
  cover: ["cvr", "covers"],
  plate: ["wallplate", "plates"],
  switch: ["sw", "switches"],
  bushing: ["bshg", "bushings"],
  conduit: ["cond"],
  box: ["boxes"],
  strap: ["straps"],
  square: ["sq", "sqr"],
  "4sq": ["4square", "foursquare"],
  afci: ["afc", "cafi", "cafci"],
  locknut: ["lockring", "lockrings"],
  maleadapter: ["ma"],
};
for (const [canonical, aliases] of Object.entries(GROUPS)) {
  for (const alias of aliases) SYNONYMS[alias] = canonical;
}

const STOPWORDS = new Set([
  "the", "a", "an", "of", "for", "and", "with", "w", "in", "inch", "inches", "ft", "feet", "foot",
  "pc", "pcs", "ea", "each", "qty", "x", "to", "at", "per", "please", "need", "needs", "n/a", "na",
  "used", "is", "or", "on", "awg", "ga", "gauge", "type", "some", "we", "i", "my", "our", "get",
]);

const MIXED_FRACTION = /^\d+-\d+\/\d+$/;
const FRACTION = /^\d+\/\d+$/;

const FRACTION_GLYPHS: Record<string, string> = {
  "¼": "1/4",
  "½": "1/2",
  "¾": "3/4",
  "⅛": "1/8",
  "⅜": "3/8",
  "⅝": "5/8",
  "⅞": "7/8",
};

export function normalizeSymbols(input: string): string {
  return input
    .replace(/(\d)([¼½¾⅛⅜⅝⅞])/g, "$1 $2")
    .replace(/[¼½¾⅛⅜⅝⅞]/g, (fraction) => FRACTION_GLYPHS[fraction])
    .replace(/[\u201c\u201d\u2033]/g, '"')
    .replace(/[\u2018\u2019\u2032]/g, "'");
}

export function normalizeText(input: string): string {
  let t = normalizeSymbols(input).toLowerCase();
  // "1 1/4" -> "1-1/4" (trade sizes)
  t = t.replace(/\b(\d+)\s+(\d+\/\d+)/g, "$1-$2");
  // inch marks and spelled inches after numbers: 3/4" , 2in , 1-1/4 inch
  t = t.replace(/(\d)\s*(?:"|''|inch(?:es)?\b|in\b)/g, "$1 ");
  // feet marks: 500' , 10ft
  t = t.replace(/(\d)\s*(?:'|ft\b|feet\b|foot\b)/g, "$1 ");
  // wire gauge "#12" -> "12"
  t = t.replace(/#\s*(\d)/g, "$1");
  // "12-2" cable notation -> "12/2" (only common conductor counts, not "4-2-1/8")
  t = t.replace(/(^|[^\d/-])(18|16|14|12|10|8|6|4|3|2|1)-([234])(?![\d/-])/g, "$1$2/$3");
  // amps, volts
  t = t.replace(/\b(\d+)\s*(?:amps?|a)\b/g, "$1a");
  t = t.replace(/\b(\d+)\s*(?:volts?|v)\b/g, "$1v");
  // "1 pole" / "2-pole"
  t = t.replace(/\b([1-4])\s*-?\s*poles?\b/g, "$1p");
  t = t.replace(/\b([1-4])\s*-?\s*gang\b/g, "$1g");
  for (const [re, rep] of PHRASES) t = t.replace(re, rep);
  return t;
}

function canonical(token: string): string {
  return SYNONYMS[token] ?? token;
}

/**
 * Split normalized text into match tokens. Fractions ("3/4", "1-1/4", "12/2") stay whole;
 * other hyphen/slash compounds are split and also emitted in a compact form so
 * part numbers match regardless of punctuation ("QO-120" == "qo120").
 */
export function tokenize(input: string, options: { keepStopwords?: boolean } = {}): string[] {
  const text = normalizeText(input);
  const rawTokens = text.split(/[^a-z0-9/.\-]+/).filter(Boolean);
  const out: string[] = [];
  const push = (tok: string) => {
    const cleaned = tok.replace(/^[.\-/]+|[.\-/]+$/g, "");
    if (!cleaned) return;
    if (!options.keepStopwords && STOPWORDS.has(cleaned)) return;
    out.push(canonical(cleaned));
  };

  for (const raw of rawTokens) {
    const tok = raw.replace(/^[.\-/]+|[.\-/]+$/g, "");
    if (!tok) continue;
    if (MIXED_FRACTION.test(tok) || FRACTION.test(tok)) {
      push(tok);
      continue;
    }
    const parts = tok.split("-").filter(Boolean);
    for (const part of parts) {
      if (MIXED_FRACTION.test(part) || FRACTION.test(part)) {
        push(part);
      } else if (part.includes("/")) {
        for (const sub of part.split("/")) push(sub);
      } else {
        push(part.replace(/\.(?!\d)/g, ""));
      }
    }
    const compact = tok.replace(/[^a-z0-9]/g, "");
    if (parts.length > 1 || tok.includes("/")) {
      if (compact.length >= 3 && compact.length <= 24 && /[a-z]/.test(compact) && /\d/.test(compact)) {
        out.push(compact);
      }
    }
  }
  return out;
}

/** Collapse a part number / SKU to alphanumerics for exact lookups. */
export function compactCode(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/** Size/rating tokens (contain digits) are critical: a mismatch usually means the wrong part. */
export function isSpecToken(token: string): boolean {
  return /\d/.test(token);
}
