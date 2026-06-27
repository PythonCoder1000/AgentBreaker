// Shared UI setup: React + htm (no build step) and small helpers, imported by
// every component module so they bind to the same `html` / hooks.
import React from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import htm from "https://esm.sh/htm@3.1.1";

export { React, createRoot };
export const e = React.createElement;
export const html = htm.bind(e);
export const { useState, useEffect, useRef, useCallback } = React;

export const AGENTS = ["prompt", "breaker"];

export function newId() {
  // Session ids key per-session state (history, capability token, audit log), so
  // they must be unguessable. Prefer randomUUID; fall back to a 128-bit value from
  // the CSPRNG (never Math.random, which would be predictable on a plain-HTTP host).
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}
