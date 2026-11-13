// Same-origin API client. Adds the CSRF header to mutating requests, redirects
// to /login on 401 and turns `{detail}` error bodies into ApiError messages.

export class ApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

export function readCookie(name) {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      try {
        return decodeURIComponent(trimmed.slice(prefix.length));
      } catch {
        return trimmed.slice(prefix.length);
      }
    }
  }
  return '';
}

let redirecting = false;
function goToLogin() {
  if (redirecting) return;
  redirecting = true;
  const here = `${window.location.pathname}${window.location.search}`;
  window.location.assign(here === '/' ? '/login' : `/login?next=${encodeURIComponent(here)}`);
}

function errorMessage(data, response) {
  if (data && typeof data.detail === 'string' && data.detail) return data.detail;
  if (data && Array.isArray(data.detail)) {
    return data.detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
  }
  if (response.status === 413) return 'The file is larger than the server allows.';
  if (response.status >= 500) return `The server returned an error (${response.status}).`;
  return response.statusText || `Request failed (${response.status})`;
}

/**
 * request(method, path, {body, json, signal, raw}) → parsed JSON (or Response when raw).
 */
export async function request(method, path, { json, body, signal, raw = false, headers = {} } = {}) {
  const init = { method, credentials: 'same-origin', signal, headers: new Headers(headers) };
  if (method !== 'GET' && method !== 'HEAD') {
    init.headers.set('x-csrf-token', readCookie('subgen_csrf'));
  }
  if (json !== undefined) {
    init.headers.set('content-type', 'application/json');
    init.body = JSON.stringify(json);
  } else if (body !== undefined) {
    init.body = body;
  }
  init.headers.set('accept', 'application/json');

  let response;
  try {
    response = await fetch(path, init);
  } catch (err) {
    if (err.name === 'AbortError') throw err;
    throw new ApiError('Cannot reach the SubGen server. Check that it is running.', 0, null);
  }
  if (response.status === 401) {
    goToLogin();
    throw new ApiError('Your session has expired. Sign in again.', 401, null);
  }
  if (raw && response.ok) return response;

  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!response.ok) throw new ApiError(errorMessage(data, response), response.status, data);
  return data;
}

export const api = {
  get: (path, opts) => request('GET', path, opts),
  post: (path, json, opts = {}) => request('POST', path, { ...opts, json }),
  put: (path, json, opts = {}) => request('PUT', path, { ...opts, json }),
  del: (path, opts) => request('DELETE', path, opts),
};

/** Build a query string, skipping empty values. */
export function qs(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : '';
}

/**
 * Upload raw bytes with progress. Returns {promise, abort}.
 * onProgress receives (loadedBytes, totalBytes).
 */
export function uploadFile(file, onProgress) {
  const xhr = new XMLHttpRequest();
  let aborted = false;
  const cancelled = () => {
    const err = new Error('Upload cancelled');
    err.name = 'AbortError';
    return err;
  };
  // Ask first (size limit, free disk space): a refusal sent mid-upload often reaches the browser
  // as a dropped connection instead of a readable message.
  const promise = request('GET', `/api/uploads/space${qs({ size: file.size })}`).then(() => {
    if (aborted) throw cancelled();
    return send();
  });
  const send = () => new Promise((resolve, reject) => {
    xhr.open('POST', `/api/uploads${qs({ filename: file.name })}`);
    xhr.setRequestHeader('x-csrf-token', readCookie('subgen_csrf'));
    xhr.setRequestHeader('content-type', 'application/octet-stream');
    xhr.setRequestHeader('accept', 'application/json');
    xhr.upload.onprogress = (event) => {
      if (onProgress) onProgress(event.loaded, event.lengthComputable ? event.total : file.size);
    };
    xhr.onload = () => {
      let data = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = null;
      }
      if (xhr.status === 401) {
        goToLogin();
        reject(new ApiError('Your session has expired. Sign in again.', 401, null));
      } else if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data);
      } else {
        const message =
          (data && typeof data.detail === 'string' && data.detail) ||
          (xhr.status === 413 ? 'The file is larger than the server allows.' : `Upload failed (${xhr.status})`);
        reject(new ApiError(message, xhr.status, data));
      }
    };
    xhr.onerror = () => reject(new ApiError('The upload was interrupted. Check the connection and try again.', 0, null));
    xhr.onabort = () => reject(cancelled());
    xhr.send(file);
  });
  return {
    promise,
    abort: () => {
      aborted = true;
      xhr.abort();
    },
  };
}

/** POST/GET a file endpoint and save the response as a download. */
export async function downloadBlob(method, path, fallbackName) {
  const response = await request(method, path, { raw: true, headers: { accept: '*/*' } });
  const blob = await response.blob();
  const name = filenameFrom(response.headers.get('content-disposition')) || fallbackName;
  saveBlob(blob, name);
}

export function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function filenameFrom(header) {
  if (!header) return '';
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''));
    } catch {
      /* fall through */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : '';
}
