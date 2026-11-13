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

  it("recognizes quote titles and numbered quote separators even if they match catalog text", () => {
    expect(isLikelyHeading(parseLine("Flying Diamond Quotes")!, "high")).toBe(true);
    expect(isLikelyHeading(parseLine("QUOTE 1:")!, "medium")).toBe(true);
    expect(isLikelyHeading(parseLine("QUOTE #3:")!, "high")).toBe(true);
    expect(isLikelyHeading(parseLine("20A receptacles")!, "high")).toBe(false);
  });

  it("turns cut notes into comments and footage", () => {
    const line = parseLine("12 thhn white (5 cuts @ 50ft)")!;
    expect(line.comment).toMatch(/5 cuts @ 50ft/);
    expect(line.quantity).toBe(250);
    expect(line.unit).toBe("ft");
  });
});

describe("parseOrderText", () => {
  it("keeps paired fitting packages distinct and applies an explicit each quantity to the second size", () => {
    const lines = parseOrderText(
      "give me a box of 3/4 ss connectors and a box of couplings, " +
      "same thing for the 1in ss conn and coup (need 25 each). throw in 20 3/4 one hole straps",
    );
    expect(lines.slice(0, 4).map((line) => [line.quantity, line.unit, line.query])).toEqual([
      [1, "box", "3/4 ss connectors"],
      [1, "box", "3/4 ss couplings"],
      [25, "ea", "1in ss conn"],
      [25, "ea", "1in ss couplings"],
    ]);
    expect(lines[4].quantity).toBe(20);
  });

  it("expands wire color lists with per-color quantities and does not lose stranding", () => {
    const lines = parseOrderText(
      "for wire pull i need 500ft spools of #10 thhn solid - black, red, white, green, " +
      "and 500ft of #8 thhn stranded blk, red, wht, grn. throw in 1 emt coupling",
    );
    expect(lines.slice(0, 4).map((line) => [line.quantity, line.unit, line.query])).toEqual([
      [1, "spool", "#10 thhn solid black 500ft"],
      [1, "spool", "#10 thhn solid red 500ft"],
      [1, "spool", "#10 thhn solid white 500ft"],
      [1, "spool", "#10 thhn solid green 500ft"],
    ]);
    expect(lines.slice(4, 8).map((line) => [line.quantity, line.unit, line.query])).toEqual([
      [500, "ft", "#8 thhn stranded blk"],
      [500, "ft", "#8 thhn stranded red"],
      [500, "ft", "#8 thhn stranded wht"],
      [500, "ft", "#8 thhn stranded grn"],
    ]);
    expect(lines.slice(0, 8).every((line) => line.reviewNote?.includes("each listed color"))).toBe(true);
  });

  it("preserves coil length and container contents without treating them as ordered container quantities", () => {
    const lines = parseOrderText(
      "throw in a 250ft coil of 12/2 mc lite and a 250ft roll of 12/3 mc, " +
      "with a jar of 100 mc snap-in connectors and a bag of redheads",
    );
    expect(lines.map((line) => [line.quantity, line.unit, line.query])).toEqual([
      [1, "coil", "12/2 mc lite 250ft"],
      [1, "roll", "12/3 mc 250ft"],
      [1, "jar", "mc snap-in connectors"],
      [1, "bag", "redheads"],
    ]);
    expect(lines[2].comment).toContain("100 pieces");
  });

  it("extracts exact breaker quantities and holds motor assemblies without interpreting FLA as an item", () => {
    expect(parseLine("for the panel we got an eaton prl1a 208v so send 6 bab1020")).toMatchObject({
      quantity: 6, query: "bab1020",
    });
    const lines = parseOrderText(
      "oh and a 30a 3ph motor starter nema 1 with 120v coil and overload for a 5hp motor at 208v (around 16.7 fla). " +
      "add 2 cans of cold galv spray, a roll of phasing tape pack, and 2 tubes of fire caulk",
    );
    expect(lines).toHaveLength(4);
    expect(lines[0].needsClarification).toBe(true);
    expect(lines[0].comment).toContain("16.7 fla");
    expect(lines.slice(1).map((line) => [line.quantity, line.unit])).toEqual([[2, "can"], [1, "roll"], [2, "tube"]]);
    expect(lines[2].needsClarification).toBe(true);
  });

  it("separates conversational breaker quantities, wire footage and shared fitting quantities", () => {
    const lines = parseOrderText(
      "throw in 10 20a single pole and 4 15a breakers, also need 2 30a 2pole and a 50a 2p. " +
      "for the riser gimme 30ft of 2/0 thhn copper black and 15ft white. " +
      "2in locknuts and plastic bushings (x4)",
    );
    expect(lines.map((line) => [line.quantity, line.unit, line.query])).toEqual([
      [10, "ea", "20a single pole breaker"],
      [4, "ea", "15a breakers single pole"],
      [2, "ea", "30a 2pole breaker"],
      [1, "ea", "a 50a 2p breaker"],
      [30, "ft", "2/0 thhn copper black"],
      [15, "ft", "2/0 thhn copper white"],
      [4, "ea", "2in locknuts"],
      [4, "ea", "2in plastic bushings"],
    ]);
    expect(lines[5].reviewNote).toMatch(/carried from preceding footage/);
    expect(lines[7].reviewNote).toMatch(/grouped/);
  });

  it("holds contradictory cable revisions instead of ordering both old and new cable", () => {
    const lines = parseOrderText(
      "need 25ft of 2/0 copper ser or actually make it 4/0 al seu just 3 wire wait no 4 wire for the subpanel feed.",
    );
    expect(lines).toHaveLength(1);
    expect(lines[0]).toMatchObject({ quantity: 25, unit: "ft", needsClarification: true });
    expect(lines[0].reviewNote).toMatch(/confirm final cable type/);
    const punctuated = parseOrderText("gimme 25ft 2/0 SER, or actually make it 4/0 SEU, wait no 4 wire.");
    expect(punctuated).toHaveLength(1);
    expect(punctuated[0].needsClarification).toBe(true);
  });

  it("keeps comma-separated thousands intact while splitting conversational footage", () => {
    const lines = parseOrderText("gimme 1,000ft #12 thhn black and 500ft white");
    expect(lines.map((line) => [line.quantity, line.unit, line.query])).toEqual([
      [1000, "ft", "#12 thhn black"], [500, "ft", "#12 thhn white"],
    ]);
  });

  it("keeps stock length separate from quantity and warns about package and stock requests", () => {
    expect(parseLine("2in rigid mast 10ft stick")).toMatchObject({ quantity: 1, query: "2in rigid conduit" });
    expect(parseLine("1/4x1-1/4 tapcons box of 100")).toMatchObject({ quantity: 1, unit: "box" });
    expect(parseLine("let me know if u have the 200a whole house surge spd for homeline in stock too")?.reviewNote)
      .toMatch(/does not verify stock/);
    const rods = parseOrderText("gimme two 8ft copper ground rods with acorns");
    expect(rods.map((line) => [line.quantity, line.query])).toEqual([
      [2, "8ft copper ground rods"], [2, "acorns"],
    ]);
  });

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
