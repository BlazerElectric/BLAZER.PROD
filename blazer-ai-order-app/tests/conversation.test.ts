import { expect, it, vi } from "vitest";
import { isMaterialList } from "../lib/order/conversation";

const { runChat } = vi.hoisted(() => ({ runChat: vi.fn() }));
vi.mock("@/lib/ai/gemini", () => ({ runChat, AiNotConfiguredError: class extends Error {} }));
vi.mock("@/lib/ratelimit", () => ({ clientKey: () => "test", rateLimit: () => true }));

it("keeps ordinary lists on deterministic parsing", () => {
  for (const text of ["20 sticks 3/4 emt\n50 3/4 emt couplings", "10 QO120", "romex 12-2 250ft coil x 3", "QO230", "a roll of 14/2 romex", "two boxes of red heads"]) {
    expect(isMaterialList(text), text).toBe(true);
  }
});

it("passes validated existing-line context to the chat service", async () => {
  const { POST } = await import("../app/api/chat/route");
  const messages = [{ role: "user", content: "For line 1, stranded please" }];
  const orderLines = [{ id: "line-1", description: "12 THHN white", quantity: 250, unit: "ft", comment: "5 cuts @ 50ft" }];
  runChat.mockResolvedValueOnce({ reply: "Confirm stranded", suggestions: [] });
  const response = await POST(new Request("http://localhost/api/chat", { method: "POST", body: JSON.stringify({ messages, orderLines }) }));
  expect(response.status).toBe(200);
  expect(runChat).toHaveBeenLastCalledWith(messages, "", orderLines);
});

it("rejects invalid line quantities before calling the AI service", async () => {
  const { POST } = await import("../app/api/chat/route");
  runChat.mockClear();
  const response = await POST(new Request("http://localhost/api/chat", { method: "POST", body: JSON.stringify({
    messages: [{ role: "user", content: "stranded please" }],
    orderLines: [{ id: "line-1", description: "THHN", quantity: -1, unit: "ft", comment: "" }],
  }) }));
  expect(response.status).toBe(400);
  expect(runChat).not.toHaveBeenCalled();
});

it("routes questions, job descriptions and clarification replies to conversation", () => {
  for (const text of ["stranded please", "both white", "What breaker for a 50A EV charger?", "change line 5 to stranded", "I need six outdoor outlets", "20 outlets\nwhich wire do I need?", "no, 2 half inch elbows", "For line 10 (10 QO120), outdoor please"]) {
    expect(isMaterialList(text), text).toBe(false);
  }
});