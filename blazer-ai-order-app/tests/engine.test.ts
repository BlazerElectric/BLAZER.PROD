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
