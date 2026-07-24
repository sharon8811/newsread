export type CaptureMode = "full" | "metadata_only";

export interface DomainRule {
  id: number;
  hostname: string;
  match_subdomains: boolean;
  mode: "exclude" | "metadata_only";
}

export interface ExtensionSettings {
  serverUrl: string;
  token: string;
  paused: boolean;
  captureMode: CaptureMode;
  excludedDomains: string[];
  knownRevision: number;
  domainRules: DomainRule[];
  connectionStatus: "unpaired" | "paired" | "revoked" | "error";
  connectionId: number | null;
  connectionName: string;
  userName: string;
  lastSyncAt: string | null;
}

export interface CaptureCandidate {
  url: string;
  title: string;
  text: string;
  textExcerpt: string;
  capturedAt: string;
}

export interface QueuedCapture {
  urlHash: string;
  record_id: string;
  url: string;
  title: string;
  text: string;
  text_excerpt: string;
  first_visited_at: string;
  last_visited_at: string;
  captured_at: string | null;
  visit_count: number;
  known_revision: number;
  contentHash: string;
  queuedAt: number;
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
