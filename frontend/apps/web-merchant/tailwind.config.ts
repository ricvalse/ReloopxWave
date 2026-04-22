import type { Config } from 'tailwindcss';
import preset from '@reloop/ui/tailwind';

const config: Config = {
  presets: [preset],
  content: ['./src/**/*.{ts,tsx}', '../../packages/ui/src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: 'hsl(160 84% 39%)',
          foreground: 'hsl(0 0% 100%)',
        },
      },
    },
  },
};

export default config;
