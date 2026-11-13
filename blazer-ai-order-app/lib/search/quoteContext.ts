import type { ParsedLine } from "../types";
import { isQuoteSection } from "../parser/orderParser";
import { isSpecToken, tokenize } from "./normalize";

export const MATERIAL_TOKENS = new Set(["pvc", "emt", "rigid", "ent", "mc", "nmb", "fmc"]);
const CONTEXT_DISTANCE = 5;

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

export function inferAdapterMaterialContext(
  lines: ParsedLine[],
  targetIndex: number,
): QuoteMaterialContext | undefined {
  const targetTokens = tokenize(lines[targetIndex]?.query ?? "");
  const adapterRequest = targetTokens.includes("maleadapter") || targetTokens.includes("femaleadapter");
  if (!adapterRequest || targetTokens.some((token) => MATERIAL_TOKENS.has(token))) return undefined;

  const sizes = targetTokens.filter(isSpecToken);
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
  if (!winner || winner[1].score < 0.4 || (runnerUp && winner[1].score < runnerUp[1].score * 1.5)) {
    return undefined;
  }
  return { material: winner[0], tradeSize: winner[1].tradeSize };
}
