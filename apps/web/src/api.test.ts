import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

Object.assign(globalThis, {
  window: {
    __RT_APP_CONFIG__: { apiBaseUrl: "/api/v1" },
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  },
});
const { ApiClient, ApiError } = await import("./api.ts");

test("envia token, chave de idempotência e payload da consulta incremental", async () => {
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async (input, options) => {
    calls++;
    assert.equal(input, "/api/v1/queries");
    assert.equal(options?.method, "POST");
    assert.equal(
      new Headers(options?.headers).get("Authorization"),
      "Bearer token-teste",
    );
    assert.equal(
      new Headers(options?.headers).get("Idempotency-Key"),
      "idempotencia-teste-123",
    );
    assert.equal(options?.cache, "no-store");
    assert.deepEqual(JSON.parse(String(options?.body)), {
      company_id: "empresa-1",
      resource: "creditos",
      environment: "fixture",
    });
    return new Response(
      JSON.stringify({
        id: "consulta-1",
        company_id: "empresa-1",
        resource: "creditos",
        environment: "fixture",
        status: "queued",
      }),
      { status: 202 },
    );
  };
  try {
    const client = new ApiClient(
      () => "token-teste",
      () => {
        throw new Error("401 inesperado");
      },
    );
    const result = await client.createQuery(
      "empresa-1",
      "creditos",
      "fixture",
      "idempotencia-teste-123",
    );
    assert.equal(result.status, "queued");
    assert.equal(calls, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("confere SHA-256 dos bytes antes de liberar download bruto", async () => {
  const originalFetch = globalThis.fetch;
  const data = '{"documentos":[]}';
  const digest = createHash("sha256").update(data).digest("hex");
  globalThis.fetch = async () =>
    new Response(data, {
      status: 200,
      headers: { "X-Content-SHA256": digest },
    });
  try {
    const client = new ApiClient(
      () => "token-teste",
      () => {},
    );
    const blob = await client.raw("consulta-1", digest);
    assert.equal(await blob.text(), data);
    await assert.rejects(
      client.raw("consulta-1", "0".repeat(64)),
      (error: unknown) =>
        error instanceof ApiError && /hash anunciado/.test(error.message),
    );
    globalThis.fetch = async () =>
      new Response(`${data} `, {
        status: 200,
        headers: { "X-Content-SHA256": digest },
      });
    await assert.rejects(
      client.raw("consulta-1", digest),
      (error: unknown) =>
        error instanceof ApiError && /falhou na conferência SHA-256/.test(error.message),
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("opera agentes por empresa com emissão única e revogação autenticada", async () => {
  const originalFetch = globalThis.fetch;
  const calls: { path: string; method: string; body?: unknown }[] = [];
  globalThis.fetch = async (input, options) => {
    const path = String(input);
    const method = options?.method || "GET";
    calls.push({ path, method, body: options?.body ? JSON.parse(String(options.body)) : undefined });
    assert.equal(new Headers(options?.headers).get("Authorization"), "Bearer sessao-admin");
    assert.equal(options?.cache, "no-store");
    if (method === "POST")
      return Response.json({ id: "agente-1", company_id: "empresa-1", name: "Servidor", token: "segredo-uma-vez" }, { status: 201 });
    if (method === "DELETE") return new Response(null, { status: 204 });
    return Response.json({ items: [{ id: "agente-1", company_id: "empresa-1", name: "Servidor" }] });
  };
  try {
    const client = new ApiClient(() => "sessao-admin", () => {});
    const issued = await client.createAgent("empresa-1", "Servidor");
    assert.equal(issued.token, "segredo-uma-vez");
    const list = await client.agents("empresa-1");
    assert.equal(list.items[0]?.name, "Servidor");
    await client.revokeAgent("agente-1");
    assert.deepEqual(calls, [
      { path: "/api/v1/agents", method: "POST", body: { company_id: "empresa-1", name: "Servidor" } },
      { path: "/api/v1/agents?company_id=empresa-1", method: "GET", body: undefined },
      { path: "/api/v1/agents/agente-1", method: "DELETE", body: undefined },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("baixa XML somente após conferir hash e consulta documentos por empresa", async () => {
  const originalFetch = globalThis.fetch;
  const xml = "<nfeProc><NFe/></nfeProc>";
  const digest = createHash("sha256").update(xml).digest("hex");
  globalThis.fetch = async (input) => {
    if (String(input).includes("/documents?")) {
      assert.equal(input, "/api/v1/documents?company_id=empresa-1&cursor=pagina-2");
      return Response.json({ items: [], next_cursor: null });
    }
    assert.equal(input, "/api/v1/documents/documento-1/raw");
    return new Response(xml, { headers: { "X-Content-SHA256": digest } });
  };
  try {
    const client = new ApiClient(() => "sessao", () => {});
    assert.equal((await client.documents("empresa-1", "pagina-2")).items.length, 0);
    assert.equal(await (await client.documentRaw("documento-1", digest)).text(), xml);
    await assert.rejects(
      client.documentRaw("documento-1", "0".repeat(64)),
      (error: unknown) => error instanceof ApiError && /hash anunciado/.test(error.message),
    );
    globalThis.fetch = async () => new Response(`${xml} `, { headers: { "X-Content-SHA256": digest } });
    await assert.rejects(
      client.documentRaw("documento-1", digest),
      (error: unknown) => error instanceof ApiError && /falhou na conferência SHA-256/.test(error.message),
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("consulta conciliação por empresa e competência sem calcular saldos no navegador", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    assert.equal(input, "/api/v1/reconciliation?company_id=empresa-1&period=2026-10");
    return Response.json({
      summary: { total: 1, match: 1, mismatch: 0, missing: 0, ambiguous: 0, unknown: 0 },
      items: [{ document_key: "chave", status: "match", reason: null, source_amount: "10.00", official_amount: "10.00" }],
    });
  };
  try {
    const result = await new ApiClient(() => "sessao", () => {}).reconciliation("empresa-1", "2026-10");
    assert.equal(result.items[0]?.status, "match");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("mantém valores exatos e separa evidência de estoque de parcela comercial", async () => {
  const originalFetch = globalThis.fetch;
  const calls: { path: string; method: string; body: unknown }[] = [];
  globalThis.fetch = async (input, options) => {
    const path = String(input);
    const method = options?.method || "GET";
    const body = options?.body ? JSON.parse(String(options.body)) : null;
    calls.push({ path, method, body });
    assert.equal(new Headers(options?.headers).get("Authorization"), "Bearer operador");
    if (method === "POST" && path.endsWith("stock-credit-claims"))
      return Response.json({ id: "estoque-1", ...body, status: "draft", appropriated_amount: "0.00", used_amount: "0.00", evidence_verified: false }, { status: 201 });
    if (method === "POST") return Response.json({ id: "parcela-1", ...body, category: "commercial_payment" }, { status: 201 });
    return Response.json({ items: [], next_cursor: null });
  };
  try {
    const client = new ApiClient(() => "operador", () => {});
    const claim = await client.createStockCreditClaim("empresa-1", "2026-10-14", "9007199254740993.01", "Inventário A");
    assert.equal(claim.expected_amount, "9007199254740993.01");
    assert.equal(claim.appropriated_amount, "0.00");
    assert.equal((await client.stockCreditClaims("empresa-1", "50")).items.length, 0);
    const installment = await client.createCommercialInstallment("empresa-1", "NF-10", 2, "100.01", null);
    assert.equal(installment.paid_at, null);
    assert.equal((await client.commercialInstallments("empresa-1", "100")).items.length, 0);
    assert.deepEqual(calls, [
      { path: "/api/v1/stock-credit-claims", method: "POST", body: { company_id: "empresa-1", inventory_date: "2026-10-14", expected_amount: "9007199254740993.01", evidence_ref: "Inventário A" } },
      { path: "/api/v1/stock-credit-claims?company_id=empresa-1&cursor=50", method: "GET", body: null },
      { path: "/api/v1/commercial-installments", method: "POST", body: { company_id: "empresa-1", document_ref: "NF-10", installment_no: 2, amount: "100.01", paid_at: null } },
      { path: "/api/v1/commercial-installments?company_id=empresa-1&cursor=100", method: "GET", body: null },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
