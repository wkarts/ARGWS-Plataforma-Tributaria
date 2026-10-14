import assert from "node:assert/strict";
import test from "node:test";
import { canQuery, classifyCredit, formatBRL, statusKind } from "./logic.ts";

test("preserva precisão decimal na visualização e ausência de valores", () => {
  assert.equal(
    formatBRL("12345678901234567890.1234"),
    "R$ 12.345.678.901.234.567.890,1234",
  );
  assert.equal(formatBRL("0"), "R$ 0,00");
  assert.equal(formatBRL(null), "—");
  assert.equal(formatBRL(""), "—");
});

test("não infere crédito presumido de origens ou campos ausentes", () => {
  const record = {
    id: "1",
    query_id: "1",
    resource: "creditos" as const,
    provenance: { origem: "estoque" },
  };
  assert.equal(classifyCredit(record), "unclassified");
  assert.equal(
    classifyCredit({ ...record, credit_category: "presumed_stock" }),
    "presumed_stock",
  );
});

test("respeita capacidade por recurso e ambiente", () => {
  const capabilities = {
    provider_mode: "receita" as const,
    environments: ["fixture", "production"] as ("fixture" | "production")[],
    resources: {
      creditos: { enabled: true, environments: ["production" as const] },
      pagamentos: { enabled: false },
    },
  };
  assert.equal(canQuery(capabilities, "creditos", "production"), true);
  assert.equal(canQuery(capabilities, "creditos", "fixture"), false);
  assert.equal(canQuery(capabilities, "pagamentos", "production"), false);
  assert.equal(canQuery(capabilities, "recolhimentos", "production"), false);
  assert.equal(
    canQuery(
      { ...capabilities, resources: { creditos: {} } },
      "creditos",
      "production",
    ),
    false,
  );
  assert.equal(statusKind("failed"), "error");
});
