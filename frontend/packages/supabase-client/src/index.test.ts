import { describe, expect, it } from 'vitest';
import { browserClientOptions } from './index';

/**
 * Guards the impersonation Realtime fix.
 *
 * Under agency→merchant impersonation the browser client MUST stay a singleton.
 * RealtimeAuthGate calls `realtime.setAuth(token)` on the shared instance while
 * the channel consumers (`conversations-route`, `agenda/use-appointments`,
 * `dashboard/merchant-dashboard`) subscribe `.channel()` on that same instance.
 * If we passed `isSingleton: false` when a token is present, each caller would
 * get a FRESH client, splitting setAuth from the channels — Realtime would
 * authenticate as anon, RLS would block, and we'd regress to the 30s poll. So:
 * never emit `isSingleton`, and carry the impersonation token on REST via the
 * global Authorization header.
 */
describe('browserClientOptions', () => {
  it('is a plain singleton client when not impersonating (no token)', () => {
    const opts = browserClientOptions();
    expect(opts).not.toHaveProperty('isSingleton');
    expect(opts).not.toHaveProperty('global');
  });

  it('keeps the singleton even under impersonation (never isSingleton:false)', () => {
    const opts = browserClientOptions('imp-jwt') as { isSingleton?: boolean };
    // Regression guard: a non-singleton client would break RealtimeAuthGate.
    expect(opts).not.toHaveProperty('isSingleton');
    expect(opts.isSingleton).not.toBe(false);
  });

  it('carries the impersonation Bearer on REST/Storage reads', () => {
    const opts = browserClientOptions('imp-jwt') as {
      global?: { headers?: Record<string, string> };
    };
    expect(opts.global?.headers?.Authorization).toBe('Bearer imp-jwt');
  });
});
