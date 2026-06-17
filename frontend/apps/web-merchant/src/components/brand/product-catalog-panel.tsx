'use client';

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { components } from '@reloop/api-client';
import {
  Badge,
  Button,
  ButtonSpinner,
  Card,
  CardContent,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  SkeletonCard,
  Textarea,
} from '@reloop/ui';
import { Package, Pencil, Plus, Trash2 } from 'lucide-react';
import { apiErrorMessage, getApiClient } from '@/lib/api';
import { useMerchantId } from '@/hooks/use-merchant-id';

type Product = components['schemas']['ProductOut'];

export function ProductCatalogPanel() {
  const { merchantId } = useMerchantId();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Product | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  // Bump on every open so the form dialog remounts with fresh initial state.
  const [seq, setSeq] = useState(0);

  const query = useQuery({
    queryKey: ['products', merchantId],
    enabled: !!merchantId,
    queryFn: async (): Promise<Product[]> => {
      const api = getApiClient();
      const { data, error } = await api.GET('/catalog/{merchant_id}/products' as never, {
        params: { path: { merchant_id: merchantId } },
      } as never);
      if (error) throw new Error(apiErrorMessage(error));
      return (data as Product[]) ?? [];
    },
  });

  const del = useMutation({
    mutationFn: async (id: string) => {
      const api = getApiClient();
      const { error } = await api.DELETE('/catalog/{merchant_id}/products/{product_id}' as never, {
        params: { path: { merchant_id: merchantId, product_id: id } },
      } as never);
      if (error) throw new Error(apiErrorMessage(error));
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['products', merchantId] }),
    onSettled: () => setConfirmDelete(null),
  });

  const openNew = () => {
    setEditing(null);
    setSeq((s) => s + 1);
    setDialogOpen(true);
  };
  const openEdit = (p: Product) => {
    setEditing(p);
    setSeq((s) => s + 1);
    setDialogOpen(true);
  };

  const products = query.data ?? [];

  return (
    <div className="space-y-4 p-6">
      <div className="flex justify-end">
        <Button onClick={openNew}>
          <Plus className="h-4 w-4" />
          Nuovo prodotto
        </Button>
      </div>

      {query.isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : products.length === 0 ? (
        <Card>
          <CardContent className="py-4">
            <EmptyState
              icon={Package}
              title="Nessun prodotto"
              description="Aggiungi i prodotti principali così il bot può proporli ai clienti."
              action={
                <Button onClick={openNew}>
                  <Plus className="h-4 w-4" />
                  Aggiungi prodotto
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {products.map((p) => (
            <Card key={p.id} className="overflow-hidden">
              {p.images?.[0] ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={p.images[0]}
                  alt={p.title}
                  className="h-36 w-full object-cover"
                />
              ) : (
                <div className="flex h-36 w-full items-center justify-center bg-muted">
                  <Package className="h-6 w-6 text-muted-foreground" />
                </div>
              )}
              <CardContent className="space-y-2 p-4">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium leading-tight">{p.title}</p>
                  {p.price != null ? (
                    <span className="shrink-0 text-sm font-semibold tabular-nums">
                      {p.price} {p.currency}
                    </span>
                  ) : null}
                </div>
                <p className="text-xs text-muted-foreground">
                  {[p.vendor, p.product_type].filter(Boolean).join(' · ') || '—'}
                </p>
                {p.tags?.length ? (
                  <div className="flex flex-wrap gap-1">
                    {p.tags.slice(0, 4).map((t) => (
                      <Badge key={t} variant="secondary">
                        {t}
                      </Badge>
                    ))}
                  </div>
                ) : null}
                <div className="flex items-center gap-3 pt-1">
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => openEdit(p)}
                  >
                    <Pencil className="h-3.5 w-3.5" /> Modifica
                  </button>
                  {confirmDelete === p.id ? (
                    <span className="inline-flex items-center gap-2 text-xs">
                      <button
                        type="button"
                        className="text-destructive hover:underline"
                        onClick={() => del.mutate(p.id)}
                        disabled={del.isPending}
                      >
                        Conferma
                      </button>
                      <button
                        type="button"
                        className="text-muted-foreground hover:underline"
                        onClick={() => setConfirmDelete(null)}
                      >
                        Annulla
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 text-xs text-destructive/80 hover:text-destructive"
                      onClick={() => setConfirmDelete(p.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" /> Elimina
                    </button>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <ProductFormDialog
        key={seq}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        product={editing}
        merchantId={merchantId}
      />
    </div>
  );
}

function ProductFormDialog({
  open,
  onOpenChange,
  product,
  merchantId,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  product: Product | null;
  merchantId: string | null;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  // Keyed remount (below) gives a fresh form per open; initialise from product.
  const [title, setTitle] = useState(product?.title ?? '');
  const [description, setDescription] = useState(product?.description ?? '');
  const [vendor, setVendor] = useState(product?.vendor ?? '');
  const [productType, setProductType] = useState(product?.product_type ?? '');
  const [tags, setTags] = useState((product?.tags ?? []).join('\n'));
  const [images, setImages] = useState((product?.images ?? []).join('\n'));
  const [price, setPrice] = useState(product?.price != null ? String(product.price) : '');

  const lines = (s: string) =>
    s
      .split('\n')
      .map((x) => x.trim())
      .filter(Boolean);

  const save = useMutation({
    mutationFn: async () => {
      if (!merchantId) throw new Error('Merchant context mancante');
      const body = {
        title: title.trim(),
        description: description.trim() || null,
        vendor: vendor.trim() || null,
        product_type: productType.trim() || null,
        tags: lines(tags),
        images: lines(images),
        variants: product?.variants ?? [],
        price: price.trim() ? Number(price) : null,
        is_active: true,
      };
      const api = getApiClient();
      if (product) {
        const { error } = await api.PUT(
          '/catalog/{merchant_id}/products/{product_id}' as never,
          {
            params: { path: { merchant_id: merchantId, product_id: product.id } },
            body,
          } as never,
        );
        if (error) throw new Error(apiErrorMessage(error));
      } else {
        const { error } = await api.POST('/catalog/{merchant_id}/products' as never, {
          params: { path: { merchant_id: merchantId } },
          body,
        } as never);
        if (error) throw new Error(apiErrorMessage(error));
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['products', merchantId] });
      onOpenChange(false);
    },
    onError: (e) => setError(apiErrorMessage(e)),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{product ? 'Modifica prodotto' : 'Nuovo prodotto'}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="p-title">Titolo</Label>
            <Input
              id="p-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Es: Abito midi in seta"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="p-desc">Descrizione</Label>
            <Textarea
              id="p-desc"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Materiali, vestibilità, dettagli che il bot può citare."
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="p-vendor">Marca</Label>
              <Input id="p-vendor" value={vendor} onChange={(e) => setVendor(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="p-type">Tipo</Label>
              <Input
                id="p-type"
                value={productType}
                onChange={(e) => setProductType(e.target.value)}
                placeholder="Es: Abiti, Scarpe"
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="p-price">Prezzo (EUR)</Label>
              <Input
                id="p-price"
                type="number"
                min={0}
                step="0.01"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="159.00"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="p-tags">Tag (uno per riga)</Label>
              <Textarea
                id="p-tags"
                rows={3}
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder={'seta\nelegante'}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="p-images">URL immagini (uno per riga)</Label>
            <Textarea
              id="p-images"
              rows={2}
              value={images}
              onChange={(e) => setImages(e.target.value)}
              placeholder="https://…/foto.jpg"
            />
          </div>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={save.isPending}>
            Annulla
          </Button>
          <Button onClick={() => save.mutate()} disabled={!title.trim() || save.isPending}>
            {save.isPending ? (
              <>
                <ButtonSpinner />
                Salvataggio…
              </>
            ) : (
              'Salva'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
