// Mirrors backend/app/schemas.py's Admin* models exactly -- field names and
// shapes must stay in sync with those, not renamed independently here.

export interface AdminUserSummary {
  id: number;
  email: string;
  createdAt: string;
  lastLoginAt: string | null;
  isActive: boolean;
  plan: string;
  storageUsedBytes: number;
  storageLimitBytes: number;
  documentCount: number;
}

export interface AdminDocumentSummary {
  id: string;
  name: string;
  sizeBytes: number;
  createdAt: string;
  modifiedAt: string;
}

export interface AdminUserDetail {
  user: AdminUserSummary;
  documents: AdminDocumentSummary[];
}

export interface AdminDeviceSummary {
  id: number;
  userEmail: string | null;
  platform: string | null;
  appVersion: string | null;
  createdAt: string;
  lastSeenAt: string;
}

export interface AdminAppConfig {
  minSupportedVersion: string;
  latestVersion: string;
  playStoreUrl: string;
}

export interface AdminNotificationSendResult {
  message: string;
  successCount: number;
  failureCount: number;
}
