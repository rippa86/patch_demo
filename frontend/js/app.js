import { Account, Auth, Cart } from "./api.js";

const $ = (id) => document.getElementById(id);

function money(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

function show(el, text, ok) {
  el.textContent = text || "";
  el.className = "msg " + (ok ? "ok" : "bad");
}

function renderAuth() {
  const loggedIn = Boolean(Auth.token());
  $("login-form").hidden = loggedIn;
  $("session").hidden = !loggedIn;
  $("cart-panel").hidden = !loggedIn;
}

async function loadProducts() {
  const products = await Cart.products();
  const root = $("products");
  root.innerHTML = "";
  products.forEach((product) => {
    const row = document.createElement("div");
    row.className = "product";
    // Built with textContent: product names now come from the admin form.
    const info = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = product.name;
    const desc = document.createElement("div");
    desc.className = "msg";
    desc.textContent = product.description;
    const price = document.createElement("div");
    price.className = "price";
    price.textContent = `${money(product.price_cents)} · ${product.stock} in stock`;
    info.append(name, desc, price);
    row.appendChild(info);
    const btn = document.createElement("button");
    btn.textContent = "Add";
    btn.disabled = !Auth.token();
    btn.addEventListener("click", () => addToCart(product.id));
    row.appendChild(btn);
    root.appendChild(row);
  });
}

async function addToCart(productId) {
  const cart = await Cart.get();
  const items = cart.items.map((item) => ({ product_id: item.product_id, qty: item.qty }));
  const found = items.find((item) => item.product_id === productId);
  if (found) found.qty += 1;
  else items.push({ product_id: productId, qty: 1 });
  await Cart.put(items);
  await loadCart();
}

async function loadCart() {
  if (!Auth.token()) {
    $("cart-items").innerHTML = "";
    return;
  }
  const cart = await Cart.get();
  const root = $("cart-items");
  root.innerHTML = "";
  if (!cart.items.length) {
    root.textContent = "Cart is empty.";
    return;
  }
  cart.items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "cart-item";
    const label = document.createElement("span");
    label.textContent = `${item.name} × ${item.qty}`;
    const total = document.createElement("span");
    total.textContent = money(item.price_cents * item.qty);
    row.append(label, total);
    root.appendChild(row);
  });
}

if ($("login-form")) {
  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await Auth.login({
        username: $("login-user").value,
        password: $("login-pass").value,
      });
      show($("login-msg"), "Signed in.", true);
      renderAuth();
      await loadProducts();
      await loadCart();
    } catch (err) {
      show($("login-msg"), err.message, false);
    }
  });
}

if ($("logout")) {
  $("logout").addEventListener("click", async () => {
    await Auth.logout();
    renderAuth();
    show($("login-msg"), "Signed out.", true);
    await loadProducts();
  });
}

if ($("checkout")) {
  $("checkout").addEventListener("click", async () => {
    try {
      const order = await Cart.checkout();
      show($("cart-msg"), `Order ${order.order_id} placed.`, true);
      await loadCart();
      await loadProducts();
    } catch (err) {
      show($("cart-msg"), err.message, false);
    }
  });
}

if ($("account-form")) {
  $("account-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const result = await Account.create({
        username: $("acc-user").value,
        password: $("acc-pass").value,
        email: $("acc-email").value,
      });
      show($("account-msg"), `Account ${result.username} created. You can sign in.`, true);
    } catch (err) {
      show($("account-msg"), err.message, false);
    }
  });
}

renderAuth();
if ($("products")) {
  loadProducts().catch((err) => show($("shop-msg"), err.message, false));
  if (Auth.token()) loadCart().catch((err) => show($("cart-msg"), err.message, false));
}
