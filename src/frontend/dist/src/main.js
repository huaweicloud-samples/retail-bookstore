import { call, getConfig, saveConfig } from './api.js';

const $ = (id) => document.getElementById(id);
const content = $('content');
const message = $('message');
let books = [];

function show(text, error = false) {
  message.textContent = text;
  message.className = error ? 'message error' : 'message';
}

function card(book) {
  return `<article class="card"><div class="cover">${book.name.slice(0, 1)}</div><div class="card-body"><span class="tag">${book.category || 'book'}</span><h3>${book.name}</h3><p>${book.description || ''}</p><strong>${Number(book.price).toFixed(2)} CNY</strong><div class="card-actions"><button data-detail="${book.id}">详情</button><button data-cart="${book.id}">加入购物车</button></div></div></article>`;
}

function render(values) {
  books = values;
  content.innerHTML = values.length ? values.map(card).join('') : '<p class="empty">没有找到书籍。</p>';
}

async function loadBooks() {
  try {
    const category = $('category').value;
    render(await call(`/books${category ? `?category=${encodeURIComponent(category)}` : ''}`));
    show(`已加载 ${books.length} 本书`);
  } catch (error) { show(error.message, true); }
}

async function loadCategories() {
  try {
    const categories = await call('/categories');
    $('category').innerHTML = '<option value="">全部分类</option>' + categories.map(x => `<option>${x.name}</option>`).join('');
  } catch (error) { show(error.message, true); }
}

async function showSearch() {
  try { render(await call(`/search?q=${encodeURIComponent($('search').value)}`)); } catch (error) { show(error.message, true); }
}

async function loadCart() {
  try {
    const cart = await call('/cart');
    $('cart').innerHTML = cart.items.length ? cart.items.map(item => `<div class="cart-row"><span>${item.book_id}</span><input data-qty="${item.book_id}" type="number" min="0" value="${item.quantity}"></div>`).join('') : '<p class="muted">购物车为空。请先配置 JWT。</p>';
    document.querySelectorAll('[data-qty]').forEach(input => input.addEventListener('change', async () => { await call(`/cart/${input.dataset.qty}`, { method: 'PUT', body: JSON.stringify({ quantity: Number(input.value) }) }); await loadCart(); }));
  } catch (error) { $('cart').innerHTML = '<p class="muted">登录后查看购物车。</p>'; }
}

async function loadAction(action) {
  try {
    let result;
    if (action === 'bestsellers') result = await call('/bestsellers');
    if (action === 'friends') result = await call('/recommendations/friends');
    if (action === 'orders') result = await call('/orders');
    render(Array.isArray(result) && result[0]?.items ? result.flatMap(x => x.items) : result || []);
    show(action === 'orders' ? `订单 ${result.length} 条` : '推荐结果');
  } catch (error) { show(error.message, true); }
}

content.addEventListener('click', async (event) => {
  const detail = event.target.dataset.detail;
  const bookId = event.target.dataset.cart;
  try {
    if (detail) { render([await call(`/books/${detail}`)]); show('书籍详情'); }
    if (bookId) { await call(`/cart/${bookId}`, { method: 'PUT', body: JSON.stringify({ quantity: 1 }) }); await loadCart(); show('已加入购物车'); }
  } catch (error) { show(error.message, true); }
});

$('saveConfig').addEventListener('click', async () => { saveConfig($('apiBase').value, $('jwt').value); show('配置已保存'); await loadCategories(); await loadBooks(); await loadCart(); });
$('loadBooks').addEventListener('click', loadBooks);
$('searchButton').addEventListener('click', showSearch);
$('category').addEventListener('change', loadBooks);
$('checkout').addEventListener('click', async () => { try { const order = await call('/checkout', { method: 'POST', headers: { 'Idempotency-Key': `ui-${Date.now()}` }, body: JSON.stringify({}) }); show(`结账成功：${order.id}，合计 ${order.total.toFixed(2)} CNY`); await loadCart(); } catch (error) { show(error.message, true); } });
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => loadAction(button.dataset.action)));

$('apiBase').value = getConfig().base;
$('jwt').value = getConfig().token;
if (getConfig().base) { loadCategories(); loadBooks(); loadCart(); }
