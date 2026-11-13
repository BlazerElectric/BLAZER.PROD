import { describe, expect, it } from "vitest";
import { buildSubmitPayload, formatQuickPad, isBridgeMessage, sanitizeText } from "../lib/billtrust/protocol";

describe("billtrust protocol", () => {
  it("merges duplicate SKUs, drops invalid ones and joins comments", () => {
    const payload = buildSubmitPayload(
      [
        { sku: "1021", quantity: 2, comment: "5 cuts @ 50ft" },
        { sku: "1021", quantity: 1, comment: "tag for unit 3" },
        { sku: "<script>", quantity: 1, comment: "" },
        { sku: null, quantity: 4, comment: "" },
        { sku: "3024", quantity: 0, comment: "" },
      ],
      "Deliver to\r\nback gate",
    );
    expect(payload.items).toEqual([{ sku: "1021", quantity: 3 }]);
    expect(payload.lineComments).toEqual([{ sku: "1021", comment: "5 cuts @ 50ft | tag for unit 3" }]);
    expect(payload.shippingInstructions).toBe("Deliver to\nback gate");
  });

  it("sanitizes markup and clamps length", () => {
    expect(sanitizeText("<b>hi</b>\u0007  there", 7)).toBe("bhi/b t");
  });

  it("formats QuickPad lines", () => {
    expect(formatQuickPad([{ sku: "1021", quantity: 3 }, { sku: "3024", quantity: 1 }])).toBe("1021,3\n3024,1");
  });

  it("only accepts bridge messages with the right source and version", () => {
    expect(isBridgeMessage({ source: "billtrust-bridge", version: 1, type: "READY" })).toBe(true);
    expect(isBridgeMessage({ source: "evil", version: 1, type: "READY" })).toBe(false);
  });
});
