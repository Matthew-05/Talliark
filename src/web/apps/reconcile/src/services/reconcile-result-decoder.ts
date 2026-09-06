import type { ReconcileModel } from "../types/index.js";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Decode the contract-owned gzip envelope and reject a payload from another model. */
export async function decodeReconcileResult(base64: string): Promise<ReconcileModel> {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index++) {
    bytes[index] = binary.charCodeAt(index);
  }

  const stream = new Blob([bytes.buffer as ArrayBuffer])
    .stream()
    .pipeThrough(new DecompressionStream("gzip"));
  const value: unknown = JSON.parse(await new Response(stream).text());

  if (
    !isRecord(value) ||
    value.version !== 1 ||
    value.coordinateSpace !== "normalized" ||
    !isRecord(value.source) ||
    !isRecord(value.summary) ||
    !Array.isArray(value.tables) ||
    !Array.isArray(value.findings)
  ) {
    throw new Error("Unsupported Reconcile result");
  }

  return value as unknown as ReconcileModel;
}
