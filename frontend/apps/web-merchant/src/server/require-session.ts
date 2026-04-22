import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { getServerSupabase } from '@/lib/supabase';

export async function requireSession() {
  const cookieStore = await cookies();
  const supabase = getServerSupabase({
    getAll: () => cookieStore.getAll(),
    setAll: (pairs) => {
      for (const { name, value, options } of pairs) {
        cookieStore.set({ name, value, ...options });
      }
    },
  });

  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    redirect('/login');
  }

  return session;
}
