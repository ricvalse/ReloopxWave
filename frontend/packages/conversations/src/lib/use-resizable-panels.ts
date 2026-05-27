'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Drag-to-resize state for the inbox's left (thread list) and right (detail)
 * rails, persisted to localStorage. Widths are clamped to sane bounds; the
 * centre thread column always flexes to fill what's left.
 *
 * Implementation note: we resize by pointer *delta* from the mousedown anchor
 * (not absolute clientX) so the hook needs no container ref and behaves the
 * same regardless of where the layout sits on the page. During a drag we set
 * body cursor/user-select so the whole window shows the resize affordance and
 * text doesn't get selected mid-drag.
 */
const LEFT_MIN = 280;
const LEFT_MAX = 460;
const LEFT_DEFAULT = 340;

const RIGHT_MIN = 300;
const RIGHT_MAX = 480;
const RIGHT_DEFAULT = 360;

const LS_LEFT = 'reloop-inbox-left-width';
const LS_RIGHT = 'reloop-inbox-right-width';
const LS_DETAIL_COLLAPSED = 'reloop-inbox-detail-collapsed';

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function readNumber(key: string, fallback: number, min: number, max: number): number {
  if (typeof window === 'undefined') return fallback;
  const raw = window.localStorage.getItem(key);
  if (!raw) return fallback;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) ? clamp(n, min, max) : fallback;
}

type Side = 'left' | 'right' | null;

export function useResizablePanels(detailEnabled: boolean) {
  const [leftWidth, setLeftWidth] = useState(LEFT_DEFAULT);
  const [rightWidth, setRightWidth] = useState(RIGHT_DEFAULT);
  const [detailOpen, setDetailOpen] = useState(true);
  const [resizing, setResizing] = useState<Side>(null);

  // Hydrate from localStorage after mount (avoids SSR hydration mismatch).
  useEffect(() => {
    setLeftWidth(readNumber(LS_LEFT, LEFT_DEFAULT, LEFT_MIN, LEFT_MAX));
    setRightWidth(readNumber(LS_RIGHT, RIGHT_DEFAULT, RIGHT_MIN, RIGHT_MAX));
    if (typeof window !== 'undefined') {
      setDetailOpen(window.localStorage.getItem(LS_DETAIL_COLLAPSED) !== 'true');
    }
  }, []);

  const dragRef = useRef<{ side: Exclude<Side, null>; startX: number; startWidth: number } | null>(
    null,
  );

  useEffect(() => {
    if (!resizing) return;

    function onMove(e: MouseEvent) {
      const drag = dragRef.current;
      if (!drag) return;
      const delta = e.clientX - drag.startX;
      if (drag.side === 'left') {
        setLeftWidth(clamp(drag.startWidth + delta, LEFT_MIN, LEFT_MAX));
      } else {
        // Dragging the right divider leftwards widens the detail panel.
        setRightWidth(clamp(drag.startWidth - delta, RIGHT_MIN, RIGHT_MAX));
      }
    }

    function onUp() {
      const drag = dragRef.current;
      dragRef.current = null;
      setResizing(null);
      if (typeof window !== 'undefined' && drag) {
        if (drag.side === 'left') {
          window.localStorage.setItem(LS_LEFT, String(leftWidthRef.current));
        } else {
          window.localStorage.setItem(LS_RIGHT, String(rightWidthRef.current));
        }
      }
    }

    const prevCursor = document.body.style.cursor;
    const prevSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = prevCursor;
      document.body.style.userSelect = prevSelect;
    };
  }, [resizing]);

  // Keep latest widths readable inside the mouseup persistence closure.
  const leftWidthRef = useRef(leftWidth);
  const rightWidthRef = useRef(rightWidth);
  leftWidthRef.current = leftWidth;
  rightWidthRef.current = rightWidth;

  const startLeftResize = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragRef.current = { side: 'left', startX: e.clientX, startWidth: leftWidthRef.current };
      setResizing('left');
    },
    [],
  );

  const startRightResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { side: 'right', startX: e.clientX, startWidth: rightWidthRef.current };
    setResizing('right');
  }, []);

  const setDetailOpenPersisted = useCallback((open: boolean) => {
    setDetailOpen(open);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(LS_DETAIL_COLLAPSED, open ? 'false' : 'true');
    }
  }, []);

  const toggleDetail = useCallback(() => {
    setDetailOpenPersisted(!detailOpen);
  }, [detailOpen, setDetailOpenPersisted]);

  return {
    leftWidth,
    rightWidth,
    detailOpen: detailEnabled && detailOpen,
    setDetailOpen: setDetailOpenPersisted,
    toggleDetail,
    startLeftResize,
    startRightResize,
    isResizing: resizing !== null,
  };
}
