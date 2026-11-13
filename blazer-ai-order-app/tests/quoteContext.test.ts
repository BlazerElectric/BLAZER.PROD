import { describe, expect, it } from "vitest";
import { parseOrderText } from "../lib/parser/orderParser";
import { inferFittingMaterialContext, inferBreakerFamilyContext, splitQuoteSections } from "../lib/search/quoteContext";

describe("quote material context", () => {
  it("carries a unique panel family to breakers without overriding explicit brands or mixed families", () => {
    const lines = parseOrderText("200A Homeline panel\n4 - 15A breakers\n1 - 20A Eaton breaker");
    expect(inferBreakerFamilyContext(lines, 1)).toBe("homeline");
    expect(inferBreakerFamilyContext(lines, 2)).toBeUndefined();
    expect(inferBreakerFamilyContext([...lines, ...parseOrderText("100A QO panel")], 1)).toBeUndefined();
  });

  it("uses a unique same-size raceway elsewhere in the quote for a strap without borrowing another quote", () => {
    const lines = parseOrderText(
      "QUOTE 1:\n200ft 3/4 EMT\n1 widget\n1 widget\n1 widget\n1 widget\n1 widget\n1 widget\n20 3/4 one hole straps\nQUOTE 2:\n20 3/4 one hole straps",
    );
    const { sections } = splitQuoteSections(lines);
    expect(inferFittingMaterialContext(sections[0], 7)).toEqual({ material: "emt", tradeSize: "3/4" });
    expect(inferFittingMaterialContext(sections[1], 0)).toBeUndefined();
  });
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
    expect(inferFittingMaterialContext(sections[0], 4)).toEqual({ material: "pvc", tradeSize: "3/4" });
    expect(inferFittingMaterialContext(sections[1], 1)).toEqual({ material: "emt", tradeSize: "3/4" });
  });

  it("does not infer material when the item already states it or has no matching size evidence", () => {
    const explicit = parseOrderText("1 - 3/4 PVC male adapter");
    const mismatched = parseOrderText("1 - 3/4 EMT connector\n1 - 1/2 MA");

    expect(inferFittingMaterialContext(explicit, 0)).toBeUndefined();
    expect(inferFittingMaterialContext(mismatched, 1)).toBeUndefined();
  });
});
