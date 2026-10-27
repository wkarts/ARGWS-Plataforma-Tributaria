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
  provider_mode: "fixture" | "receita";
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

export type SimulationMode = "regular" | "simples";

export interface ReformSimulationInput {
  company_id: string;
  period: string;
  mode: SimulationMode;
  revenue: string;
  purchases: string;
  cbs_sales_rate: string;
  ibs_sales_rate: string;
  cbs_purchase_rate: string;
  ibs_purchase_rate: string;
  das_effective_rate?: string;
  das_cbs_component_rate?: string;
  das_ibs_component_rate?: string;
}

export interface ReformSimulation {
  company_id: string;
  period: string;
  mode: SimulationMode;
  source: "operator_defined_hypothesis";
  official_assessment: false;
  official_coverage_proven: false;
  regular: {
    cbs_debit: string;
    ibs_debit: string;
    cbs_potential_purchase_tax: string;
    ibs_potential_purchase_tax: string;
    theoretical_net: string;
    credit_to_appropriate: null;
    credit_appropriated: null;
    credit_used: null;
  };
  simples: {
    das_total_estimate: string;
    das_cbs_component_estimate: string;
    das_ibs_component_estimate: string;
    das_other_component_estimate: string;
    regular_option_combined_hypothesis: string;
    difference_regular_minus_simples: string;
  } | null;
  warnings: string[];
}
