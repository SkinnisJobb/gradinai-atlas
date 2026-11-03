// @ts-check
// core/prefs.js — per-viewer conveniences that survive a reload.
//
// Same contract as legacy's remember/recall (util.js): never throw. Private
// mode, a full quota and blocked storage all read as "nothing stored". The
// difference is the namespace. v2 keeps its own keys under `atlas.v2.*`, so it
// cannot overwrite a choice the legacy board depends on. The two boards share
// only what both must agree on, the theme and the text size, and `SHARED`
// lists those so the exception has to be written down.

const PREFIX = "atlas.v2.";

/** Keys read under their legacy names, so both boards honour one choice. */
export const SHARED = Object.freeze({ theme: "atlas.theme", uiScale: "atlas.uiScale" });

/**
 * @param {Pick<Storage, "getItem" | "setItem" | "removeItem"> | null} storage
 */
export function createPrefs(storage) {
  /** @param {string} key */
  const full = (key) => (key in SHARED ? SHARED[/** @type {keyof typeof SHARED} */ (key)] : PREFIX + key);
  return {
    /**
     * @template T
     * @param {string} key
     * @param {T} fallback
     * @returns {T}
     */
    recall(key, fallback) {
      try {
        const raw = storage ? storage.getItem(full(key)) : null;
        if (raw == null) return fallback;
        // Legacy wrote plain strings ("dark", "1.25"). Accept them as they are.
        try { return JSON.parse(raw); } catch { return /** @type {T} */ (/** @type {unknown} */ (raw)); }
      } catch {
        return fallback;
      }
    },
    /**
     * @param {string} key
     * @param {unknown} value  `undefined` removes the key
     */
    remember(key, value) {
      try {
        if (!storage) return;
        if (value === undefined) storage.removeItem(full(key));
        else storage.setItem(full(key), key in SHARED ? String(value) : JSON.stringify(value));
      } catch { /* a convenience that fails to save is not an error */ }
    },
  };
}

/** The browser's storage, or null where even touching it throws. */
function browserStorage() {
  try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; }
}

export const prefs = createPrefs(browserStorage());
