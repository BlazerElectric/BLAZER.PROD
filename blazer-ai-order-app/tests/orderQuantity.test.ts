import { describe, expect, it } from "vitest";
import { quantityInCatalogBoxes, quantityInCatalogPackages } from "../lib/order/quantity";

describe("quantityInCatalogPackages", () => {
  it("converts feet to catalog spool and stock-length quantities", () => {
    expect(quantityInCatalogPackages(500, "ft", {
      category: "Wire, Cable & Cord",
      subcategory: "Building Wire",
      title: "12 THHN 500S/R",
      specs: "500 Foot Spool",
    })).toEqual({ quantity: 1, packageLength: 500, excessLength: 0, assumedLength: false });

    expect(quantityInCatalogPackages(100, "ft", {
      category: "Conduit, Strut & Raceways",
      subcategory: "Conduit - Metallic",
      title: "3/4 EMT CONDUIT",
      specs: "",
    })).toEqual({ quantity: 10, packageLength: 10, excessLength: 0, assumedLength: true });
  });

  it("rounds up partial stock lengths and reports the extra footage", () => {
    expect(quantityInCatalogPackages(25, "ft", {
      category: "Conduit, Strut & Raceways",
      subcategory: "Strut",
      title: "Slotted channel 10FT",
      specs: "",
    })).toEqual({ quantity: 3, packageLength: 10, excessLength: 5, assumedLength: false });
  });

  it("does not convert piece counts or products without a catalog stock length", () => {
    expect(quantityInCatalogPackages(2, "ea", {
      category: "Wire, Cable & Cord",
      subcategory: "Building Wire",
      title: "12 THHN 500S/R",
      specs: "500 Foot Spool",
    })).toBeUndefined();
    expect(quantityInCatalogPackages(25, "ft", {
      category: "Fittings",
      subcategory: "EMT Fittings",
      title: "3/4 EMT Connector",
      specs: "",
    })).toBeUndefined();
  });

  it("converts catalog box piece counts to whole boxes", () => {
    expect(quantityInCatalogBoxes(100, "ea", {
      title: "3/8 FENDER WASHER SOLD BY THE BOX (100CT)",
      specs: "",
    })).toEqual({ quantity: 1, piecesPerBox: 100, excessPieces: 0 });
    expect(quantityInCatalogBoxes(100, "ea", {
      title: "3/8 TOGGLE BOLT SOLD BY THE BOX (25 CT)",
      specs: "",
    })).toEqual({ quantity: 4, piecesPerBox: 25, excessPieces: 0 });
    expect(quantityInCatalogBoxes(1, "ea", {
      title: "1/4 SCREW",
      specs: "",
    })).toBeUndefined();
  });
});
