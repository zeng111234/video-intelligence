// jsdom does not implement pseudo-element layout or media playback. Ant Design
// probes both while rendering otherwise testable controls, so provide the
// browser-compatible no-op surface instead of flooding release logs with false
// runtime errors.
if (typeof window !== "undefined") {
  const getComputedStyleWithoutPseudo = window.getComputedStyle.bind(window);
  Object.defineProperty(window, "getComputedStyle", {
    configurable: true,
    value: (element: Element) => getComputedStyleWithoutPseudo(element),
  });
}

if (typeof HTMLMediaElement !== "undefined") {
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    value: () => undefined,
  });
}
