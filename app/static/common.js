// Shared by the single-address page (/) and the batch page (/batch).

export const STATUS = {
  verified:       { t: '已核实',     tone: 'ok',   brief: '门牌和街道都有记录；输入了单元号时，单元也有记录。' },
  unit_not_found: { t: '单元未找到', tone: 'warn', brief: '楼栋存在，这个单元号没有记录（很多数据源不录单元）。',
                    d: '楼栋存在，但这个单元号没有记录。很多数据源只录到楼栋，不一定代表单元不存在。' },
  street_only:    { t: '门牌不存在', tone: 'warn', brief: '附近有这条街，但没有这个门牌号。',
                    d: '附近有这条街，但没有这个门牌号。' },
  not_found:      { t: '未找到',     tone: 'bad',  brief: '该地区有数据覆盖却没有匹配，较可能是错误或虚构的地址。',
                    d: '这个地区有数据覆盖，却没有匹配的地址，较大概率是错误或虚构的地址。' },
  not_covered:    { t: '无法判断',   tone: 'none', brief: '数据源在该地区没有数据，或者只有 OpenStreetMap 而它没收录这个门牌。',
                    d: 'NAD 和 Overture 在这个地区都没有数据，无法判断地址是否存在。' },
  geocode_failed: { t: '无法定位',   tone: 'none', brief: '定位服务找不到这条街或这个地名；美国地址带 ZIP 时可以按 ZIP 范围重试。',
                    d: 'Census 地理编码找不到这个地址，没有位置就没法在 NAD 里查找。' },
  invalid_input:  { t: '无法解析',   tone: 'none', brief: '没有识别出门牌号。',
                    d: '没有识别出门牌号，请输入完整的街道地址，例如 1700 W Washington St, Phoenix, AZ。' },
  upstream_error: { t: '服务异常',   tone: 'bad',  brief: '免费的上游服务超时或出错。',
                    d: '免费的上游服务超时或出错，请稍后再试。' },
};
// Short names for the result tables; the full provider name comes with each result (r.provider.name).
export const SOURCE = { nad: 'NAD', overture: 'Overture', fr_ban: 'BAN', nl_pdok: 'BAG', jp_gsi: '国土地理院', osm: 'OSM' };

// The record's ZIP / postcode when it differs from the one the user typed (a wrong ZIP misroutes a parcel even
// though the address exists); null otherwise, including when none was given.
export function zipMismatch(r) {
  const bm = r && r.best_match, typed = r && r.parsed && r.parsed.zip;
  return bm && typed && bm.match && bm.match.zip_match === false && bm.zip ? bm.zip : null;
}

const intl = (r) => Boolean(r && r.country && r.country !== 'US');
export const zipWord = (r) => (intl(r) ? '邮编' : 'ZIP');

// Status word for one result: Japanese addresses can be verified down to the block (番) only.
export function verdictLabel(r) {
  if (r && r.status === 'verified' && r.match_level === 'block') return '已核实到番';
  return label(r && r.status);
}

// One-sentence explanation of a result. Outside the US the wording depends on where the answer came from: an
// official register, Overture (official data republished), OpenStreetMap (incomplete) or Japan's 国土地理院.
export function verdictText(r) {
  const st = STATUS[r.status] || {};
  const zipFix = zipMismatch(r);
  const zipNote = zipFix ? `但${zipWord(r)}不对：输入的是 ${r.parsed.zip}，记录里是 ${zipFix}。` : '';
  if (!intl(r)) {
    if (r.status === 'verified') return (r.match_level === 'unit' ? '门牌、街道和单元号都有记录。' : '门牌和街道都有记录。') + zipNote;
    return st.d || '';
  }
  const jp = r.country === 'JP', src = (r.provider && r.provider.name) || SOURCE[r.data_source] || '数据源';
  const osm = r.provider && r.provider.authority === 'osm';
  switch (r.status) {
    case 'verified':
      if (r.match_level === 'block') return '「番」（街区）存在，但国土地理院在这里只记录到番，「号」无法核实。' + zipNote;
      return (r.match_level === 'unit' ? '门牌、街道和单元都有记录。' : jp ? '丁目、番、号都有记录。' : '门牌和街道都有记录。')
        + (osm ? '依据是 OpenStreetMap 的社区数据，不是官方登记。' : '') + zipNote;
    case 'unit_not_found': return '楼栋存在，但这个单元（附加号）在官方登记里没有记录。';
    case 'street_only': return jp ? '丁目存在，但没有这个番或号。' : '这条街存在，但官方登记里没有这个门牌号。';
    case 'not_found': return jp ? '这个町没有这个丁目，地址大概率写错了。' : '登记里没有这条街和门牌，较大概率是错误或虚构的地址。';
    case 'not_covered':
      if (jp) return '国土地理院在这一带没有门牌（地番）数据，无法判断。';
      if (osm) return 'OpenStreetMap 有这条街，但没收录这个门牌号。OSM 的门牌数据不全，无法据此判断地址真假。';
      return `${src}在这一带没有数据，无法判断。`;
    case 'geocode_failed':
      return jp ? '国土地理院找不到这个地名。罗马字地址需要带邮编，或写出町名和区市名。' : '找不到这条街，请检查街道名和城市的拼写。';
    case 'invalid_input':
      return jp ? '没有识别出丁目、番、号或番地，请输入完整地址，例如 東京都世田谷区世田谷4-21-27。'
        : '没有识别出门牌号，请输入完整地址，例如 Unter den Linden 77, 10117 Berlin。';
    case 'upstream_error': return `${src}暂时连不上或出错，请稍后再试。`;
    default: return st.d || '';
  }
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

// Country picker options from /countries ("自动识别" first). Calls back with [] when the list can't be loaded.
export function loadCountries(select, selected) {
  return fetch('countries').then((r) => r.json()).then((list) => {
    const opt = (c) => `<option value="${esc(c.code)}">${esc(c.name)}</option>`;
    const us = list.filter((c) => c.code === 'US'), jp = list.filter((c) => c.code === 'JP');
    const eu = list.filter((c) => c.code !== 'US' && c.code !== 'JP').sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
    select.innerHTML = '<option value="">自动识别</option>' + us.map(opt).join('') + jp.map(opt).join('')
      + `<optgroup label="欧洲">${eu.map(opt).join('')}</optgroup>`;
    select.value = list.some((c) => c.code === selected) ? selected : '';
    return list;
  }).catch(() => []);
}
