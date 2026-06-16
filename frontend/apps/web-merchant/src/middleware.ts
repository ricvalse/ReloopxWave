import { NextResponse, type NextRequest } from 'next/server';
import { getServerSupabase } from '@/lib/supabase';
import { IMP_COOKIE, decodeJwtPayload, impTokenValid } from '@/lib/impersonation';

export async function middleware(request: NextRequest) {
  const response = NextResponse.next();
  const path = request.nextUrl.pathname;

  // The handoff route and the expired landing must be reachable without any
  // session — the handoff is precisely what establishes one.
  if (path === '/impersonate' || path.startsWith('/impersonation-expired')) {
    return response;
  }

  const supabase = getServerSupabase({
    getAll: () => request.cookies.getAll().map(({ name, value }) => ({ name, value })),
    setAll: (pairs) => {
      for (const { name, value, options } of pairs) {
        response.cookies.set({ name, value, ...options });
      }
    },
  });

  const {
    data: { session },
  } = await supabase.auth.getSession();

  // An impersonation session is a valid `imp-access-token` cookie.
  const impCookie = request.cookies.get(IMP_COOKIE)?.value ?? null;
  const impValid = impCookie ? impTokenValid(decodeJwtPayload(impCookie)) : false;

  const isLogin = path.startsWith('/login');

  if (session || impValid) {
    if (session && isLogin) {
      return NextResponse.redirect(new URL('/dashboard', request.url));
    }
    return response;
  }

  if (isLogin) {
    return response;
  }

  // No live session. If an impersonation cookie was present but is now expired,
  // send the admin to the dedicated expired page rather than the merchant login.
  if (impCookie) {
    return NextResponse.redirect(new URL('/impersonation-expired', request.url));
  }

  const loginUrl = new URL('/login', request.url);
  loginUrl.searchParams.set('redirectTo', path);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|api/public).*)'],
};
