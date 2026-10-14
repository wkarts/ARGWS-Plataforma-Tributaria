import type {
  Capabilities,
  CreditCategory,
  FiscalRecord,
  Query,
  Resource,
} from "./types.ts";

export const RESOURCE_LABELS: Record<Resource, string> = {
  debitos: "Débitos",
  creditos: "Créditos",
  pagamentos: "Pagamentos",
  recolhimentos: "Recolhimentos",
};

const activeStatuses = new Set([
  "queued",
  "opening",
  "awaiting",
  "pending",
  "accepted",
  "submitted",
  "processing",
  "running",
  "waiting",
  "aguardando",
  "processando",
]);
const failureStatuses = new Set([
  "failed",
  "uncertain",
  "error",
  "expired",
  "rejected",
  "falha",
  "erro",
]);
const successStatuses = new Set([
  "completed",
  "done",
  "success",
  "concluido",
  "concluído",
]);

export function statusKind(
  status: string,
): "active" | "error" | "success" | "neutral" {
  const normalized = status.toLocaleLowerCase("pt-BR");
  if (activeStatuses.has(normalized)) return "active";
  if (failureStatuses.has(normalized)) return "error";
  if (successStatuses.has(normalized)) return "success";
  return "neutral";
}

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: "Na fila",
    opening: "Abrindo consulta",
    awaiting: "Aguardando retorno",
    uncertain: "Envio incerto",
    pending: "Pendente",
    accepted: "Aceita",
    submitted: "Enviada",
    processing: "Processando",
    running: "Processando",
    waiting: "Aguardando",
    completed: "Concluída",
    done: "Concluída",
    success: "Concluída",
    failed: "Falhou",
    error: "Erro",
    expired: "Expirada",
    rejected: "Rejeitada",
  };
  return labels[status.toLowerCase()] || status;
}

export function isActive(query: Query): boolean {
  return statusKind(query.status) === "active";
}
export function isFailed(query: Query): boolean {
  return statusKind(query.status) === "error";
}

export function canQuery(
  capabilities: Capabilities | null,
  resource: Resource,
  environment: string,
): boolean {
  if (
    !capabilities ||
    !capabilities.environments.some((item) => item === environment)
  )
    return false;
  const capability = capabilities.resources[resource];
  if (
    !capability ||
    capability.enabled !== true ||
    capability.available === false
  )
    return false;
  if (
    capability.environments &&
    !capability.environments.some((item) => item === environment)
  )
    return false;
  return true;
}

// Formata apenas valores recebidos; nunca calcula totais fiscais com ponto flutuante.
export function formatBRL(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const raw = String(value).trim();
  if (!/^-?\d+(?:\.\d+)?$/.test(raw)) return raw;
  const negative = raw.startsWith("-");
  const [whole, fractional = ""] = (negative ? raw.slice(1) : raw).split(".");
  // A API pode devolver casas subcentavo; mostramos todas sem arredondamento implícito.
  const decimals =
    fractional.length > 2 ? fractional : fractional.padEnd(2, "0");
  const integer = BigInt(whole)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${negative ? "-" : ""}R$ ${integer},${decimals}`;
}

export function classifyCredit(
  record: FiscalRecord,
): CreditCategory | "unclassified" {
  if (
    record.credit_category === "government_reported" ||
    record.credit_category === "presumed_stock"
  )
    return record.credit_category;
  return "unclassified";
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("pt-BR", {
        dateStyle: "short",
        timeStyle: "short",
      }).format(date);
}

export function prettyJson(value: unknown): string {
  if (value === undefined || value === null)
    return "Não disponibilizado pela API.";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2) || "Não disponibilizado pela API.";
}
