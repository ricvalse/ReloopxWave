import { FlatCompat } from '@eslint/eslintrc';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const compat = new FlatCompat({ baseDirectory: __dirname });

const config = [
  {
    ignores: ['.next/**', 'node_modules/**', 'out/**', 'next-env.d.ts'],
  },
  ...compat.extends('next/core-web-vitals', 'next/typescript'),
  {
    // config files (eslint.config.mjs, postcss.config.mjs, next.config.ts)
    // legitimately export a single config object — don't flag them.
    files: ['*.config.{js,mjs,cjs,ts}', '*.config.mts'],
    rules: {
      'import/no-anonymous-default-export': 'off',
    },
  },
];

export default config;
