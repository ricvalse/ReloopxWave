import createClient, { type Middleware } from 'openapi-fetch';
import type { paths } from './generated';

export type { components, paths } from './generated';

export type ReloopClient = ReturnType<typeof createClient<paths>>;

export function createReloopClient(options: {
  baseUrl: string;
  getAccessToken?: () => Promise<string | null>;
  /** Called for every response; use to react to an auth session that the
   * backend has rejected (expired/missing/mismatched token) — e.g. redirect. */
  onSessionInvalid?: (response: Response) => void | Promise<void>;
}): ReloopClient {
  const client = createClient<paths>({ baseUrl: options.baseUrl });

  if (options.getAccessToken) {
    const authMiddleware: Middleware = {
      async onRequest({ request }) {
        const token = await options.getAccessToken!();
        if (token) {
          request.headers.set('Authorization', `Bearer ${token}`);
        }
        return request;
      },
    };
    client.use(authMiddleware);
  }

  if (options.onSessionInvalid) {
    const sessionMiddleware: Middleware = {
      async onResponse({ response }) {
        await options.onSessionInvalid!(response);
        return undefined;
      },
    };
    client.use(sessionMiddleware);
  }

  return client;
}
