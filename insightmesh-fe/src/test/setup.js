// src/test/setup.js
// Vitest global setup — runs before every test file.
import "@testing-library/jest-dom";

// Polyfill localStorage so components that use it don't blow up in jsdom
// (jsdom provides it, but tests can override here if needed)

// Polyfill matchMedia for components that check it (Recharts etc.)
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

// Polyfill ResizeObserver (Recharts needs it)
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
