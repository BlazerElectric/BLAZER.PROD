import { describe, expect, it } from "vitest";
import { isLikelyHeading, parseLine, parseOrderText } from "../lib/parser/orderParser";

describe("parseLine", () => {
  it.each([
    ["20 sticks 3/4 emt", 20, "3/4 emt"],
    ["500' #12 THHN blk", 500, "#12 THHN blk"],
    ["QO230 - 4", 4, "QO230"],
    ["(25) 3/4 emt couplings", 25, "3/4 emt couplings"],
    ["romex 12-2 x 3", 3, "romex 12-2"],
  ])("%s", (raw, quantity, query) => {
    const line = parseLine(raw)!;
    expect(line.quantity).toBe(quantity);
    expect(line.query.toLowerCase()).toContain(query.toLowerCase());
  });

  it("does not read wire gauge or trade sizes as quantities", () => {
    expect(parseLine("12 thhn white")!.quantity).toBe(1);
    expect(parseLine("1 1/4 emt connector")!.quantity).toBe(1);
  });

  it("reads spelled-out and implied bulk quantities", () => {
    expect(parseLine("roll of 12/2 romex")).toMatchObject({ quantity: 1, unit: "roll", query: "12/2 romex" });
    expect(parseLine("a roll of romex")).toMatchObject({ quantity: 1, unit: "roll", query: "romex" });
    expect(parseLine("two boxes of wire nuts")).toMatchObject({ quantity: 2, unit: "box", query: "wire nuts" });
    expect(parseLine("three phase breaker")!.quantity).toBe(1);
  });

  it("does not read the x in a word as a quantity marker", () => {
    expect(parseLine("1/2 carflex 90")).toMatchObject({ quantity: 1, query: "1/2 carflex 90" });
    expect(parseLine("QO120 x 4")!.quantity).toBe(4);
  });

  it("reads smart feet marks and keeps Unicode trade sizes", () => {
    expect(parseLine("20’ #12 thhn")).toMatchObject({ quantity: 20, unit: "ft", query: "#12 thhn" });
    expect(parseLine("2 2 ½” MA")).toMatchObject({ quantity: 2, query: '2 1/2" MA' });
    expect(parseLine("2 ½” LB")).toMatchObject({ quantity: 2, query: '1/2" LB' });
  });

  it("flags job names as headings only when unmatched and quantity-free", () => {
    expect(isLikelyHeading(parseLine("Smith residence - rough in")!, "none")).toBe(true);
    expect(isLikelyHeading(parseLine("Smith residence - rough in")!, "medium")).toBe(false);
    expect(isLikelyHeading(parseLine("5 widgets")!, "none")).toBe(false);
    expect(isLikelyHeading(parseLine("quad rs cover")!, "none")).toBe(false);
  });

  it("turns cut notes into comments and footage", () => {
    const line = parseLine("12 thhn white (5 cuts @ 50ft)")!;
    expect(line.comment).toMatch(/5 cuts @ 50ft/);
    expect(line.quantity).toBe(250);
    expect(line.unit).toBe("ft");
  });
});

describe("parseOrderText", () => {
  it("skips headers/context and numbering", () => {
    const lines = parseOrderText("Job: Smith residence\nQTY DESCRIPTION\n1. 10 QO120\n2) 4 4sq box");
    expect(lines.map((l) => l.quantity)).toEqual([10, 4]);
  });

  it("splits long comma-separated material lists without splitting short spec phrases", () => {
    const list = parseOrderText(
      "200A feedthru panel, 30A BR, 2 20A BR, single gang bell box, 20A GFI, in use cover, ½” chase nipple, 20’ #12 thhn",
    );
    expect(list).toHaveLength(8);
    expect(list[2]).toMatchObject({ quantity: 2, query: "20A BR" });
    expect(list[6]).toMatchObject({ query: '1/2" chase nipple' });
    expect(list[7]).toMatchObject({ quantity: 20, unit: "ft", query: "#12 thhn" });

    expect(parseOrderText("20A breaker, 1 pole, 120V, QO series")).toHaveLength(1);
  });
});
