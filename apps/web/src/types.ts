export const RESOURCES = [
  "debitos",
  "creditos",
  "pagamentos",
  "recolhimentos",
] as const;
export type Resource = (typeof RESOURCES)[number];
export type Environment = "fixture" | "restricted" | "production";
export type CreditCategory = "government_reported" | "presumed_stock";

export interface User {
  id: string;
  username: string;
  role: string;
  organization_id?: string;
  full_name?: string;
  position?: string;
  active?: boolean;
  company_access_mode?: "all" | "restricted";
  permission_keys?: string[];
  mfa_enabled?: boolean;
  avatar_updated_at?: string | null;
}

export interface ManagedUser extends User {
  group_ids: string[];
  company_ids: string[];
  organization_id: string;
  full_name: string;
  position: string;
  active: boolean;
  company_access_mode: "all" | "restricted";
  permission_keys: string[];
  mfa_enabled: boolean;
}

export interface AccessGroup {
  id: string;
  name: string;
  description: string;
  permission_keys: string[];
  company_ids: string[];
  user_ids: string[];
}

export interface MailStatus {
  enabled: boolean;
  ready: boolean;
  missing: string[];
  host?: string;
  port?: number;
  sender?: string;
}

export interface CnpjLookup {
  cnpj: string;
  name: string;
  trade_name: string;
  status: string;
  state: string;
  city: string;
  address: string;
  source: string;
  warning: string;
}

export interface InstallationBrand {
  display_name: string;
  short_name: string;
  primary_color: string;
  secondary_color: string;
  logo_url: string | null;
  version: number;
}

export interface InstallationBrandInput {
  display_name: string;
  short_name: string;
  primary_color: string;
  secondary_color: string;
  version: number;
  remove_logo?: boolean;
}

export interface AssistedPreview {
  company_id: string;
  period: string;
  status: "preliminary_unvalidated";
  basis: string;
  coverage: {
    local_xml_complete: false;
    official_cbs_complete: false;
    ibs_official_connected: false;
    signature_cryptographically_verified: false;
    official_receipts: number;
    company_scope_complete: boolean;
    unperioded_document_count: number;
  };
  totals: Array<{
    tax: "CBS" | "IBS";
    direction: "incoming" | "outgoing";
    model: "55" | "65" | "57";
    amount: string;
    document_count: number;
  }>;
  summary: Record<"total" | "match" | "mismatch" | "missing" | "ambiguous" | "unknown", number>;
  items: Array<{
    document_key: string | null;
    period: string | null;
    resource: string | null;
    status: string;
    reason: string | null;
    source_amount: string | null;
    source_ibs_amount: string | null;
    official_amount: string | null;
    credit_to_appropriate: string | null;
    credit_appropriated: string | null;
    credit_used: string | null;
    document_ids: string[];
    record_ids: string[];
    source_sha256?: string | null;
    model?: string | null;
    source_signature_present?: boolean | null;
    source_authorization_status?: string | null;
  }>;
  issues: Array<{ code: string; document_key: string | null; document_ids: string[]; record_ids: string[] }>;
}

export interface Company {
  id: string;
  name: string;
  cnpj: string;
}

export interface Capability {
  enabled?: boolean;
  available?: boolean;
  reason?: string | null;
  environments?: Environment[];
}

export interface Capabilities {
  provider_mode: "fixture" | "receita" | "disabled";
  official_configuration?: { ready: boolean; reason: string | null; missing: string[] };
  environments: Environment[];
  resources: Partial<Record<Resource, Capability>>;
  ibs?: { enabled: boolean; reason?: string | null };
}

export interface ResourceCoverage {
  last_completed_query_id: string | null;
  last_completed_opened_at: string | null;
  age_days: number | null;
  needs_attention: boolean;
  gap_over_eight_days: boolean;
  first_query_limited: boolean;
}

export interface Coverage {
  company_id: string;
  cnpj_base: string;
  environment: Environment;
  incremental_max_days: number;
  has_automatic_backfill: boolean;
  resources: Record<Resource, ResourceCoverage>;
}

export interface Query {
  id: string;
  company_id: string;
  resource: Resource;
  environment: Environment;
  status: string;
  ticket?: string | null;
  error?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
  idempotency_key?: string | null;
  checkpoint?: string | null;
  attempts?: number | null;
  poll_count?: number | null;
  next_poll_at?: string | null;
  opening_started_at?: string | null;
  record_count?: number | null;
  raw_sha256?: string | null;
  raw_path?: string | null;
}

export interface FiscalRecord {
  id: string;
  query_id: string;
  resource: Resource;
  period?: string | null;
  source_key?: string | null;
  amount?: string | number | null;
  credit_to_appropriate?: string | number | null;
  credit_appropriated?: string | number | null;
  credit_used?: string | number | null;
  credit_category?: CreditCategory | null;
  provenance?: Record<string, unknown> | string | null;
  raw?: unknown;
  raw_sha256?: string | null;
}

export interface ListResponse<T> {
  items: T[];
  next_cursor?: string | null;
}

export interface FiscalDocument {
  id: string;
  kind: string;
  document_key: string | null;
  model: string | null;
  sha256: string;
  signature_present: boolean;
  authorization_status: string | null;
  created_at: string | null;
}

export interface LocalAgent {
  id: string;
  name: string;
  company_id: string;
  created_at?: string | null;
  last_used_at?: string | null;
  revoked_at?: string | null;
  status?: string | null;
}

export interface IssuedAgent extends LocalAgent {
  token: string;
}

export interface ReconciliationItem {
  document_key: string | null;
  status: string;
  reason: string | null;
  source_amount: string | number | null;
  official_amount: string | number | null;
  credit_to_appropriate?: string | number | null;
  credit_appropriated?: string | number | null;
  credit_used?: string | number | null;
}

export interface Reconciliation {
  summary: Record<string, string | number | null>;
  items: ReconciliationItem[];
  company_scope_complete?: boolean;
  unperioded_document_count?: number;
}

export interface StockCreditClaim {
  id: string;
  company_id: string;
  inventory_date: string;
  expected_amount: string;
  appropriated_amount: string;
  used_amount: string;
  evidence_ref: string;
  status: string;
  created_at: string | null;
  category: "presumed_stock_credit_candidate";
  evidence_verified: false;
  basis: string;
}

export interface CommercialInstallment {
  id: string;
  company_id: string;
  document_ref: string;
  installment_no: number;
  amount: string;
  paid_at: string | null;
  created_at: string | null;
  category: "commercial_payment";
  basis: string;
}

/** The central is an evidence dashboard; it never asserts official assessment coverage. */
export interface CentralQuery {
  id: string;
  resource: Resource;
  environment: Environment;
  status: string;
  ticket: string | null;
  received_sha256: string | null;
  has_raw_evidence: boolean;
  has_error: boolean;
  poll_count: number;
  created_at: string | null;
  updated_at: string | null;
  official_receipt: boolean;
}

export interface CentralSummary {
  company_id: string;
  cnpj_base: string;
  counts: {
    queries: number;
    active: number;
    attention: number;
    completed: number;
    by_status: Record<string, number>;
    documents: number;
    records: number;
    official_snapshots: number;
  };
  recent_queries: CentralQuery[];
  official_coverage_proven: false;
  basis: string;
}

export interface CentralHistory {
  company_id: string;
  items: CentralQuery[];
  next_cursor: string | null;
}

export type TaxRegime = "regular" | "simples";

export interface TaxCalculationInput {
  company_id: string;
  regime: TaxRegime;
  payload: Record<string, unknown>;
}

export interface TaxCalculation {
  id: string;
  company_id: string;
  regime: TaxRegime;
  created_at: string;
  request_sha256: string;
  response_sha256: string;
  engine_version: Record<string, unknown>;
  totals: Record<string, string>;
  calculation_source: "local_normative_engine";
  government_assessment: false;
  fiscal_credit_appropriated: false;
  request?: Record<string, unknown>;
  engine_response?: Record<string, unknown>;
}

export interface TaxCalculationStatus {
  regular: { configured: boolean };
  simples: { configured: boolean };
}
