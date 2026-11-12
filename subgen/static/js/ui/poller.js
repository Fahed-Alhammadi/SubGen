// Visibility-aware polling that never overlaps requests.
//
//   const p = createPoller(async () => {...}, 3000); p.start(); ... p.stop();
//
// - runs immediately on start()
// - schedules the next tick only after the previous one settled
// - pauses while document.hidden and runs right away when the tab is visible again
// - `trigger()` runs now (coalesced with an in-flight run)

export function createPoller(task, interval = 3000) {
  let timer = null;
  let running = false;
  let active = false;
  let pendingTrigger = false;

  const schedule = () => {
    clearTimeout(timer);
    if (!active || document.hidden) return;
    const ms = typeof interval === 'function' ? interval() : interval;
    if (ms === null || ms === undefined || ms === false) return;
    timer = setTimeout(tick, ms);
  };

  async function tick() {
    clearTimeout(timer);
    if (!active) return;
    if (running) {
      pendingTrigger = true;
      return;
    }
    running = true;
    try {
      await task();
    } catch (err) {
      // Swallowed: the task is expected to surface its own errors.
      if (err && err.name !== 'AbortError') console.warn('poll failed', err);
    } finally {
      running = false;
    }
    if (pendingTrigger) {
      pendingTrigger = false;
      tick();
      return;
    }
    schedule();
  }

  const onVisibility = () => {
    if (!active) return;
    if (document.hidden) clearTimeout(timer);
    else tick();
  };

  return {
    start() {
      if (active) return;
      active = true;
      document.addEventListener('visibilitychange', onVisibility);
      tick();
    },
    stop() {
      active = false;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    },
    trigger() {
      if (active) tick();
    },
    /** Re-arm the timer (e.g. when the interval function's answer changed). */
    reschedule() {
      if (active && !running) schedule();
    },
  };
}
