export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  try {
    // Build the search index at boot so the first request isn't slowed by ~2s of indexing.
    const { getCatalog } = await import("./lib/catalog/server");
    const { totalProducts, buildMs } = getCatalog();
    console.log(`[catalog] indexed ${totalProducts} products in ${buildMs}ms`);
  } catch (error) {
    console.error("[catalog] warm-up failed; will retry on first request", error);
  }
}
