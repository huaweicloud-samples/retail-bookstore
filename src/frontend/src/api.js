const config = {
  get base() { return localStorage.getItem('bookstore.apiBase') || ''; },
  get token() { return localStorage.getItem('bookstore.jwt') || ''; }
};

export function saveConfig(base, token) {
  localStorage.setItem('bookstore.apiBase', base.replace(/\/$/, ''));
  localStorage.setItem('bookstore.jwt', token);
}

export async function call(path, options = {}) {
  if (!config.base) throw new Error('请先填写 API 基地址');
  const headers = { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}) };
  if (config.token) headers.Authorization = `Bearer ${config.token}`;
  const response = await fetch(`${config.base}${path}`, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

export function getConfig() { return config; }
