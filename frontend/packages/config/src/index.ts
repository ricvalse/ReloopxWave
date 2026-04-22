export * from './env';

export const appBrand = {
  admin: {
    name: 'Reloop Admin',
    primaryColor: 'hsl(221 83% 53%)',
  },
  merchant: {
    name: 'Reloop Merchant',
    primaryColor: 'hsl(160 84% 39%)',
  },
} as const;

export type AppKind = keyof typeof appBrand;

export const defaultLocale = 'it';
