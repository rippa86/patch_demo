import { Admin, Auth } from "./api.js";

const $ = (id) => document.getElementById(id);

function money(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

function show(el, text, ok) {
  el.textContent = text || "";
  el.className = "msg " + (ok ? "ok" : "bad");
}

function view(name) {
  $("admin-login").hidden = name !== "login";
  $("admin-panel").hidden = name !== "panel";
  $("admin-denied").hidden = name !== "denied";
}

function cell(text, cls) {
  const td = document.createElement("td");
  td.textContent = text;
  if (cls) td.className = cls;
  return td;
}

async function loadCatalog() {
  const products = await Admin.products();
  const body = $("catalog");
  body.innerHTML = "";
  products.forEach((p) => {
    const tr = document.createElement("tr");
    tr.appendChild(cell(p.sku, "col-sku"));
    tr.appendChild(cell(p.name));
    tr.appendChild(cell(money(p.price_cents), "num"));
    tr.appendChild(cell(String(p.stock), p.stock < 10 ? "num stock-low" : "num"));

    const td = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "restock";
    const qty = document.createElement("input");
    qty.type = "number";
    qty.min = "1";
    qty.step = "1";
    qty.value = "10";
    qty.setAttribute("aria-label", `Restock quantity for ${p.name}`);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = "Add";
    btn.addEventListener("click", async () => {
      const n = parseInt(qty.value, 10);
      if (!Number.isInteger(n) || n < 1) {
        show($("catalog-msg"), "Restock quantity must be 1 or more.", false);
        return;
      }
      btn.disabled = true;
      try {
        const updated = await Admin.restock(p.id, n);
        show($("catalog-msg"), `${updated.name}: +${n}, now ${updated.stock} in stock.`, true);
        await refresh();
      } catch (err) {
        handleError(err, $("catalog-msg"));
      } finally {
        btn.disabled = false;
      }
    });
    wrap.appendChild(qty);
    wrap.appendChild(btn);
    td.appendChild(wrap);
    tr.appendChild(td);
    body.appendChild(tr);
  });
}

async function loadLog() {
  const [entries, products] = await Promise.all([Admin.log(15), Admin.products()]);
  const names = Object.fromEntries(products.map((p) => [p.id, p.name]));
  const root = $("inv-log");
  root.innerHTML = "";
  if (!entries.length) {
    root.textContent = "No admin changes yet.";
    return;
  }
  entries.forEach((e) => {
    const row = document.createElement("div");
    row.className = "cart-item";
    const when = e.created_at ? new Date(e.created_at).toLocaleString() : "";
    const left = document.createElement("span");
    left.textContent = `${e.action} ${names[e.product_id] || "#" + e.product_id} (+${e.delta}) by ${e.uid}`;
    const right = document.createElement("span");
    right.textContent = when;
    row.appendChild(left);
    row.appendChild(right);
    root.appendChild(row);
  });
}

async function refresh() {
  await loadCatalog();
  await loadLog();
}

function handleError(err, el) {
  if (err.status === 401) {
    Auth.logout().finally(() => {
      view("login");
      show($("admin-login-msg"), "Session expired. Sign in again.", false);
    });
    return;
  }
  show(el, err.message, false);
}

async function start() {
  if (!Auth.token()) {
    view("login");
    return;
  }
  try {
    const me = await Admin.me();
    $("admin-who").textContent = `Signed in as ${me.uid}`;
    view("panel");
    await refresh();
  } catch (err) {
    if (err.status === 403) {
      view("denied");
      show($("denied-msg"), "This account is not an admin.", false);
    } else if (err.status === 401) {
      await Auth.logout();
      view("login");
    } else {
      view("panel");
      show($("catalog-msg"), err.message, false);
    }
  }
}

$("admin-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await Auth.login({ username: $("admin-user").value, password: $("admin-pass").value });
    $("admin-pass").value = "";
    show($("admin-login-msg"), "", true);
    await start();
  } catch (err) {
    show($("admin-login-msg"), err.message, false);
  }
});

$("create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const price = Math.round(parseFloat($("new-price").value) * 100);
  try {
    const p = await Admin.create({
      sku: $("new-sku").value.trim(),
      name: $("new-name").value.trim(),
      description: $("new-desc").value.trim(),
      price_cents: price,
      stock: parseInt($("new-stock").value, 10),
    });
    show($("create-msg"), `Created ${p.sku} (${p.name}) with ${p.stock} in stock.`, true);
    event.target.reset();
    $("new-stock").value = "25";
    await refresh();
  } catch (err) {
    handleError(err, $("create-msg"));
  }
});

for (const id of ["admin-logout", "denied-logout"]) {
  $(id).addEventListener("click", async () => {
    await Auth.logout();
    view("login");
  });
}

start();
