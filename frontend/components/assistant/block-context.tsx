"use client";

/**
 * Lets a form inside a collapsible card tell the card it was saved, so the card
 * closes and keeps a visible "Guardado" mark in its header. Outside a card the
 * hook returns `null` and the form just stays as it is.
 */

import { createContext, useContext } from "react";

export interface BlockHandle {
  /** The block's data was saved: collapse it and mark it. */
  saved: () => void;
}

export const BlockContext = createContext<BlockHandle | null>(null);

export function useBlock(): BlockHandle | null {
  return useContext(BlockContext);
}
