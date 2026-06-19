'use client';

import '@xyflow/react/dist/style.css';

import { useCallback, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import type { components } from '@reloop/api-client';
import { Button, Card, CardContent, Input, Label } from '@reloop/ui';
import { Trash2 } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import {
  ACTION_DEFS,
  CONDITION_DEFS,
  DEFS_BY_KIND,
  TRIGGER_DEFS,
  type AutomationNodeData,
  type FieldDef,
  type NodeKind,
  automationNodeTypes,
  findDef,
} from './automation-nodes';

type Automation = components['schemas']['AutomationOut'];
type Template = components['schemas']['WhatsAppTemplateOut'];

const PALETTE: { kind: NodeKind; label: string; defs: typeof TRIGGER_DEFS }[] = [
  { kind: 'trigger', label: 'Trigger', defs: TRIGGER_DEFS },
  { kind: 'condition', label: 'Condizioni', defs: CONDITION_DEFS },
  { kind: 'action', label: 'Azioni', defs: ACTION_DEFS },
];

function defaultConfig(kind: NodeKind, type: string): Record<string, unknown> {
  if (kind === 'condition') {
    if (type === 'lead_temperature') return { op: '==', value: 'hot' };
    if (type === 'lead_score') return { op: '>=', value: 80 };
    if (type === 'time_of_day') return { from: '09:00', to: '18:00' };
    if (type === 'message_contains') return { keywords: [] };
  }
  if (kind === 'action') {
    if (type === 'wait') return { minutes: 60 };
    if (type === 'send') return { window_policy: 'auto', free_text: '', template_id: '', variable_mapping: {} };
    if (type === 'send_message') return { text: '' };
    if (type === 'send_template') return { template_id: '', variable_mapping: {} };
  }
  return {};
}

function toRFNodes(a: Automation, isSystem: boolean): Node[] {
  return a.nodes.map((n) => ({
    id: n.node_key,
    type: n.kind,
    position: { x: n.position_x, y: n.position_y },
    // System lifecycle flows have a locked trigger (the entry point can't be removed).
    deletable: !(isSystem && n.kind === 'trigger'),
    data: {
      kind: n.kind as NodeKind,
      type: n.type,
      label: findDef(n.kind as NodeKind, n.type)?.label ?? n.type,
      config: (n.config as Record<string, unknown>) ?? {},
    } satisfies AutomationNodeData,
  }));
}

function toRFEdges(a: Automation): Edge[] {
  return a.edges.map((e, i) => ({
    id: `e${i}`,
    source: e.source_key,
    target: e.target_key,
    sourceHandle: e.branch === 'default' ? undefined : e.branch,
    label: e.branch === 'true' ? 'sì' : e.branch === 'false' ? 'no' : undefined,
  }));
}

function describeError(error: unknown): string {
  const detail = (error as { detail?: { errors?: string[] } } | undefined)?.detail;
  if (detail?.errors && Array.isArray(detail.errors)) return detail.errors.join(' · ');
  return apiErrorMessage(error);
}

export function AutomationEditor({
  editing,
  onDone,
}: {
  editing: Automation | null;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const isSystem = editing?.is_system ?? false;
  const [name, setName] = useState(editing?.name ?? 'Nuova automazione');
  const [enabled, setEnabled] = useState(editing?.enabled ?? false);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(
    editing ? toRFNodes(editing, isSystem) : [],
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(editing ? toRFEdges(editing) : []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const idCounter = useRef(
    editing
      ? Math.max(0, ...editing.nodes.map((n) => Number(n.node_key.replace(/\D/g, '')) || 0)) + 1
      : 1,
  );

  const templates = useQuery({
    queryKey: ['whatsapp-templates'],
    queryFn: async (): Promise<Template[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/whatsapp-templates');
      if (error) throw new Error(apiErrorMessage(error));
      return data as Template[];
    },
  });
  const approvedTemplates = useMemo(
    () => (templates.data ?? []).filter((t) => t.status === 'approved'),
    [templates.data],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      const branchLabel =
        connection.sourceHandle === 'true' ? 'sì' : connection.sourceHandle === 'false' ? 'no' : undefined;
      setEdges((eds) => addEdge({ ...connection, label: branchLabel }, eds));
    },
    [setEdges],
  );

  const addNode = (kind: NodeKind, type: string) => {
    const id = `n${idCounter.current++}`;
    const def = findDef(kind, type);
    const newNode: Node = {
      id,
      type: kind,
      position: { x: 120 + Math.random() * 240, y: 80 + Math.random() * 240 },
      data: {
        kind,
        type,
        label: def?.label ?? type,
        config: defaultConfig(kind, type),
      } satisfies AutomationNodeData,
    };
    setNodes((nds) => nds.concat(newNode));
    setSelectedId(id);
  };

  const updateConfig = (id: string, key: string, value: unknown) => {
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id
          ? { ...n, data: { ...n.data, config: { ...(n.data as AutomationNodeData).config, [key]: value } } }
          : n,
      ),
    );
  };

  const deleteSelected = () => {
    if (!selectedId) return;
    const node = nodes.find((n) => n.id === selectedId);
    // The trigger of a system lifecycle flow is locked.
    if (isSystem && (node?.data as AutomationNodeData | undefined)?.kind === 'trigger') return;
    setNodes((nds) => nds.filter((n) => n.id !== selectedId));
    setEdges((eds) => eds.filter((e) => e.source !== selectedId && e.target !== selectedId));
    setSelectedId(null);
  };

  const buildPayload = () => ({
    name,
    description: null,
    enabled,
    canvas: {},
    nodes: nodes.map((n) => {
      const data = n.data as AutomationNodeData;
      return {
        node_key: n.id,
        kind: data.kind,
        type: data.type,
        config: data.config,
        position_x: Math.round(n.position.x),
        position_y: Math.round(n.position.y),
      };
    }),
    edges: edges.map((e) => ({
      source_key: e.source,
      target_key: e.target,
      branch: (e.sourceHandle as string) || 'default',
    })),
  });

  const save = useMutation({
    mutationFn: async () => {
      const api = getApiClient();
      if (editing) {
        const { error } = await api.PUT('/automations/{automation_id}', {
          params: { path: { automation_id: editing.id } },
          body: buildPayload() as never,
        });
        if (error) throw new Error(describeError(error));
      } else {
        const { error } = await api.POST('/automations', { body: buildPayload() as never });
        if (error) throw new Error(describeError(error));
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['automations'] });
      onDone();
    },
  });

  const selectedNode = nodes.find((n) => n.id === selectedId) ?? null;
  const triggerCount = nodes.filter((n) => (n.data as AutomationNodeData).kind === 'trigger').length;
  // System flows: trigger is locked (hide the Trigger group) and use only the
  // unified `send` action (hide the legacy send_template/send_message).
  const palette = PALETTE.filter((g) => !(isSystem && g.kind === 'trigger')).map((g) =>
    isSystem && g.kind === 'action'
      ? { ...g, defs: g.defs.filter((d) => d.type !== 'send_template' && d.type !== 'send_message') }
      : g,
  );

  return (
    <Card>
      <CardContent className="space-y-3 p-4">
        {/* Toolbar */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Input className="max-w-xs" value={name} onChange={(e) => setName(e.target.value)} />
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Attiva
            </label>
            <Button variant="ghost" onClick={onDone}>
              Annulla
            </Button>
            <Button onClick={() => save.mutate()} disabled={save.isPending || !name.trim()}>
              {save.isPending ? 'Salvataggio…' : 'Salva'}
            </Button>
          </div>
        </div>

        {isSystem ? (
          <p className="text-xs text-muted-foreground">
            Flusso di sistema: il trigger è bloccato. Aggiungi condizioni e azioni «Invia» per
            personalizzare gli invii; attivalo per usarlo al posto del testo predefinito.
          </p>
        ) : triggerCount !== 1 ? (
          <p className="text-xs text-amber-600 dark:text-amber-500">
            Un’automazione richiede esattamente un nodo trigger (ora: {triggerCount}). Necessario per attivarla.
          </p>
        ) : null}
        {save.error ? (
          <p className="text-xs text-destructive">
            {save.error instanceof Error ? save.error.message : 'Errore nel salvataggio'}
          </p>
        ) : null}

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[180px_1fr_280px]">
          {/* Palette */}
          <div className="space-y-3">
            {palette.map((group) => (
              <div key={group.kind} className="space-y-1">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {group.label}
                </p>
                {group.defs.map((def) => (
                  <button
                    key={def.type}
                    type="button"
                    onClick={() => addNode(group.kind, def.type)}
                    className="block w-full rounded-md border border-input bg-background px-2 py-1.5 text-left text-xs hover:bg-accent"
                  >
                    {def.label}
                  </button>
                ))}
              </div>
            ))}
          </div>

          {/* Canvas */}
          <div className="h-[68vh] overflow-hidden rounded-md border border-input">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              nodeTypes={automationNodeTypes}
              onNodeClick={(_e, node) => setSelectedId(node.id)}
              onPaneClick={() => setSelectedId(null)}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background />
              <Controls />
              <MiniMap pannable zoomable />
            </ReactFlow>
          </div>

          {/* Config panel */}
          <div className="space-y-3">
            {selectedNode ? (
              <NodeConfigPanel
                node={selectedNode}
                approvedTemplates={approvedTemplates}
                onChange={(key, value) => updateConfig(selectedNode.id, key, value)}
                onDelete={deleteSelected}
              />
            ) : (
              <p className="rounded-md border border-dashed border-input p-4 text-xs text-muted-foreground">
                Aggiungi nodi dalla palette, collegali trascinando dai pallini, e clicca un nodo per
                configurarlo. Le condizioni hanno due uscite: «sì» e «no».
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function NodeConfigPanel({
  node,
  approvedTemplates,
  onChange,
  onDelete,
}: {
  node: Node;
  approvedTemplates: Template[];
  onChange: (key: string, value: unknown) => void;
  onDelete: () => void;
}) {
  const data = node.data as AutomationNodeData;
  const def = findDef(data.kind, data.type);
  const config = data.config || {};

  return (
    <div className="space-y-3 rounded-md border border-input p-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">{DEFS_BY_KIND[data.kind] ? data.kind : ''}</p>
          <p className="text-sm font-medium">{def?.label ?? data.type}</p>
        </div>
        <Button variant="ghost" size="icon" onClick={onDelete} aria-label="Elimina nodo">
          <Trash2 size={16} />
        </Button>
      </div>
      {def?.description ? <p className="text-xs text-muted-foreground">{def.description}</p> : null}

      {def?.fields.length === 0 ? (
        <p className="text-xs text-muted-foreground">Nessun parametro da configurare.</p>
      ) : (
        def?.fields.map((field) => (
          <ConfigField
            key={field.key}
            field={field}
            value={config[field.key]}
            approvedTemplates={approvedTemplates}
            onChange={(value) => onChange(field.key, value)}
          />
        ))
      )}
    </div>
  );
}

function ConfigField({
  field,
  value,
  approvedTemplates,
  onChange,
}: {
  field: FieldDef;
  value: unknown;
  approvedTemplates: Template[];
  onChange: (value: unknown) => void;
}) {
  const selectClass = 'h-9 w-full rounded-md border border-input bg-background px-2 text-sm';
  return (
    <div className="space-y-1">
      <Label className="text-xs">{field.label}</Label>
      {field.kind === 'text' ? (
        <Input
          placeholder={field.placeholder}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : field.kind === 'number' ? (
        <Input
          type="number"
          placeholder={field.placeholder}
          value={value === undefined || value === null ? '' : String(value)}
          onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        />
      ) : field.kind === 'select' ? (
        <select className={selectClass} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          {(field.options ?? []).map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : field.kind === 'keywords' ? (
        <Input
          placeholder={field.placeholder}
          value={Array.isArray(value) ? value.join(', ') : ''}
          onChange={(e) =>
            onChange(
              e.target.value
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
        />
      ) : field.kind === 'template' ? (
        <select className={selectClass} value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          <option value="">— seleziona —</option>
          {approvedTemplates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}
