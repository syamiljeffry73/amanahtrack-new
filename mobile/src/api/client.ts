/**
 * API client.
 *
 * One rule worth keeping: rule refusals from the server are not errors to be
 * swallowed. A 422 carrying a fiqh explanation is a real answer and the UI
 * needs the whole payload, so it is surfaced as a typed result rather than a
 * thrown exception with a lost body.
 */

const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

/**
 * Session state.
 *
 * The slug is no longer read from config. It comes back from the login
 * response, because the server decides which institution a user belongs to.
 * A client that picks its own slug is a client that can ask for someone
 * else's ledger.
 */
let TOKEN: string | null = null;
let SLUG = '';
let onExpired: (() => void) | null = null;

export function setSession(token: string | null, slug = '') {
  TOKEN = token;
  SLUG = slug;
}

export function onSessionExpired(cb: () => void) {
  onExpired = cb;
}

export interface Session {
  token: string;
  name: string;
  role: 'admin' | 'treasurer' | 'auditor' | 'committee';
  institution_slug: string;
}

export function canWrite(role: Session['role'] | undefined): boolean {
  return role === 'treasurer' || role === 'admin';
}

export type FundCategory =
  | 'zakat'
  | 'sadaqah'
  | 'infaq'
  | 'waqf_principal'
  | 'waqf_income'
  | 'nazar'
  | 'fidyah'
  | 'khairat';

export interface Fund {
  id: string;
  code: string;
  category: FundCategory;
  label_ms: string;
  label_en: string;
  balance: string;
  provisional: string;
  spendable: boolean;
  constraint_note: string;
}

export interface Txn {
  reference: string;
  description: string;
  occurred_at: string;
  channel: string;
  status: 'provisional' | 'confirmed' | 'void';
  amount: string;
}

export interface RuleVerdict {
  allowed: boolean;
  code: string;
  message_en: string;
  message_ms: string;
  suggested_funds: string[];
}

export interface CountResult {
  alert_raised?: boolean;
  counted: string;
  estimated: string;
  variance: string;
  flagged: boolean;
}

export type AlertSeverity = 'info' | 'warning' | 'critical';
export type AlertStatus = 'open' | 'acknowledged' | 'escalated';

export interface Alert {
  id: string;
  seq: number;
  kind: string;
  severity: AlertSeverity;
  status: AlertStatus;
  title: string;
  detail: string;
  device_label: string | null;
  related_reference: string | null;
  occurred_at: string;
  has_photo: boolean;
  photo_sha256: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
}

/** ok is null when a check does not apply, for example no photo attached. */
export interface Check {
  key: string;
  label: string;
  ok: boolean | null;
  detail: string;
}

export interface AlertVerification {
  intact: boolean;
  seq: number;
  photo_sha256: string | null;
  recomputed_sha256: string | null;
  payload_hash: string;
  prev_hash: string;
  chain_hash: string;
  checks: Check[];
}

export interface Proof {
  reference: string;
  description: string;
  amount: string;
  seq: number;
  sealed_at: string;
  payload_hash: string;
  recomputed_payload_hash: string;
  prev_hash: string;
  chain_hash: string;
  intact: boolean;
  checks: Check[];
}

/** Treasurers handle the cash, so they cannot be the ones who clear its alerts. */
export function canReview(role: Session['role'] | undefined): boolean {
  return role === 'committee' || role === 'admin' || role === 'auditor';
}

export interface LiveEvent {
  id: string;
  kind: string;
  detail: string;
  device_label: string | null;
  occurred_at: string;
  alert_id: string | null;
  severity: AlertSeverity | null;
  has_photo: boolean;
}

export interface DeviceStatus {
  serial: string;
  label: string;
  online: boolean;
  last_seen_at: string | null;
  door_state: 'open' | 'closed' | null;
  door_opened_at: string | null;
}

export type LiveMessage =
  | { type: 'hello' }
  | { type: 'event'; event: LiveEvent }
  | { type: 'status'; device: DeviceStatus }
  | { type: 'alert_count'; open: number; critical_open: number };

/** WebSocket URL for the live feed. The token rides in the query string
 *  because a phone WebSocket cannot set an Authorization header. */
export function liveSocketUrl(): string {
  return `${BASE_URL.replace(/^http/, 'ws')}/i/${SLUG}/live?token=${encodeURIComponent(TOKEN ?? '')}`;
}

export type Result<T> =
  | { ok: true; data: T }
  | { ok: false; refusal: RuleVerdict }
  | { ok: false; error: string };

function authHeaders(): Record<string, string> {
  return TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  });
  if (res.status === 401) {
    // Token expired or revoked. Bounce to login rather than showing a
    // confusing empty screen.
    setSession(null);
    onExpired?.();
    throw new Error('Sesi tamat. Sila log masuk semula.');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body?.detail;
    throw new Error(
      (typeof detail === 'string' ? detail : detail?.message_ms ?? detail?.message_en) ??
        `Request failed (${res.status})`,
    );
  }
  return res.json() as Promise<T>;
}

export async function login(email: string, password: string): Promise<Session> {
  // The login endpoint takes form encoding, not JSON: it follows the OAuth2
  // password flow so FastAPI's own docs page can drive it too.
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
  if (!res.ok) {
    throw new Error(
      res.status === 401
        ? 'E-mel atau kata laluan salah.'
        : `Tidak dapat log masuk (${res.status})`,
    );
  }
  const data = await res.json();
  const session: Session = {
    token: data.access_token,
    name: data.name,
    role: data.role,
    institution_slug: data.institution_slug,
  };
  setSession(session.token, session.institution_slug);
  return session;
}

export const api = {
  funds: () => request<Fund[]>(`/i/${SLUG}/funds`),

  transactions: (limit = 30) =>
    request<Txn[]>(`/i/${SLUG}/transactions?limit=${limit}`),

  verify: () =>
    request<{ intact: boolean; checked: number; summary: string; head_hash: string }>(
      `/i/${SLUG}/verify`,
    ),

  checkSpend: (fund_code: string, purpose: string) =>
    request<RuleVerdict>(`/i/${SLUG}/check-spend`, {
      method: 'POST',
      body: JSON.stringify({ fund_code, purpose }),
    }),

  donate: (body: { fund_code: string; amount: number; description: string }) =>
    request<{ reference: string; balance: string }>(`/i/${SLUG}/donations`, {
      method: 'POST',
      body: JSON.stringify({ ...body, channel: 'dashboard' }),
    }),

  /** Returns a refusal instead of throwing, because a refusal is the feature. */
  async disburse(body: {
    fund_code: string;
    amount: number;
    purpose: string;
    description: string;
  }): Promise<Result<{ reference: string; balance: string }>> {
    const res = await fetch(`${BASE_URL}/i/${SLUG}/disbursements`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    });
    const payload = await res.json().catch(() => null);

    if (res.status === 401) {
      setSession(null);
      onExpired?.();
      return { ok: false, error: 'Sesi tamat. Sila log masuk semula.' };
    }
    if (res.status === 403) {
      return {
        ok: false,
        error:
          payload?.detail?.message_ms ??
          'Peranan anda tidak dibenarkan merekod perbelanjaan.',
      };
    }
    if (res.ok) return { ok: true, data: payload };
    if (res.status === 422 && payload?.detail?.code) {
      return { ok: false, refusal: payload.detail as RuleVerdict };
    }
    return {
      ok: false,
      error: payload?.detail?.message_en ?? `Could not post (${res.status})`,
    };
  },

  devices: () => request<DeviceStatus[]>(`/i/${SLUG}/devices`),

  registerPushToken: (token: string) =>
    fetch(`${BASE_URL}/i/${SLUG}/push-token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ token, platform: 'android' }),
    }),
  removePushToken: (token: string) =>
    fetch(`${BASE_URL}/i/${SLUG}/push-token?token=${encodeURIComponent(token)}`, {
      method: 'DELETE',
      headers: authHeaders(),
    }),

  liveEvents: (limit = 40) => request<LiveEvent[]>(`/i/${SLUG}/live/events?limit=${limit}`),

  alertSummary: () =>
    request<{ open: number; critical_open: number }>(`/i/${SLUG}/alerts/summary`),

  alerts: (status?: AlertStatus) =>
    request<Alert[]>(`/i/${SLUG}/alerts${status ? `?status_filter=${status}` : ''}`),

  alert: (id: string) => request<Alert>(`/i/${SLUG}/alerts/${id}`),

  verifyAlert: (id: string) =>
    request<AlertVerification>(`/i/${SLUG}/alerts/${id}/verify`, { method: 'POST' }),

  reviewAlert: (id: string, action: 'acknowledge' | 'escalate', note?: string) =>
    request<Alert>(`/i/${SLUG}/alerts/${id}/review`, {
      method: 'POST',
      body: JSON.stringify({ action, note: note || null }),
    }),

  proof: (reference: string) =>
    request<Proof>(`/i/${SLUG}/transactions/${encodeURIComponent(reference)}/proof`),

  /** The photo needs the bearer token, so it cannot be a bare URL. */
  photoSource: (id: string) => ({
    uri: `${BASE_URL}/i/${SLUG}/alerts/${id}/photo`,
    headers: authHeaders(),
  }),

  /** Fetches the photo with the bearer token and returns it as a data URI.
   * Android's image loader is unreliable with auth headers, so the app
   * downloads the bytes itself and hands the picture over from memory. */
  photoDataUri: async (id: string): Promise<string> => {
    const res = await fetch(`${BASE_URL}/i/${SLUG}/alerts/${id}/photo`, {
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(`photo ${res.status}`);
    const blob = await res.blob();
    return await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error('photo read failed'));
      reader.readAsDataURL(blob);
    });
  },

  closeCount: (body: {
    device_serial?: string;
    counted_total: number;
    witness_count: number;
    note?: string;
  }) =>
    request<CountResult>(`/i/${SLUG}/counts`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
};
