'use client';

/**
 * Subtle doodle wallpaper layered behind the message scroll area.
 *
 * The pattern is a single inline SVG encoded as a data URI and tiled by
 * `background-repeat`. We keep it brand-tinted (currentColor) at low opacity so
 * it reads as texture, not decoration — the WhatsApp Web "wallpaper feel"
 * without copying the WhatsApp doodles directly.
 *
 * Mounted as an absolutely-positioned, `pointer-events-none` sibling inside
 * the relatively-positioned scroll container; the messages then sit above it
 * via a higher z-index.
 */
const TILE = `
<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160' viewBox='0 0 160 160' fill='none' stroke='currentColor' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'>
  <circle cx='24' cy='28' r='2.2' fill='currentColor' stroke='none'/>
  <path d='M62 18 q4 6 0 12'/>
  <path d='M110 30 l6 -6 M110 24 l6 6'/>
  <path d='M140 60 q-5 4 0 10 q5 -4 0 -10 z' fill='currentColor' fill-opacity='0.4'/>
  <path d='M30 70 c4 -6 12 -6 16 0'/>
  <circle cx='84' cy='80' r='1.8' fill='currentColor' stroke='none'/>
  <path d='M120 96 l4 4 l-4 4 l-4 -4 z'/>
  <path d='M20 118 q6 -8 14 -2 q-6 8 -14 2 z' fill='currentColor' fill-opacity='0.35'/>
  <path d='M70 130 l4 -4 M70 126 l4 4'/>
  <circle cx='130' cy='140' r='2.2' fill='currentColor' stroke='none'/>
</svg>
`;

const TILE_DATA_URI = `url("data:image/svg+xml;utf8,${encodeURIComponent(TILE.trim())}")`;

export function ChatWallpaper() {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 text-foreground opacity-[0.07] dark:opacity-[0.09]"
      style={{
        backgroundImage: TILE_DATA_URI,
        backgroundRepeat: 'repeat',
        backgroundSize: '160px 160px',
      }}
    />
  );
}
