export type CaptureMode = "full" | "metadata_only";

export interface DomainRule {
  id: number;
  hostname: string;
  match_subdomains: boolean;
  mode: "exclude" | "metadata_only";
}

export interface SystemRule {
  id: string;
  label: string;
  description: string;
  hosts: string[];
  path_match: "exact" | "prefix";
  path: string;
  enabled: boolean;
}

export interface ExtensionSettings {
  serverUrl: string;
  token: string;
  paused: boolean;
  captureMode: CaptureMode;
  excludedDomains: string[];
  knownRevision: number;
  domainRules: DomainRule[];
  systemPolicyRevision: number;
  systemRules: SystemRule[];
  contentCapabilityRevision: number;
  connectionStatus: "unpaired" | "paired" | "revoked" | "error";
  connectionId: number | null;
  connectionName: string;
  userName: string;
  lastSyncAt: string | null;
}

export type CaptureBlockKind =
  | "heading"
  | "paragraph"
  | "list_item"
  | "quote"
  | "code";

export interface CaptureBlock {
  id: string;
  kind: CaptureBlockKind;
  text: string;
}

export interface CaptureDocument {
  schema_version: 1;
  extraction_version: "history-dom-v2";
  content_type: "article" | "page";
  language: string;
  blocks: CaptureBlock[];
}

export interface CapturedImage {
  bytesBase64: string;
  contentType: "image/png" | "image/jpeg" | "image/webp";
  width: number;
  height: number;
}

export interface CitationAnchor {
  quote: string;
  prefix: string | null;
  suffix: string | null;
}

export interface CitationNavigation {
  version: 1;
  url: string;
  highlightUrl: string;
  anchor: CitationAnchor;
}

export interface PendingCitation {
  targetUrl: string;
  anchor: CitationAnchor;
  expiresAt: number;
}

export interface CaptureCandidate {
  url: string;
  title: string;
  document: CaptureDocument | null;
  leadImage: CapturedImage | null;
  favicon: CapturedImage | null;
  textExcerpt: string;
  capturedAt: string;
}

export interface QueuedCapture {
  urlHash: string;
  record_id: string;
  url: string;
  title: string;
  legacy_text: string;
  legacy_text_excerpt: string;
  content_hash: string | null;
  lead_image_hash: string | null;
  favicon_image_hash: string | null;
  first_visited_at: string;
  last_visited_at: string;
  captured_at: string | null;
  visit_count: number;
  known_revision: number;
  queuedAt: number;
}

export interface QueuedContent {
  contentHash: string;
  canonicalJson: string;
  uploadState: "pending" | "uploaded";
  lastError: string;
  updatedAt: number;
}

export interface QueuedImage {
  imageHash: string;
  bytesBase64: string;
  contentType: CapturedImage["contentType"];
  uploadState: "pending" | "uploaded";
  lastError: string;
  updatedAt: number;
}

export interface SyncState {
  attempt: number;
  nextRetryAt: number;
  lastError: string;
}

export interface ExtensionStatus {
  paired: boolean;
  paused: boolean;
  captureMode: CaptureMode;
  connectionStatus: ExtensionSettings["connectionStatus"];
  connectionName: string;
  userName: string;
  serverUrl: string;
  lastSyncAt: string | null;
  queueCount: number;
  lastError: string;
}
