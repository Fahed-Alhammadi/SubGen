// Small shared caches and the worker-status channel.
import { api } from './api.js';

let languagesPromise = null;

/** GET /api/languages once per page load. */
export function getLanguages() {
  if (!languagesPromise) {
    languagesPromise = api.get('/api/languages').catch((err) => {
      languagesPromise = null;
      throw err;
    });
  }
  return languagesPromise;
}

export function languageName(languages, code) {
  const found = languages && languages.find((l) => l.code === code);
  return found ? found.name : String(code || '').toUpperCase();
}

// Worker status is published by whichever view fetched /api/dashboard most
// recently, so the shell does not need to poll separately while Overview is open.
const workerListeners = new Set();
export const workerState = { worker: null, jobs: null, at: 0, offline: false };

export function publishDashboard(data) {
  workerState.worker = data.worker || null;
  workerState.jobs = data.jobs || null;
  workerState.at = Date.now();
  workerState.offline = false;
  workerListeners.forEach((fn) => fn(workerState));
}

export function publishOffline() {
  workerState.offline = true;
  workerListeners.forEach((fn) => fn(workerState));
}

export function onWorker(fn) {
  workerListeners.add(fn);
  return () => workerListeners.delete(fn);
}
