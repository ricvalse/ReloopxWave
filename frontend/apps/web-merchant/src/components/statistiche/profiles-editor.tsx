'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Textarea,
  toast,
} from '@reloop/ui';
import { getApiClient } from '@/lib/api';
import { ProfileBehaviorEditor } from './profile-behavior-editor';
import type { ConversationProfile } from './types';

/** Profili di conversazione (ADR 0022).
 *
 * Un profilo è un **delta** sopra la configurazione del merchant, non un bot
 * separato: business info, knowledge base e prenotazioni restano condivise. Ne
 * cambia il comportamento — obiettivo, direttive, tono — e, da qui in avanti,
 * anche quali bolle mostra la pagina Statistiche.
 */
export function ProfilesEditor({ profiles }: { profiles: ConversationProfile[] }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);

  const invalidate = () =>
    void queryClient.invalidateQueries({ queryKey: ['conversation-profiles'] });

  const createMutation = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      const { error } = await api.POST('/statistics/profiles', {
        body: {
          key: slugify(name),
          name,
          description: description || null,
          is_default: profiles.filter((p) => !p.is_library).length === 0,
          overrides: {},
        },
      });
      if (error) throw new Error(JSON.stringify(error));
    },
    onSuccess: () => {
      toast.success('Profilo creato');
      setName('');
      setDescription('');
      invalidate();
    },
    onError: () => toast.error('Creazione non riuscita'),
  });

  const setDefaultMutation = useMutation({
    mutationFn: async (profileId: string) => {
      const api = getApiClient();
      const { error } = await api.PATCH('/statistics/profiles/{profile_id}', {
        params: { path: { profile_id: profileId } },
        body: { is_default: true },
      });
      if (error) throw new Error(JSON.stringify(error));
    },
    onSuccess: () => {
      toast.success('Profilo di default aggiornato');
      invalidate();
    },
    onError: () => toast.error('Aggiornamento non riuscito'),
  });

  const toggleMutation = useMutation({
    mutationFn: async (profile: ConversationProfile) => {
      const api = getApiClient();
      const { error } = await api.PATCH('/statistics/profiles/{profile_id}', {
        params: { path: { profile_id: profile.id } },
        body: { enabled: !profile.enabled },
      });
      if (error) throw new Error(JSON.stringify(error));
    },
    onSuccess: invalidate,
    onError: () => toast.error('Aggiornamento non riuscito'),
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Nuovo profilo</CardTitle>
          <p className="text-xs text-muted-foreground">
            Un profilo cambia il comportamento del bot su una conversazione — obiettivo,
            direttive, tono — lasciando condivise informazioni aziendali, knowledge base e
            prenotazioni. Un&apos;automazione può caricarlo con il nodo «Carica profilo»; a fine
            conversazione si torna a quello di default.
          </p>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="profile-name">Nome</Label>
            <Input
              id="profile-name"
              value={name}
              placeholder="Consulenza telefonica"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="profile-description">Descrizione</Label>
            <Textarea
              id="profile-description"
              value={description}
              placeholder="Quando parte la campagna consulenze, il bot qualifica invece di prenotare."
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
            />
          </div>
          <Button
            onClick={() => createMutation.mutate()}
            disabled={!name.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? 'Creazione…' : 'Crea profilo'}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Profili</CardTitle>
        </CardHeader>
        <CardContent>
          {profiles.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nessun profilo. Senza profili il bot si comporta come sempre: la configurazione del
              merchant vale per ogni conversazione.
            </p>
          ) : (
            <ul className="space-y-2">
              {profiles.map((p) => (
                <li key={p.id} className="rounded-md border border-border p-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{p.name}</span>
                    <code className="text-xs text-muted-foreground">{p.key}</code>
                    {p.is_default ? <Badge>Default</Badge> : null}
                    {p.is_library ? <Badge variant="secondary">Libreria agenzia</Badge> : null}
                    {!p.enabled ? <Badge variant="outline">Disattivato</Badge> : null}
                    {p.description ? (
                      <span className="w-full text-xs text-muted-foreground">{p.description}</span>
                    ) : null}
                    {!p.is_library ? (
                      <div className="ml-auto flex gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setOpenId(openId === p.id ? null : p.id)}
                        >
                          {openId === p.id ? 'Chiudi' : 'Istruzioni'}
                        </Button>
                        {!p.is_default ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setDefaultMutation.mutate(p.id)}
                          >
                            Rendi default
                          </Button>
                        ) : null}
                        <Button variant="ghost" size="sm" onClick={() => toggleMutation.mutate(p)}>
                          {p.enabled ? 'Disattiva' : 'Riattiva'}
                        </Button>
                      </div>
                    ) : null}
                  </div>
                  {openId === p.id ? (
                    <div className="mt-3">
                      <ProfileBehaviorEditor profile={p} />
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 64);
}
