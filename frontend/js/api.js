const TOKEN_KEY = "webstore_token";

function token() {
  return sessionStorage.getItem(TOKEN_KEY);
}

function setToken(value) {
  if (value) sessionStorage.setItem(TOKEN_KEY, value);
  else sessionStorage.removeItem(TOKEN_KEY);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  if (token()) headers.Authorization = `Bearer ${token()}`;
  const response = await fetch(path, { ...options, headers });
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : response.statusText;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = response.status;
    throw err;
  }
  return data;
}

export const Account = {
  create: (body) => api("/api/account", { method: "POST", body: JSON.stringify(body) }),
};

export const Auth = {
  login: async (body) => {
    const data = await api("/api/login", { method: "POST", body: JSON.stringify(body) });
    setToken(data.token);
    return data;
  },
  logout: async () => {
    try {
      await api("/api/logout", { method: "POST" });
    } finally {
      setToken(null);
    }
  },
  token,
};

export const Cart = {
  products: () => api("/api/cart/products"),
  get: () => api("/api/cart"),
  put: (items) => api("/api/cart", { method: "PUT", body: JSON.stringify({ items }) }),
  checkout: () => api("/api/cart/checkout", { method: "POST" }),
};

export const Admin = {
  me: () => api("/api/admin/me"),
  products: () => api("/api/admin/products"),
  create: (product) =>
    api("/api/admin/products", { method: "POST", body: JSON.stringify(product) }),
  restock: (productId, qty) =>
    api(`/api/admin/products/${productId}/restock`, {
      method: "POST",
      body: JSON.stringify({ qty }),
    }),
  log: (limit = 20) => api(`/api/admin/inventory-log?limit=${limit}`),
};
