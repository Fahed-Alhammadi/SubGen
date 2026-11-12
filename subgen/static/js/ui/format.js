// Formatting helpers. Every function tolerates null/undefined input.

const pad = (n, w = 2) => String(n).padStart(w, '0');

/** 5400000 → "1:30:00", 65000 → "1:05". Short form for display. */
export function clock(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(Number(ms))) return '—';
  const total = Math.max(0, Math.floor(Number(ms) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** SRT style timecode HH:MM:SS,mmm */
export function timecode(ms) {
  const v = Math.max(0, Math.round(Number(ms) || 0));
  const h = Math.floor(v / 3600000);
  const m = Math.floor((v % 3600000) / 60000);
  const s = Math.floor((v % 60000) / 1000);
  return `${pad(h)}:${pad(m)}:${pad(s)},${pad(v % 1000, 3)}`;
}

/**
 * Parse "HH:MM:SS,mmm" (also accepts "." as separator, "MM:SS,mmm", "SS.mmm"
 * and a bare number of seconds). Returns ms or null when invalid.
 */
export function parseTimecode(input) {
  const s = String(input ?? '').trim();
  if (!s) return null;
  const m = /^(?:(\d{1,3}):)?(?:(\d{1,2}):)?(\d{1,2})(?:[,.](\d{1,3}))?$/.exec(s);
  if (!m) return null;
  let [, a, b, sec, frac] = m;
  let hours = 0;
  let minutes = 0;
  if (a !== undefined && b !== undefined) {
    hours = Number(a);
    minutes = Number(b);
  } else if (a !== undefined) {
    minutes = Number(a);
  }
  const seconds = Number(sec);
  if (minutes > 59 || (seconds > 59 && (a !== undefined || b !== undefined))) return null;
  const millis = frac ? Number(frac.padEnd(3, '0')) : 0;
  return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis;
}

/** Human duration: 266000 → "4m 26s", 5400000 → "1h 30m". */
export function duration(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(Number(ms))) return '—';
  const total = Math.max(0, Math.round(Number(ms) / 1000));
  if (total < 60) return `${total}s`;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return m ? `${h}h ${m}m` : `${h}h`;
  return s ? `${m}m ${s}s` : `${m}m`;
}

export function bytes(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v < 0) return '—';
  if (v < 1000) return `${v} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = v / 1000;
  let i = 0;
  while (value >= 1000 && i < units.length - 1) {
    value /= 1000;
    i += 1;
  }
  return `${value >= 100 ? value.toFixed(0) : value.toFixed(1)} ${units[i]}`;
}

export function number(n) {
  const v = Number(n);
  return Number.isFinite(v) ? v.toLocaleString() : '—';
}

export function speed(x) {
  const v = Number(x);
  if (x === null || x === undefined || !Number.isFinite(v) || v <= 0) return '';
  return `${v >= 10 ? v.toFixed(0) : v.toFixed(1)}× realtime`;
}

export function toDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function dateTime(iso) {
  const d = toDate(iso);
  if (!d) return '—';
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const rtf = typeof Intl !== 'undefined' && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' }) : null;

/** "3 minutes ago" / "in 5 hours" */
export function relative(iso, now = Date.now()) {
  const d = toDate(iso);
  if (!d) return '—';
  const diff = (d.getTime() - now) / 1000;
  const abs = Math.abs(diff);
  const table = [
    [60, 'second', 1],
    [3600, 'minute', 60],
    [86400, 'hour', 3600],
    [604800, 'day', 86400],
    [2629800, 'week', 604800],
    [31557600, 'month', 2629800],
    [Infinity, 'year', 31557600],
  ];
  for (const [limit, unit, div] of table) {
    if (abs < limit) {
      const value = Math.round(diff / div);
      if (unit === 'second' && abs < 10) return 'just now';
      return rtf ? rtf.format(value, unit) : `${value} ${unit}s`;
    }
  }
  return dateTime(iso);
}

/** Elapsed between two ISO timestamps (or until now). */
export function elapsed(startIso, endIso) {
  const a = toDate(startIso);
  if (!a) return '—';
  const b = toDate(endIso) || new Date();
  return duration(b.getTime() - a.getTime());
}

export function plural(n, one, many = `${one}s`) {
  return `${number(n)} ${Number(n) === 1 ? one : many}`;
}

export const STATUS_LABELS = {
  queued: 'Queued',
  processing: 'Processing',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export const STAGE_LABELS = {
  queued: 'Waiting',
  loading: 'Loading model',
  probing: 'Reading media',
  extracting: 'Extracting audio',
  transcribing: 'Transcribing',
  cached: 'Reusing transcript',
  translating: 'Translating',
  writing: 'Writing subtitle',
  done: 'Finishing',
};

export const WORKER_LABELS = {
  idle: 'Idle',
  loading: 'Loading model',
  processing: 'Processing',
  error: 'Error',
  offline: 'Unreachable',
};

export function fileExt(name) {
  const m = /\.([^.]+)$/.exec(String(name || ''));
  return m ? m[1].toLowerCase() : '';
}

export const AUDIO_EXTS = new Set(['mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg', 'opus', 'mka', 'wma']);
export const VIDEO_EXTS = new Set(['mkv', 'mp4', 'm4v', 'avi', 'mov', 'ts', 'webm', 'wmv', 'flv', 'mpg', 'mpeg']);

export function isAudio(name) {
  return AUDIO_EXTS.has(fileExt(name));
}

export function dirname(path) {
  const s = String(path || '');
  const i = s.lastIndexOf('/');
  return i > 0 ? s.slice(0, i) : s;
}
