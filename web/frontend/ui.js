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
  return (crypto.randomUUID && crypto.randomUUID()) || String(Math.random());
}

export function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}
