export * from './env';

export const appBrand = {
  admin: {
    name: 'Admin',
    primaryColor: 'hsl(221 83% 53%)',
  },
  merchant: {
    name: 'Merchant',
    primaryColor: 'hsl(160 84% 39%)',
  },
} as const;

export type AppKind = keyof typeof appBrand;

export const defaultLocale = 'it';
