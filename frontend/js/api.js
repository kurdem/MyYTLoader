// API-Client und Live-Ereignisse (Server-Sent Events)

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export async function api(path, { method = 'GET', body, form } = {}) {
  const headers = { 'X-Requested-With': 'ytloader' };
  let payload;
  if (form) {
    payload = form;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: 'same-origin' });
  } catch {
    throw new ApiError('Server nicht erreichbar. Bitte Verbindung prüfen.', 0);
  }
  let data = null;
  const type = res.headers.get('content-type') || '';
  if (type.includes('application/json')) {
    data = await res.json().catch(() => null);
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/auth/')) onUnauthorized();
    throw new ApiError((data && data.detail) || `Fehler ${res.status}`, res.status);
  }
  return data;
}

/** Upload mit Fortschritt (fetch kann keinen Upload-Fortschritt melden). */
export function upload(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.setRequestHeader('X-Requested-With', 'ytloader');
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError((data && data.detail) || `Fehler ${xhr.status}`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError('Upload fehlgeschlagen.', 0));
    xhr.send(form);
  });
}

/** Ereignisstrom mit automatischem Reconnect; onOpen dient zur Resynchronisierung. */
export function connectEvents({ onEvent, onOpen }) {
  let source = null;
  let retry = null;
  const open = () => {
    clearTimeout(retry);
    if (source) source.close();
    source = new EventSource('/api/events', { withCredentials: true });
    source.addEventListener('hello', () => onOpen && onOpen());
    source.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch { /* ignore */ }
    };
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) {
        retry = setTimeout(open, 5000);
      }
    };
  };
  open();
  // iOS beendet Verbindungen im Hintergrund – beim Zurückkehren neu verbinden
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && (!source || source.readyState !== EventSource.OPEN)) open();
  });
  return () => { clearTimeout(retry); if (source) source.close(); };
}
