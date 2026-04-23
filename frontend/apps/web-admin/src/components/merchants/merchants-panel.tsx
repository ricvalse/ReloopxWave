'use client';

import { useState } from 'react';
import { Button, PageHeader } from '@reloop/ui';
import { CreateMerchantForm } from './create-merchant-form';
import { MerchantList } from './merchant-list';

export function MerchantsPanel() {
  const [creating, setCreating] = useState(false);

  return (
    <>
      <PageHeader
        title="Merchant"
        description="UC-10/11/12 — lista, onboarding, sospensione dei merchant del tenant."
        actions={
          <Button onClick={() => setCreating((v) => !v)} variant={creating ? 'outline' : 'default'}>
            {creating ? 'Annulla' : '+ Nuovo merchant'}
          </Button>
        }
      />
      {creating ? <CreateMerchantForm onClose={() => setCreating(false)} /> : null}
      <MerchantList />
    </>
  );
}
