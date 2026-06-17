'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, ButtonSpinner, Card, CardContent, CardHeader, CardTitle } from '@reloop/ui';
import { Link2, Upload } from 'lucide-react';
import { getBrowserSupabase } from '@/lib/supabase';
import { getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';
import { IMP_COOKIE, isImpersonatingBrowser, readCookieBrowser } from '@/lib/impersonation';

type UploadArgs = { file: File; title: string };

const KB_BUCKET = 'kb-documents';

type Mode = 'file' | 'url';

export function KnowledgeBaseUploader() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<Mode>('file');
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [url, setUrl] = useState('');
  // Determinate % on the impersonation proxy path (XHR); null elsewhere.
  const [progress, setProgress] = useState<number | null>(null);

  const mutation = useMutation({
    mutationFn: async ({ file, title }: UploadArgs) => {
      if (!merchantId) throw new Error('No merchant context');

      // Under agency impersonation there is no supabase-js session, so the
      // direct-to-Storage upload (RLS-scoped) can't authenticate. Route the
      // file through the FastAPI proxy, which uploads server-side. We use XHR
      // here (not fetch) so we can show a determinate upload progress bar.
      if (isImpersonatingBrowser()) {
        const form = new FormData();
        form.append('file', file);
        form.append('title', title);
        const token = readCookieBrowser(IMP_COOKIE);
        return uploadViaProxy({
          url: `${process.env.NEXT_PUBLIC_API_BASE_URL}/knowledge-base/${merchantId}/upload`,
          form,
          token,
          onProgress: setProgress,
        });
      }

      const supabase = getBrowserSupabase();
      const storagePath = `${merchantId}/${Date.now()}-${slugify(file.name)}`;

      const upload = await supabase.storage.from(KB_BUCKET).upload(storagePath, file, {
        contentType: file.type,
        upsert: false,
      });
      if (upload.error) throw upload.error;

      const api = getApiClient();
      const source = inferSource(file);
      const { data, error } = await api.POST('/knowledge-base/{merchant_id}/docs', {
        params: { path: { merchant_id: merchantId } },
        body: { title, source, storage_path: storagePath },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-docs'] });
      setFile(null);
      setTitle('');
    },
    onSettled: () => setProgress(null),
  });

  const urlMutation = useMutation({
    mutationFn: async ({ title, url }: { title: string; url: string }) => {
      if (!merchantId) throw new Error('No merchant context');
      const api = getApiClient();
      const { data, error } = await api.POST('/knowledge-base/{merchant_id}/docs', {
        params: { path: { merchant_id: merchantId } },
        body: { title, source: 'url', url },
      });
      if (error) throw new Error(typeof error === 'string' ? error : JSON.stringify(error));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kb-docs'] });
      setUrl('');
      setTitle('');
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Carica documento</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          <div className="flex gap-1 rounded-md bg-muted p-1 text-sm">
            <button
              type="button"
              onClick={() => setMode('file')}
              className={`flex flex-1 items-center justify-center gap-2 rounded px-3 py-1.5 ${
                mode === 'file'
                  ? 'bg-background shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Upload className="h-4 w-4" />
              File
            </button>
            <button
              type="button"
              onClick={() => setMode('url')}
              className={`flex flex-1 items-center justify-center gap-2 rounded px-3 py-1.5 ${
                mode === 'url'
                  ? 'bg-background shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Link2 className="h-4 w-4" />
              URL
            </button>
          </div>

          <input
            type="text"
            placeholder="Titolo (es. Listino 2026)"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          />

          {mode === 'file' ? (
            <>
              <label className="flex cursor-pointer items-center gap-3 rounded-md border-2 border-dashed border-input p-6 text-sm text-muted-foreground hover:bg-accent">
                <Upload className="h-5 w-5" />
                <span>
                  {file ? file.name : 'Trascina o seleziona PDF, DOCX, TXT (max 20 MB)'}
                </span>
                <input
                  type="file"
                  accept=".pdf,.docx,.txt"
                  className="hidden"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </label>
              {mutation.error ? (
                <p className="text-sm text-destructive">
                  {mutation.error instanceof Error ? mutation.error.message : 'Upload failed'}
                </p>
              ) : null}
              {mutation.isPending && progress !== null ? (
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-[width] duration-150"
                    style={{ width: `${progress}%` }}
                  />
                </div>
              ) : null}
              <Button
                disabled={!file || !title || mutation.isPending}
                onClick={() => file && title && mutation.mutate({ file, title })}
              >
                {mutation.isPending ? (
                  <>
                    <ButtonSpinner />
                    Caricamento…
                  </>
                ) : (
                  'Carica e indicizza'
                )}
              </Button>
            </>
          ) : (
            <>
              <input
                type="url"
                placeholder="https://esempio.it/pagina"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              />
              <p className="text-xs text-muted-foreground">
                Il bot scaricherà e indicizzerà il contenuto della pagina.
              </p>
              {urlMutation.error ? (
                <p className="text-sm text-destructive">
                  {urlMutation.error instanceof Error
                    ? urlMutation.error.message
                    : 'Creazione fallita'}
                </p>
              ) : null}
              <Button
                disabled={!url || !title || urlMutation.isPending}
                onClick={() => url && title && urlMutation.mutate({ title, url })}
              >
                {urlMutation.isPending ? (
                  <>
                    <ButtonSpinner />
                    Indicizzazione…
                  </>
                ) : (
                  'Indicizza da URL'
                )}
              </Button>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/** POST a multipart form via XHR so we can report determinate upload progress. */
function uploadViaProxy({
  url,
  form,
  token,
  onProgress,
}: {
  url: string;
  form: FormData;
  token: string | null;
  onProgress: (pct: number) => void;
}): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(xhr.responseText ? JSON.parse(xhr.responseText) : null);
        } catch {
          resolve(null);
        }
      } else {
        let message = 'Upload fallito';
        try {
          const body = JSON.parse(xhr.responseText) as { error?: { message?: string } };
          message = body?.error?.message ?? message;
        } catch {
          /* keep default */
        }
        reject(new Error(message));
      }
    };
    xhr.onerror = () => reject(new Error('Errore di rete durante il caricamento'));
    xhr.send(form);
  });
}

function inferSource(file: File): 'pdf' | 'docx' | 'txt' {
  if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) return 'pdf';
  if (
    file.type === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' ||
    file.name.toLowerCase().endsWith('.docx')
  )
    return 'docx';
  return 'txt';
}

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9.-]+/g, '-')
    .replace(/^-+|-+$/g, '');
}
