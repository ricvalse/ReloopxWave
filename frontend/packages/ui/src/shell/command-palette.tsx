'use client';

import type { Route } from 'next';
import { useRouter } from 'next/navigation';
import { LogOut, Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '../components/ui/command';
import { Dialog, DialogContent } from '../components/ui/dialog';
import { useSidebar } from './sidebar-context';

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSignOut?: () => void;
}

export function CommandPalette({ open, onOpenChange, onSignOut }: CommandPaletteProps) {
  const router = useRouter();
  const { nav } = useSidebar();
  const { resolvedTheme, setTheme } = useTheme();

  const allItems = nav.flatMap((s) => s.items);

  function navigate(href: string) {
    onOpenChange(false);
    router.push(href as Route);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="p-0 sm:max-w-[520px]">
        <Command className="rounded-lg border-0 shadow-none">
          <CommandInput placeholder="Cerca pagina o azione…" autoFocus />
          <CommandList>
            <CommandEmpty>Nessun risultato.</CommandEmpty>
            {allItems.length > 0 && (
              <CommandGroup heading="Navigazione">
                {allItems.map((item) => (
                  <CommandItem
                    key={item.href}
                    onSelect={() => navigate(item.href)}
                    className="gap-2"
                  >
                    <item.icon className="h-4 w-4 text-muted-foreground" />
                    {item.label}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
            <CommandSeparator />
            <CommandGroup heading="Azioni">
              <CommandItem
                onSelect={() => {
                  setTheme(resolvedTheme === 'dark' ? 'light' : 'dark');
                  onOpenChange(false);
                }}
                className="gap-2"
              >
                {resolvedTheme === 'dark' ? (
                  <Sun className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <Moon className="h-4 w-4 text-muted-foreground" />
                )}
                {resolvedTheme === 'dark' ? 'Passa a light mode' : 'Passa a dark mode'}
              </CommandItem>
              {onSignOut && (
                <CommandItem
                  onSelect={() => {
                    onOpenChange(false);
                    onSignOut();
                  }}
                  className="gap-2 text-destructive data-[selected=true]:text-destructive"
                >
                  <LogOut className="h-4 w-4" />
                  Esci
                </CommandItem>
              )}
            </CommandGroup>
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
