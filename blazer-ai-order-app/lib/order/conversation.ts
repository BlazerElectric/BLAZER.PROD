export function isMaterialList(text: string): boolean {
  const lines = text.trim().split(/\n/).filter((line) => line.trim());
  if (/^\s*for line\s+\d+/i.test(text)) return false;
  if (/^\s*(?:a|an|one|two|three|four|five|six|ten)\s+(?:roll|coil|spool|reel|stick|box|boxes|bag|jar|pack)\b/i.test(text) && !/\?|\b(?:what|which|should|could|please|need|change|update|instead)\b/i.test(text)) return true;
  const question = /\?|\b(?:what|which|should|could|please|need|recommend|instead|change|update|make|replace|remove|add|both|yes|no|those)\b|\b(?:can|do|does|is|are)\s+(?:you|i|it|this|that|the|these)\b/i;
  if (question.test(text)) return false;
  return lines.some((line) => /^\s*(?:\(?\d|#\d)|\b\d+(?:ft|a|v|\s*(?:roll|coil|stick))\b|\b[A-Z]{1,6}\d{2,}/i.test(line));
}