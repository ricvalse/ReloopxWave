import type { Config } from 'tailwindcss';
import preset from '@reloop/ui/tailwind';

const config: Config = {
  presets: [preset],
  content: ['./src/**/*.{ts,tsx}', '../../packages/ui/src/**/*.{ts,tsx}'],
};

export default config;
