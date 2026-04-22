import { NextResponse, type NextRequest } from 'next/server';
import { getServerSupabase } from '@/lib/supabase';

export async function middleware(request: NextRequest) {
  const response = NextResponse.next();

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

  const isLogin = request.nextUrl.pathname.startsWith('/login');
  if (!session && !isLogin) {
    const loginUrl = new URL('/login', request.url);
    loginUrl.searchParams.set('redirectTo', request.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }
  if (session && isLogin) {
    return NextResponse.redirect(new URL('/dashboard', request.url));
  }

  return response;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico|api/public).*)'],
};
