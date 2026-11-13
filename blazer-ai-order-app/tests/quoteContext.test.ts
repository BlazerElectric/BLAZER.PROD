import { describe, expect, it } from "vitest";
import { parseOrderText } from "../lib/parser/orderParser";
import { inferAdapterMaterialContext, splitQuoteSections } from "../lib/search/quoteContext";

describe("quote material context", () => {
  it("uses nearby same-size evidence and keeps context inside quote headings", () => {
    const parsed = parseOrderText(
      [
        "QUOTE 1:",
        "1 - 3/4 EMT connector",
        "1 - 3/4 EMT coupling",
        "1 - 3/4 FA PVC",
        "100' - 3/4 PVC sch 40",
        "6 - 3/4 MA",
        "QUOTE 2:",
        "1 - 3/4 EMT connector",
        "6 - 3/4 MA",
      ].join("\n"),
    );
    const { sections, headings } = splitQuoteSections(parsed);

    expect(headings).toHaveLength(2);
    expect(sections).toHaveLength(2);
    expect(inferAdapterMaterialContext(sections[0], 4)).toEqual({ material: "pvc", tradeSize: "3/4" });
    expect(inferAdapterMaterialContext(sections[1], 1)).toEqual({ material: "emt", tradeSize: "3/4" });
  });

  it("does not infer material when the item already states it or has no matching size evidence", () => {
    const explicit = parseOrderText("1 - 3/4 PVC male adapter");
    const mismatched = parseOrderText("1 - 3/4 EMT connector\n1 - 1/2 MA");

    expect(inferAdapterMaterialContext(explicit, 0)).toBeUndefined();
    expect(inferAdapterMaterialContext(mismatched, 1)).toBeUndefined();
  });
});
