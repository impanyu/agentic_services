const API = '/kalshi-rec/v1';
const $ = (id) => document.getElementById(id);
const etTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'America/New_York', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const integer = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
let readToken = '';
let tradeQuery = '';
let brtiQuery = '';

function etDate(value) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(value).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function localInput(value) {
  const date = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
  return date.toISOString().slice(0, 16);
}

function message(id, value, error = false) {
  const element = $(id);
  element.textContent = value;
  element.classList.toggle('error', error);
}

async function request(path, format = 'json') {
  const response = await fetch(API + path, {
    headers: { Authorization: `Bearer ${readToken}` },
    cache: 'no-store',
  });
  if (!response.ok) {
    let detail = '';
    try { detail = (await response.json()).detail || ''; } catch (_) { /* HTTP status remains useful. */ }
    throw new Error(response.status === 401 ? 'token 无效或已失效。' : `请求失败（${response.status}）${detail ? `：${detail}` : ''}`);
  }
  return format === 'blob' ? response.blob() : response.json();
}

function setBusy(id, busy) { $(id).disabled = busy; }

function addCell(row, value) {
  const cell = document.createElement('td');
  cell.textContent = value == null ? '—' : String(value);
  row.appendChild(cell);
}

function renderTable(id, rows, values) {
  const body = $(id);
  const fragment = document.createDocumentFragment();
  for (const item of rows.slice(-200).reverse()) {
    const row = document.createElement('tr');
    for (const value of values(item)) addCell(row, value);
    fragment.appendChild(row);
  }
  body.replaceChildren(fragment);
}

function drawChart(id, rows, key, color) {
  const canvas = $(id);
  const width = Math.max(320, Math.floor(canvas.getBoundingClientRect().width));
  const height = 280;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  const context = canvas.getContext('2d');
  context.scale(dpr, dpr);
  context.clearRect(0, 0, width, height);
  const pad = { left: 54, right: 13, top: 16, bottom: 28 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const sampled = rows.length > 1200 ? rows.filter((_, index) => index % Math.ceil(rows.length / 1200) === 0 || index === rows.length - 1) : rows;
  const points = sampled.map((item) => ({ x: Number(item.ts_ms), y: Number(item[key]) })).filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
  if (!points.length) return;
  let lo = Math.min(...points.map((point) => point.y));
  let hi = Math.max(...points.map((point) => point.y));
  if (lo === hi) { lo -= 1; hi += 1; }
  const start = points[0].x;
  const span = Math.max(1, points[points.length - 1].x - start);
  context.font = '11px ui-monospace, monospace';
  context.fillStyle = '#a9b8b5';
  context.strokeStyle = '#34464a';
  context.lineWidth = 1;
  for (let index = 0; index < 4; index++) {
    const y = pad.top + (plotHeight * index) / 3;
    context.beginPath(); context.moveTo(pad.left, y); context.lineTo(width - pad.right, y); context.stroke();
    context.fillText((hi - ((hi - lo) * index) / 3).toFixed(key === 'value' ? 0 : 1), 3, y + 4);
  }
  context.beginPath();
  points.forEach((point, index) => {
    const x = pad.left + ((point.x - start) / span) * plotWidth;
    const y = pad.top + ((hi - point.y) / (hi - lo)) * plotHeight;
    if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
  });
  context.strokeStyle = color;
  context.lineWidth = 2;
  context.stroke();
  context.fillStyle = '#a9b8b5';
  context.fillText(etTime.format(new Date(start)), pad.left, height - 7);
  const endLabel = etTime.format(new Date(points[points.length - 1].x));
  context.fillText(endLabel, Math.max(pad.left, width - pad.right - context.measureText(endLabel).width), height - 7);
}

async function loadTickers() {
  const day = $('trade-day').value;
  if (!day) return;
  const select = $('trade-ticker');
  select.replaceChildren(new Option('正在读取市场…', ''));
  $('trades-results').hidden = true;
  message('trades-message', '正在读取市场列表…');
  try {
    const data = await request(`/btc15m/tickers?day=${encodeURIComponent(day)}`);
    select.replaceChildren(new Option(data.n ? '选择一个市场' : '这一天暂无市场记录', ''));
    for (const ticker of data.tickers.reverse()) select.add(new Option(ticker, ticker));
    message('trades-message', data.n ? `找到 ${data.n} 个市场。请选择市场查看逐笔记录。` : '这一天还没有可查看的市场记录。');
  } catch (error) {
    select.replaceChildren(new Option('读取失败', ''));
    message('trades-message', error.message, true);
  }
}

async function loadTrades() {
  const ticker = $('trade-ticker').value;
  if (!ticker) { message('trades-message', '请先选择一个市场。', true); return; }
  const query = `/btc15m/trades?ticker=${encodeURIComponent(ticker)}`;
  setBusy('load-trades', true);
  $('trades-results').hidden = true;
  message('trades-message', '正在读取逐笔交易…');
  try {
    const data = await request(`${query}&fmt=json`);
    const rows = data.rows;
    const prices = rows.reduce((bounds, row) => {
      const price = Number(row.yes_price);
      return [Math.min(bounds[0], price), Math.max(bounds[1], price)];
    }, [Infinity, -Infinity]);
    const volume = rows.reduce((sum, row) => sum + Number(row.count || 0), 0);
    $('trades-title').textContent = ticker;
    $('trades-count').textContent = integer.format(data.n);
    $('trades-volume').textContent = decimal.format(volume);
    $('trades-range').textContent = rows.length ? `${prices[0].toFixed(1)}–${prices[1].toFixed(1)} ¢` : '—';
    renderTable('trades-body', rows, (row) => [etTime.format(new Date(row.ts_ms)), Number(row.yes_price).toFixed(1), decimal.format(row.count), row.taker_side || '—', row.trade_id]);
    tradeQuery = query;
    $('trades-results').hidden = false;
    requestAnimationFrame(() => drawChart('trades-chart', rows, 'yes_price', '#d6f47e'));
    message('trades-message', data.n ? `已读取 ${integer.format(data.n)} 笔交易。` : '这个市场暂时没有交易记录。');
  } catch (error) { message('trades-message', error.message, true); }
  finally { setBusy('load-trades', false); }
}

async function loadBrti() {
  const start = new Date($('brti-start').value).getTime();
  const end = new Date($('brti-end').value).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) { message('brti-message', '请选择有效的开始和结束时间。', true); return; }
  if (end - start > 6 * 3600000) { message('brti-message', '图表一次最多查看 6 小时。', true); return; }
  const query = `/brti?start_ms=${start}&end_ms=${end}`;
  setBusy('load-brti', true);
  $('brti-results').hidden = true;
  message('brti-message', '正在读取 BRTI 历史值…');
  try {
    const data = await request(`${query}&fmt=json`);
    const rows = data.rows;
    const values = rows.reduce((bounds, row) => {
      const value = Number(row.value);
      return [Math.min(bounds[0], value), Math.max(bounds[1], value)];
    }, [Infinity, -Infinity]);
    $('brti-count').textContent = integer.format(data.n);
    $('brti-latest').textContent = rows.length ? `$${decimal.format(rows[rows.length - 1].value)}` : '—';
    $('brti-range').textContent = rows.length ? `$${decimal.format(values[0])}–$${decimal.format(values[1])}` : '—';
    renderTable('brti-body', rows, (row) => [etTime.format(new Date(row.ts_ms)), `$${decimal.format(row.value)}`, row.avg_60s_value == null ? '—' : `$${decimal.format(row.avg_60s_value)}`, row.avg_60s_window_sz]);
    brtiQuery = query;
    $('brti-results').hidden = false;
    requestAnimationFrame(() => drawChart('brti-chart', rows, 'value', '#bbecaa'));
    message('brti-message', data.n ? `已读取 ${integer.format(data.n)} 条指数记录。` : '这个时段暂无指数记录。');
  } catch (error) { message('brti-message', error.message, true); }
  finally { setBusy('load-brti', false); }
}

async function download(query, name, buttonId, messageId) {
  if (!query) return;
  setBusy(buttonId, true);
  try {
    const blob = await request(`${query}&fmt=parquet`, 'blob');
    const href = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = href; link.download = name; document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  } catch (error) { message(messageId, error.message, true); }
  finally { setBusy(buttonId, false); }
}

$('connect-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  readToken = $('token').value.trim();
  if (!readToken) return;
  setBusy('connect-button', true);
  message('connect-message', '正在验证 token…');
  try {
    await request('/health');
    $('token').value = '';
    $('gate').hidden = true;
    $('workspace').hidden = false;
    await loadTickers();
  } catch (error) { readToken = ''; message('connect-message', error.message, true); }
  finally { setBusy('connect-button', false); }
});

$('disconnect').addEventListener('click', () => {
  readToken = ''; tradeQuery = ''; brtiQuery = '';
  $('workspace').hidden = true; $('gate').hidden = false;
  $('trades-results').hidden = true; $('brti-results').hidden = true;
  message('connect-message', '已断开连接。');
});

for (const tab of ['trades', 'brti']) {
  $(`${tab}-tab`).addEventListener('click', () => {
    for (const name of ['trades', 'brti']) {
      const active = name === tab;
      $(`${name}-view`).hidden = !active;
      $(`${name}-tab`).classList.toggle('active', active);
      $(`${name}-tab`).setAttribute('aria-selected', String(active));
    }
  });
}

$('trade-day').addEventListener('change', loadTickers);
$('load-trades').addEventListener('click', loadTrades);
$('load-brti').addEventListener('click', loadBrti);
$('download-trades').addEventListener('click', () => download(tradeQuery, `${$('trade-ticker').value}.parquet`, 'download-trades', 'trades-message'));
$('download-brti').addEventListener('click', () => download(brtiQuery, 'brti-history.parquet', 'download-brti', 'brti-message'));
$('trade-day').value = etDate(new Date());
$('brti-end').value = localInput(new Date());
$('brti-start').value = localInput(new Date(Date.now() - 3600000));
