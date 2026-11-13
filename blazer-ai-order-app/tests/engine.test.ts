import { describe, expect, it } from "vitest";
import { SearchEngine } from "../lib/search/engine";
import { tokenize } from "../lib/search/normalize";
import { parseSlangCsv } from "../lib/search/slang";
import type { CatalogProduct } from "../lib/types";

const product = (p: Partial<CatalogProduct> & Pick<CatalogProduct, "sku" | "title">): CatalogProduct => ({
  brand: "",
  mpn: "",
  category: "",
  subcategory: "",
  specs: "",
  rank: 999999,
  search_keywords: [],
  common_misspellings: [],
  accessories: [],
  similar: [],
  ...p,
});

const engine = new SearchEngine([
  product({ sku: "1419", title: "1/2 EMT CONDUIT 10FT", category: "Conduit", subcategory: "EMT", rank: 3, search_keywords: ["emt", "thinwall"] }),
  product({ sku: "1420", title: "3/4 EMT CONDUIT 10FT", category: "Conduit", subcategory: "EMT", rank: 5, search_keywords: ["emt", "thinwall"] }),
  product({ sku: "334282", title: "3/4 EMT SET SCREW CONNECTOR", category: "Fittings", rank: 12, search_keywords: ["emt connector"] }),
  product({ sku: "3024", title: "SQD QO120 20A 1P PLUG-ON BREAKER", brand: "Square D", mpn: "QO120", rank: 8, similar: ["3025"] }),
  product({ sku: "3025", title: "SQD QO115 15A 1P PLUG-ON BREAKER", brand: "Square D", mpn: "QO115", rank: 20 }),
  product({ sku: "1021", title: "12/2 NM-B W/G 250FT ROMEX", brand: "Southwire", category: "Wire", rank: 2, search_keywords: ["romex"] }),
  product({ sku: "5000", title: "LEVITON 20A DUPLEX RECEPTACLE WHITE", brand: "Leviton", mpn: "5362-W", rank: 40 }),
]);

describe("normalize", () => {
  it("canonicalizes gauges, fractions and trade shorthand", () => {
    expect(tokenize("#12-2 romex")).toEqual(expect.arrayContaining(["12/2", "nmb"]));
    expect(tokenize('1 1/4" emt')).toContain("1-1/4");
    expect(tokenize("2½” emt")).toContain("2-1/2");
  });
});

describe("SearchEngine", () => {
  it("matches exact SKU and MPN with high confidence", () => {
    expect(engine.match("3024").product?.sku).toBe("3024");
    const byMpn = engine.match("QO-120");
    expect(byMpn.product?.sku).toBe("3024");
    expect(byMpn.confidence).toBe("high");
  });

  it("resolves contractor shorthand via synonyms", () => {
    expect(engine.match("12-2 romex").product?.sku).toBe("1021");
    expect(engine.match("3/4 emt sscr conn").product?.sku).toBe("334282");
  });

  it("tolerates typos", () => {
    expect(engine.search("levitn duplex recepticle")[0]?.product.sku).toBe("5000");
  });

  it("prefers the requested trade size over a more popular size", () => {
    expect(engine.search("3/4 emt conduit")[0]?.product.sku).toBe("1420");
  });

  it("does not match a single receptacle to a duplex request", () => {
    const receptacles = new SearchEngine([
      product({
        sku: "51932",
        title: "PNS 5851-W SINGLE 20A 250V WHITE RECEPTACLE",
        category: "Wiring Devices",
        subcategory: "Receptacles",
        rank: 1,
      }),
      product({
        sku: "61451",
        title: "PNS TR20-W DUPLEX TAMPER RESIST 20A 125V WHITE",
        category: "Wiring Devices",
        subcategory: "Receptacles",
        rank: 999999,
      }),
    ]);

    expect(receptacles.search("20A duplex receptacle white")[0]?.product.sku).toBe("61451");

    const singleOnly = new SearchEngine([
      product({
        sku: "51932",
        title: "PNS 5851-W SINGLE 20A 250V WHITE RECEPTACLE",
        category: "Wiring Devices",
        subcategory: "Receptacles",
        rank: 1,
      }),
    ]);
    expect(singleOnly.match("20A duplex receptacle white")).toMatchObject({
      confidence: "none",
      product: null,
    });
  });

  it("flags multiple package lengths for the same wire part number", () => {
    const wireVariants = new SearchEngine([
      product({
        sku: "1021",
        title: "COP NM-B-12/2-CU-WG 250FT COIL",
        mpn: "NM-B-12/2-CU-WG",
        category: "Wire, Cable & Cord",
        specs: "12 AWG, 2 Conductors, With Ground, 250 Foot Length",
        rank: 1,
      }),
      product({
        sku: "1020",
        title: "COP NM-B-12/2-CU-WG 1000FT SPOOL",
        mpn: "NM-B-12/2-CU-WG",
        category: "Wire, Cable & Cord",
        specs: "12 AWG, 2 Conductors, With Ground, 1000 Foot Spool",
        rank: 1000,
      }),
    ]);

    const ambiguous = wireVariants.match("12/2 romex", { unit: "roll" });
    expect(ambiguous.confidence).toBe("medium");
    expect(ambiguous.note).toMatch(/confirm the coil or spool length/i);
    expect(wireVariants.match("1021").confidence).toBe("high");
  });

  it("uses trade shorthand and does not cross-match adapter gender", () => {
    const adapters = new SearchEngine([
      product({
        sku: "female",
        title: "2-1/2 IN FEMALE ADAPTER SCH 40",
        category: "Fittings",
        subcategory: "PVC Fittings",
        rank: 1,
      }),
      product({
        sku: "male",
        title: "2-1/2 IN MALE ADAPTER SCH 40",
        category: "Fittings",
        subcategory: "PVC Fittings",
        rank: 999999,
      }),
      product({
        sku: "locknut",
        title: "2-1/2 IN CONDUIT LOCKNUT",
        category: "Fittings",
        subcategory: "Bushing / K.O. Seal / Locknuts",
      }),
    ]);

    expect(adapters.search('2 1/2" MA')[0]?.product.sku).toBe("male");
    expect(adapters.search('2 1/2" lockrings')[0]?.product.sku).toBe("locknut");
  });

  it("prefers conduit and conductor products for stick and footage quantities", () => {
    const bulkProducts = new SearchEngine([
      product({
        sku: "conduit",
        title: "2-1/2 SCH 40 PVC CONDUIT 10FT",
        category: "Conduit, Strut & Raceways",
        subcategory: "Conduit - Non-Metallic",
        rank: 1000,
      }),
      product({
        sku: "adapter",
        title: "2-1/2 SCH 40 PVC MALE ADAPTER",
        category: "Fittings",
        subcategory: "PVC Fittings",
        rank: 1,
      }),
      product({
        sku: "wire",
        title: "250 MCM THHN COPPER CONDUCTOR",
        category: "Wire, Cable & Cord",
        subcategory: "Building Wire",
        rank: 1000,
      }),
      product({
        sku: "tap",
        title: "250 MCM COPPER C-TAP",
        category: "Connectors & Crimps",
        subcategory: "Lugs & Split Bolts",
        rank: 1,
      }),
    ]);

    expect(bulkProducts.search('sch 40 2 1/2"', { unit: "stick" })[0]?.product.sku).toBe("conduit");
    expect(bulkProducts.search("250 MCM", { unit: "ft" })[0]?.product.sku).toBe("wire");
  });

  it("requires review when breaker pole count is unspecified", () => {
    const breakers = new SearchEngine([
      product({
        sku: "2999",
        title: "CUT BR230 30A 2P BR BREAKER",
        category: "Power Distribution",
        subcategory: "Circuit Breakers",
        specs: "30 Amp, 2-Pole, 120/240 Volt",
      }),
    ]);

    expect(breakers.match("30A BR")).toMatchObject({
      confidence: "medium",
      note: expect.stringContaining("Confirm breaker pole count"),
    });
    expect(breakers.match("30A 2-pole BR").confidence).toBe("high");
  });

  it("filters feed-through, indoor/outdoor, and cover mismatches", () => {
    const panels = new SearchEngine([
      product({
        sku: "pump",
        title: "200A FUSIBLE PUMP PANEL",
        category: "Control & Automation",
        subcategory: "Starters & Contactors",
      }),
      product({
        sku: "feedthru",
        title: "200A FEED-THROUGH PANEL",
        category: "Power Distribution",
        subcategory: "Load Centers / Panelboards",
        rank: 1000,
      }),
    ]);
    expect(panels.match("200A feedthru panel").product?.sku).toBe("feedthru");

    const meterBreaker = new SearchEngine([
      product({
        sku: "meter-panel",
        title: "200A METER BREAKER WITH FEED-THROUGH LUGS",
        category: "Power Distribution",
        subcategory: "Circuit Breakers",
      }),
    ]);
    expect(meterBreaker.match("200A feedthru panel")).toMatchObject({
      confidence: "medium",
      note: expect.stringContaining("Confirm this meter-breaker assembly"),
    });

    const indoorPanels = new SearchEngine([
      product({
        sku: "outdoor",
        title: "200A 20 SPACE NEMA 3R LOAD CENTER",
        category: "Power Distribution",
        subcategory: "Load Centers / Panelboards",
        rank: 1,
      }),
      product({
        sku: "indoor",
        title: "200A 20 SPACE NEMA 1 LOAD CENTER INDOOR",
        category: "Power Distribution",
        subcategory: "Load Centers / Panelboards",
        rank: 1000,
      }),
    ]);
    expect(indoorPanels.search("200A 20 space interior panel")[0]?.product.sku).toBe("indoor");

    const covers = new SearchEngine([
      product({
        sku: "lamp",
        title: "40W 3500K RS 2G11 LAMP",
        category: "Lamps, Bulbs & Ballast",
        subcategory: "Bulbs - Fluorescent",
        rank: 1,
      }),
      product({
        sku: "cover",
        title: "4SQ QUAD RECEPTACLE COVER",
        category: "Boxes & Enclosures",
        subcategory: "Boxes - Metallic",
        rank: 1000,
      }),
    ]);
    expect(covers.search("quad rs cover")[0]?.product.sku).toBe("cover");
  });

  it("returns no match for unrelated text and exposes similar products", () => {
    expect(engine.match("fiberglass ladder 8ft").confidence).toBe("none");
    expect(engine.match("QO120").similar.map((p) => p.sku)).toEqual(["3025"]);
  });
});

describe("trade slang", () => {
  const slang = parseSlangCsv(
    [
      "slang,search_as,skus,notes",
      "asshole|black asshole,plastic push in connector,766|768,Push-in NM connector",
      "colorado jim|cj,cable support strap studs,,Stud cable support",
    ].join("\n"),
  );
  const slangEngine = new SearchEngine(
    [
      product({ sku: "766", title: "ARL NM94 0.37IN PLASTIC PUSH IN CONNECTOR", category: "Fittings", specs: "3/8 Inch", rank: 322 }),
      product({ sku: "768", title: "ARL NM95 0.75IN PLASTIC PUSH IN CONNECTOR", category: "Fittings", specs: "3/4 Inch", rank: 901 }),
      product({ sku: "237384", title: "PHX CSS Cable Support Strap for Wood or Metal Studs", category: "Fittings", rank: 8403 }),
      product({ sku: "2915", title: "RCR50 1/2 ROMEX CONNECTOR", category: "Fittings", rank: 30, search_keywords: ["romex connector"] }),
      product({ sku: "1026", title: "NM-B 14/2 250 COIL", category: "Wire, Cable & Cord", rank: 14, search_keywords: ["romex"] }),
    ],
    { slang },
  );

  it("parses pipe-separated terms and pinned SKUs", () => {
    expect(slang[0]).toMatchObject({ terms: ["asshole", "black asshole"], skus: ["766", "768"] });
  });

  it("translates plural, multi-word slang and explains it", () => {
    const jim = slangEngine.match("50 colorado jims");
    expect(jim.product?.sku).toBe("237384");
    expect(jim.note).toMatch(/colorado jim/);
    expect(slangEngine.match("black assholes").product?.sku).toBe("766");
  });

  it("lets sizes pick between pinned SKUs", () => {
    expect(slangEngine.match("3/4 asshole").product?.sku).toBe("768");
  });

  it("does not rewrite slang inside other words", () => {
    expect(slangEngine.slang.rewrite("cjx100").applied).toHaveLength(0);
  });

  it("prefers cable over connectors for rolls and flags a missing gauge", () => {
    const roll = slangEngine.match("romex", { unit: "roll" });
    expect(roll.product?.sku).toBe("1026");
    expect(roll.confidence).toBe("medium");
    expect(roll.note).toMatch(/size/);
  });
});
