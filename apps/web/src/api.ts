import type {
  AssistedPreview,
  Capabilities,
  CommercialInstallment,
  Company,
  Coverage,
  Environment,
  FiscalDocument,
  FiscalRecord,
  IssuedAgent,
  InstallationBrand,
  InstallationBrandInput,
  ListResponse,
  LocalAgent,
  Query,
  Reconciliation,
  Resource,
  StockCreditClaim,
  User,
} from "./types.ts";

declare global {
  interface Window {
    __APP_CONFIG__?: { apiBaseUrl?: string };
  }
}

const configuredBase =
  window.__APP_CONFIG__?.apiBaseUrl ||
  import.meta.env.VITE_API_BASE_URL ||
  "/api/v1";
const base = configuredBase.replace(/\/+$/, "");

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

function errorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === "object") {
    const value = payload as Record<string, unknown>;
    const detail = value.detail || value.message || value.error;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail
        .map((item) => {
          if (typeof item === "object" && item !== null && "msg" in item)
            return String(item.msg);
          return String(item);
        })
        .join("; ");
  }
  return status === 401
    ? "Sua sessão expirou. Entre novamente."
    : `A API respondeu com HTTP ${status}.`;
}

export class ApiClient {
  private readonly token: () => string | null;
  private readonly onUnauthorized: () => void;
  constructor(token: () => string | null, onUnauthorized: () => void) {
    this.token = token;
    this.onUnauthorized = onUnauthorized;
  }

  private async request<T>(
    path: string,
    options: RequestInit = {},
    authenticated = true,
  ): Promise<T> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    try {
      const headers = new Headers(options.headers);
      headers.set("Accept", "application/json");
      if (options.body && !(options.body instanceof FormData))
        headers.set("Content-Type", "application/json");
      if (authenticated && this.token())
        headers.set("Authorization", `Bearer ${this.token()}`);
      const response = await fetch(`${base}${path}`, {
        ...options,
        headers,
        signal: controller.signal,
        cache: "no-store",
        credentials: "same-origin",
      });
      const text = await response.text();
      let payload: unknown;
      try {
        payload = text ? JSON.parse(text) : null;
      } catch {
        payload = null;
      }
      if (!response.ok) {
        if (response.status === 401 && authenticated) this.onUnauthorized();
        throw new ApiError(
          errorMessage(payload, response.status),
          response.status,
        );
      }
      return payload as T;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error instanceof DOMException && error.name === "AbortError")
        throw new ApiError(
          "A consulta à API excedeu 30 segundos. Verifique o estado antes de reenviar.",
          0,
        );
      throw new ApiError(
        "Não foi possível conectar à API. Confira a conexão e a configuração do serviço.",
        0,
      );
    } finally {
      window.clearTimeout(timeout);
    }
  }

  login(username: string, password: string) {
    return this.request<{ access_token: string; token_type: string }>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
      false,
    );
  }

  me() {
    return this.request<User>("/auth/me");
  }
  installationBrand() {
    return this.request<InstallationBrand>("/installation/branding", {}, false);
  }
  saveInstallationBrand(payload: InstallationBrandInput, logo?: File | null) {
    const form = new FormData();
    form.set("payload", JSON.stringify(payload));
    if (logo) form.set("logo", logo);
    return this.request<InstallationBrand>("/installation/branding", { method: "PUT", body: form });
  }
  capabilities() {
    return this.request<Capabilities>("/capabilities");
  }
  coverage(companyId: string, environment: Environment) {
    const params = new URLSearchParams({ company_id: companyId, environment });
    return this.request<Coverage>(`/coverage?${params.toString()}`);
  }
  companies() {
    return this.request<ListResponse<Company>>("/companies");
  }
  createCompany(name: string, cnpj: string) {
    return this.request<Company>("/companies", {
      method: "POST",
      body: JSON.stringify({ name, cnpj }),
    });
  }
  queries(companyId: string, cursor?: string) {
    const params = new URLSearchParams({ company_id: companyId });
    if (cursor) params.set("cursor", cursor);
    return this.request<ListResponse<Query>>(`/queries?${params.toString()}`);
  }
  query(queryId: string) {
    return this.request<Query>(`/queries/${encodeURIComponent(queryId)}`);
  }
  resolveQuery(
    queryId: string,
    action: "attach_ticket" | "mark_failed",
    note: string,
    ticket?: string,
  ) {
    return this.request<Query>(
      `/queries/${encodeURIComponent(queryId)}/resolve`,
      {
        method: "POST",
        body: JSON.stringify({
          action,
          note,
          ...(action === "attach_ticket" ? { ticket } : {}),
        }),
      },
    );
  }
  createQuery(
    companyId: string,
    resource: Resource,
    environment: Environment,
    idempotencyKey: string,
  ) {
    return this.request<Query>("/queries", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ company_id: companyId, resource, environment }),
    });
  }
  records(
    companyId: string,
    resource?: Resource,
    cursor?: string,
    queryId?: string,
  ) {
    const params = new URLSearchParams({ company_id: companyId });
    if (resource) params.set("resource", resource);
    if (cursor) params.set("cursor", cursor);
    if (queryId) params.set("query_id", queryId);
    return this.request<ListResponse<FiscalRecord>>(
      `/records?${params.toString()}`,
    );
  }

  documents(companyId: string, cursor?: string) {
    const params = new URLSearchParams({ company_id: companyId });
    if (cursor) params.set("cursor", cursor);
    return this.request<ListResponse<FiscalDocument>>(
      `/documents?${params.toString()}`,
    );
  }

  agents(companyId: string) {
    const params = new URLSearchParams({ company_id: companyId });
    return this.request<ListResponse<LocalAgent>>(`/agents?${params.toString()}`);
  }

  createAgent(companyId: string, name: string) {
    return this.request<IssuedAgent>("/agents", {
      method: "POST",
      body: JSON.stringify({ company_id: companyId, name }),
    });
  }

  revokeAgent(agentId: string) {
    return this.request<null>(`/agents/${encodeURIComponent(agentId)}`, {
      method: "DELETE",
    });
  }

  reconciliation(companyId: string, period: string) {
    const params = new URLSearchParams({ company_id: companyId, period });
    return this.request<Reconciliation>(`/reconciliation?${params.toString()}`);
  }

  assistedPreview(companyId: string, period: string) {
    const params = new URLSearchParams({ company_id: companyId, period });
    return this.request<AssistedPreview>(`/assisted-preview?${params.toString()}`);
  }

  stockCreditClaims(companyId: string, cursor?: string) {
    const params = new URLSearchParams({ company_id: companyId });
    if (cursor) params.set("cursor", cursor);
    return this.request<ListResponse<StockCreditClaim>>(`/stock-credit-claims?${params.toString()}`);
  }

  createStockCreditClaim(companyId: string, inventoryDate: string, expectedAmount: string, evidenceRef: string) {
    return this.request<StockCreditClaim>("/stock-credit-claims", {
      method: "POST",
      body: JSON.stringify({
        company_id: companyId,
        inventory_date: inventoryDate,
        expected_amount: expectedAmount,
        evidence_ref: evidenceRef,
      }),
    });
  }

  commercialInstallments(companyId: string, cursor?: string) {
    const params = new URLSearchParams({ company_id: companyId });
    if (cursor) params.set("cursor", cursor);
    return this.request<ListResponse<CommercialInstallment>>(`/commercial-installments?${params.toString()}`);
  }

  createCommercialInstallment(companyId: string, documentRef: string, installmentNo: number, amount: string, paidAt: string | null) {
    return this.request<CommercialInstallment>("/commercial-installments", {
      method: "POST",
      body: JSON.stringify({
        company_id: companyId,
        document_ref: documentRef,
        installment_no: installmentNo,
        amount,
        paid_at: paidAt,
      }),
    });
  }

  async documentRaw(documentId: string, sha256: string): Promise<Blob> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 120000);
    try {
      const response = await fetch(
        `${base}/documents/${encodeURIComponent(documentId)}/raw`,
        {
          headers: { Authorization: `Bearer ${this.token() || ""}` },
          cache: "no-store",
          credentials: "same-origin",
          signal: controller.signal,
        },
      );
      if (!response.ok) {
        if (response.status === 401) this.onUnauthorized();
        let payload: unknown = null;
        try {
          payload = await response.json();
        } catch {
          /* Resposta de erro sem JSON. */
        }
        throw new ApiError(errorMessage(payload, response.status), response.status);
      }
      const expected = response.headers.get("X-Content-SHA256") || sha256;
      if (expected.toLowerCase() !== sha256.toLowerCase())
        throw new ApiError("O hash anunciado pela API diverge do XML registrado.", 0);
      const bytes = await response.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const actual = [...new Uint8Array(digest)]
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
      if (actual !== sha256.toLowerCase())
        throw new ApiError("O XML falhou na conferência SHA-256. Arquivo descartado.", 0);
      return new Blob([bytes], { type: "application/xml" });
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error instanceof DOMException && error.name === "AbortError")
        throw new ApiError("O download do XML excedeu dois minutos.", 0);
      throw new ApiError("Não foi possível baixar e verificar o XML.", 0);
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async raw(queryId: string, sha256: string): Promise<Blob> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 120000);
    try {
      const response = await fetch(
        `${base}/queries/${encodeURIComponent(queryId)}/raw`,
        {
          headers: { Authorization: `Bearer ${this.token() || ""}` },
          cache: "no-store",
          credentials: "same-origin",
          signal: controller.signal,
        },
      );
      if (!response.ok) {
        if (response.status === 401) this.onUnauthorized();
        let payload: unknown = null;
        try {
          payload = await response.json();
        } catch {
          /* Sem mensagem JSON. */
        }
        throw new ApiError(
          errorMessage(payload, response.status),
          response.status,
        );
      }
      const expected = response.headers.get("X-Content-SHA256") || sha256;
      if (expected.toLowerCase() !== sha256.toLowerCase())
        throw new ApiError(
          "O hash anunciado pela API diverge do registro da consulta.",
          0,
        );
      const bytes = await response.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const actual = [...new Uint8Array(digest)]
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
      if (actual !== sha256.toLowerCase())
        throw new ApiError(
          "O retorno bruto falhou na conferência SHA-256. Arquivo descartado.",
          0,
        );
      return new Blob([bytes], { type: "application/json" });
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error instanceof DOMException && error.name === "AbortError")
        throw new ApiError("O download excedeu dois minutos.", 0);
      throw new ApiError(
        "Não foi possível baixar e verificar o retorno bruto.",
        0,
      );
    } finally {
      window.clearTimeout(timeout);
    }
  }
}
