// Shared by the single-address page (/) and the batch page (/batch).

export const STATUS = {
  verified:       { t: '已核实',     tone: 'ok',   brief: '门牌和街道都有记录；输入了单元号时，单元也有记录。' },
  unit_not_found: { t: '单元未找到', tone: 'warn', brief: '楼栋存在，这个单元号没有记录（很多数据源不录单元）。',
                    d: '楼栋存在，但这个单元号没有记录。很多数据源只录到楼栋，不一定代表单元不存在。' },
  street_only:    { t: '门牌不存在', tone: 'warn', brief: '附近有这条街，但没有这个门牌号。',
                    d: '附近有这条街，但没有这个门牌号。' },
  not_found:      { t: '未找到',     tone: 'bad',  brief: '该地区有数据覆盖却没有匹配，较可能是错误或虚构的地址。',
                    d: '这个地区有数据覆盖，却没有匹配的地址，较大概率是错误或虚构的地址。' },
  not_covered:    { t: '无法判断',   tone: 'none', brief: 'NAD 和 Overture 在该地区都没有数据。',
                    d: 'NAD 和 Overture 在这个地区都没有数据，无法判断地址是否存在。' },
  geocode_failed: { t: '无法定位',   tone: 'none', brief: 'Census 找不到这个地址；带 ZIP 时可以按 ZIP 范围重试。',
                    d: 'Census 地理编码找不到这个地址，没有位置就没法在 NAD 里查找。' },
  invalid_input:  { t: '无法解析',   tone: 'none', brief: '没有识别出门牌号。',
                    d: '没有识别出门牌号，请输入完整的街道地址，例如 1700 W Washington St, Phoenix, AZ。' },
  upstream_error: { t: '服务异常',   tone: 'bad',  brief: '免费的上游服务超时或出错。',
                    d: '免费的上游服务超时或出错，请稍后再试。' },
};
export const SOURCE = { nad: 'NAD', overture: 'Overture' };

// The record's ZIP when it differs from the ZIP the user typed (a wrong ZIP misroutes a parcel even though the
// address exists); null otherwise, including when no ZIP was given.
export function zipMismatch(r) {
  const bm = r && r.best_match, typed = r && r.parsed && r.parsed.zip;
  return bm && typed && bm.match && bm.match.zip_match === false && bm.zip ? bm.zip : null;
}

export const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode etc. */ } },
};

export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
export const tone = (s) => (STATUS[s] || {}).tone || 'none';
export const label = (s) => (STATUS[s] || {}).t || s || '—';
export const pct = (v) => `${Math.round(v * 1000) / 10}%`;
export const fmtMs = (v) => (v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`);

// API key row: hidden until the server asks for a key (401) or one was saved earlier in this browser.
export function setupKey(row, input) {
  input.value = store.get('nad.apiKey', '') || '';
  if (input.value) row.hidden = false;
  input.addEventListener('change', () => store.set('nad.apiKey', input.value.trim()));
  return {
    headers: () => (input.value.trim() ? { 'X-API-Key': input.value.trim() } : {}),
    has: () => Boolean(input.value.trim()),
    reveal() { row.hidden = false; input.focus(); },
  };
}

// Non-2xx from /v1/* -> what to tell the user.
export function describeHttpError(status, body, hadKey) {
  const d = body && body.detail;
  if (status === 401) {
    return { needKey: true, title: hadKey ? 'API Key 不正确' : '需要 API Key', msg: '这个服务开启了鉴权。在上方填写 API Key 后重试。' };
  }
  if (status === 429) return { title: '请求太频繁', msg: '超过了每分钟的请求上限，等一分钟再试。' };
  return { title: `请求失败（HTTP ${status}）`, msg: (Array.isArray(d) ? d.map((x) => x.msg).join('；') : d) || '服务返回了错误。' };
}

const UPSTREAM = {
  census_geocoder: 'Census 地理编码', nad: 'NAD 地址库', nad_coverage: 'NAD 覆盖范围', tigerweb: 'Census ZIP 范围', overture: 'Overture',
};
const ago = (s) => (s < 60 ? '刚刚' : s < 3600 ? `${Math.round(s / 60)} 分钟前` : `${Math.round(s / 3600)} 小时前`);

// Header indicator. /health reports how each upstream's latest call went, so a blocked Census shows up here
// instead of as a string of unexplained failures. Called again after queries to stay current.
export function showHealth(el) {
  fetch('health').then((r) => r.json()).then((h) => {
    const o = h.overture || {};
    const bad = Object.entries(h.upstreams || {}).filter(([, v]) => !v.ok);
    const name = (k) => UPSTREAM[k] || k;
    const state = !bad.length ? (h.status === 'ok' ? '服务正常' : '服务降级')
      : bad.length === 1 ? `${name(bad[0][0])}不可用` : `${bad.length} 个上游服务不可用`;
    const ov = o.enabled ? `Overture ${o.release || '已启用'}` : 'Overture 未启用';
    el.className = `health ${h.status === 'ok' ? 'ok' : 'bad'}`;
    el.title = bad.length
      ? bad.map(([k, v]) => `${name(k)}（${ago(v.ago_s)}）：${v.error}`).join('\n')
      : `${state} · ${ov}`;
    el.innerHTML = `<i></i><span>${esc(state)}</span>${bad.length ? '' : `<span class="rel">· ${esc(ov)}</span>`}`;
  }).catch(() => {
    el.className = 'health bad';
    el.title = '服务不可达';
    el.innerHTML = '<i></i><span>服务不可达</span>';
  });
}

// navigator.clipboard only exists on https:// and localhost; a server opened by IP over http needs the old way.
export function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(() => true, () => false);
  const ta = Object.assign(document.createElement('textarea'), { value: text, readOnly: true });
  ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch { /* not supported */ }
  ta.remove();
  return Promise.resolve(ok);
}
