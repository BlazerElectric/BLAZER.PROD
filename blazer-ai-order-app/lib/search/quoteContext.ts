import type { ParsedLine } from "../types";
import { isQuoteSection } from "../parser/orderParser";
import { isSpecToken, tokenize } from "./normalize";

export const MATERIAL_TOKENS = new Set(["pvc", "emt", "rigid", "ent", "mc", "nmb", "fmc", "steel"]);
const CONTEXT_DISTANCE = 5;
const PANEL_FAMILIES = new Set(["homeline", "qo", "br"]);
const EXPLICIT_BREAKER_BRANDS = new Set(["eaton", "siemens", "ge", "sqd", "square", "cutler", "hammer"]);

export interface QuoteMaterialContext {
  material: string;
  tradeSize: string;
}

export function splitQuoteSections(lines: ParsedLine[]): {
  sections: ParsedLine[][];
  headings: ParsedLine[];
} {
  const sections: ParsedLine[][] = [];
  const headings: ParsedLine[] = [];
  let current: ParsedLine[] = [];

  for (const line of lines) {
    if (isQuoteSection(line)) {
      headings.push(line);
      if (current.length) sections.push(current);
      current = [];
      continue;
    }
    current.push(line);
  }

  if (current.length) sections.push(current);
  return { sections, headings };
}

export function inferFittingMaterialContext(
  lines: ParsedLine[],
  targetIndex: number,
): QuoteMaterialContext | undefined {
  const targetTokens = tokenize(lines[targetIndex]?.query ?? "");
  const fittingRequest = targetTokens.some((token) => ["maleadapter", "femaleadapter", "strap", "connector", "coupling"].includes(token));
  if (!fittingRequest || targetTokens.some((token) => MATERIAL_TOKENS.has(token))) return undefined;

  const sizes = targetTokens.filter(isSpecToken).slice(0, 1);
  if (sizes.length === 0) return undefined;

  const evidence = new Map<string, { score: number; tradeSize: string }>();
  for (const [index, line] of lines.entries()) {
    const distance = Math.abs(index - targetIndex);
    if (distance === 0 || distance > CONTEXT_DISTANCE) continue;

    const tokens = tokenize(line.query);
    const tradeSize = sizes.find((size) => tokens.includes(size));
    if (!tradeSize) continue;

    for (const material of tokens.filter((token) => MATERIAL_TOKENS.has(token))) {
      const current = evidence.get(material) ?? { score: 0, tradeSize };
      current.score += 1 / distance;
      evidence.set(material, current);
    }
  }

  const ranked = [...evidence.entries()].sort((a, b) => b[1].score - a[1].score);
  const winner = ranked[0];
  const runnerUp = ranked[1];
  if (!winner) {
    const sectionMaterials = new Set(
      lines
        .filter((line) => sizes.some((size) => tokenize(line.query).includes(size)))
        .flatMap((line) => tokenize(line.query).filter((token) => MATERIAL_TOKENS.has(token))),
    );
    if (sectionMaterials.size === 1) return { material: [...sectionMaterials][0], tradeSize: sizes[0] };
  }
  if (!winner || winner[1].score < 0.4 || (runnerUp && winner[1].score < runnerUp[1].score * 1.5)) {
    return undefined;
  }
  return { material: winner[0], tradeSize: winner[1].tradeSize };
}

export function inferBreakerFamilyContext(lines: ParsedLine[], targetIndex: number): string | undefined {
  const tokens = tokenize(lines[targetIndex]?.query ?? "");
  if (!tokens.includes("breaker") ||
      tokens.some((token) => PANEL_FAMILIES.has(token) || EXPLICIT_BREAKER_BRANDS.has(token))) {
    return undefined;
  }
  const families = new Set(
    lines
      .filter((line) => /\b(?:panel|load ?center)\b/i.test(line.query))
      .flatMap((line) => tokenize(line.query).filter((token) => PANEL_FAMILIES.has(token))),
  );
  return families.size === 1 ? [...families][0] : undefined;
}
