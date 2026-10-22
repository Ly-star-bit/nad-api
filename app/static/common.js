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
  geocode_failed: { t: '无法定位',   tone: 'none', brief: '定位服务找不到这条街或这个地名；美国地址按 ZIP / 城市范围查 NAD 也没有这条街（可能是新街道或街名写错）。',
                    d: 'Census 地理编码找不到这个地址，没有位置就没法在 NAD 里查找。' },
  invalid_input:  { t: '无法解析',   tone: 'none', brief: '没有识别出门牌号。',
                    d: '没有识别出门牌号，请输入完整的街道地址，例如 1700 W Washington St, Phoenix, AZ。' },
  upstream_error: { t: '服务异常',   tone: 'bad',  brief: '免费的上游服务超时或出错。',
                    d: '免费的上游服务超时或出错，请稍后再试。' },
};
// Short names for the result tables; the full provider name comes with each result (r.provider.name).
export const SOURCE = {
  nad: 'NAD', overture: 'Overture', overture_places: 'Overture 商户', osm: 'OSM', jp_gsi: '国土地理院', fr_ban: 'BAN', nl_pdok: 'BAG', be_register: 'CRAB/ICAR',
  lu_geoportail: 'BD-Adresses', ch_geoadmin: 'GWR', at_bev: 'BEV', es_cartociudad: 'CartoCiudad', no_kartverket: 'Kartverket',
  fi_ryhti: 'Ryhti', is_hms: 'HMS', ee_inads: 'In-ADS', lv_vzd: 'VZD', pl_gugik: 'PRG', cz_ruian: 'RÚIAN', si_gurs: 'GURS',
};
export const sourceName = (r) => SOURCE[r.data_source] || (r.provider && r.provider.name) || r.data_source || '';

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
    // a business's listed address (Overture places), not an address point an addressing authority recorded
    const listing = r.data_source === 'overture_places';
    if (r.status === 'verified') {
      if (listing) return '地址库里没有这个地址，但有商户登记在这个门牌（多见于新建仓库、工业园）：依据是商户数据，不是地址点。' + zipNote;
      return (r.match_level === 'unit' ? '门牌、街道和单元号都有记录。' : '门牌和街道都有记录。') + zipNote;
    }
    // Census could not place it, so NAD was searched by ZIP / city area (r.geocode.source 'zcta' / 'city')
    const area = r.geocode && (r.geocode.source === 'zcta' || r.geocode.source === 'city');
    const said = (s) => (r.notes || []).some((n) => n.includes(s));
    if (r.status === 'geocode_failed' && said('newer than the data')) {
      return 'Census 和 NAD 里都没有这条街，无法判断地址是否存在：可能是比数据更新的街道（新建小区、新工业园），也可能是街道名写错了。';
    }
    if (r.status === 'street_only' && area) return 'Census 定位不到这个地址，但 NAD 在这一带有这条街，只是没有这个门牌号。';
    if (r.status === 'not_found' && area) return 'Census 定位不到这个地址，NAD 在这片范围里也没有这条街上的这个门牌。';
    return st.d || '';
  }
  const jp = r.country === 'JP', src = (r.provider && r.provider.name) || SOURCE[r.data_source] || '数据源';
  const osm = r.provider && r.provider.authority === 'osm';
  const parcel = Boolean(r.jp && r.jp.parcel);  // a 番地 (parcel-number) address, not 丁目-番-号
  const said = (s) => (r.notes || []).some((n) => n.includes(s));  // what the server found, in its notes
  const elsewhere = said('exists only in other places') || said('only in other places');
  switch (r.status) {
    case 'verified': {
      if (r.match_level === 'block') {
        if (parcel) return '番地（地番）存在；后面的枝番国土地理院没有记录，无法核实。' + zipNote;
        if (r.jp && !r.jp.go_given) return '「番」（街区）存在；地址里没写「号」，所以只核实到番。' + zipNote;
        return '「番」（街区）存在，但国土地理院在这里只记录到番，「号」无法核实。' + zipNote;
      }
      const what = r.match_level === 'unit' ? '门牌、街道和单元都有记录。' : jp ? (parcel ? '番地（地番）有记录。' : '丁目、番、号都有记录。')
        : '门牌和街道都有记录。';
      const extra = [said('no town or postcode in the address') ? '地址没写城市或邮编，全国只找到这一处。' : '',
        said('town differs') ? '城市名和登记写法不同（邮编一致）。' : '',
        said('street name differs') ? '街名和登记写法略有不同，请核对。' : ''].join('');
      return what + (osm ? '依据是 OpenStreetMap 的社区数据，不是官方登记。' : '') + extra + zipNote;
    }
    case 'unit_not_found': return '楼栋存在，但这个单元（附加号）在官方登记里没有记录。';
    case 'street_only':
      if (jp) return parcel ? '番地存在，但没有这个枝番。' : '丁目或番存在，但没有这个番或号。';
      return '这条街存在，但官方登记里没有这个门牌号。' + (elsewhere ? '同名街道的这个门牌在别的城市有，城市或邮编可能写错了。' : '');
    case 'not_found':
      if (jp) return '这个町没有这个丁目，地址大概率写错了。';
      if (elsewhere) return '这条街和门牌只在别的城市或邮编出现，地址里的城市或邮编很可能写错了。';
      if (osm) return 'OpenStreetMap 里找不到这个地址。OSM 数据不全，街名的写法或文字也可能不同，不能据此判断地址是假的。';
      return '登记里没有这条街和门牌，较大概率是错误或虚构的地址。';
    case 'not_covered':
      if (said('without a house number')) return '地址没有门牌号（房名、s/n 或乡村地址），登记按门牌查找，无法核实。';
      if (said('add the town or postcode')) return '地址没写城市或邮编，这条街和门牌在多个地方都有，无法判断是哪一个。';
      if (jp) return '国土地理院在这一带没有门牌（地番）数据，无法判断。';
      if (osm || said('so this does not mean the address is wrong')) {
        return `${osm ? 'OpenStreetMap' : src} 有这条街，但没收录这个门牌号。这个数据源不全，无法据此判断地址真假。`;
      }
      return `${src}在这一带没有数据，无法判断。`;
    case 'geocode_failed':
      if (jp && said('add the prefecture')) return '这个地名在日本有多处同名，请写上都道府县和市区町村。';
      if (jp) return '国土地理院找不到这个地名。罗马字地址需要带邮编，或写出町名和区市名。';
      if (said('only in other places')) return '这条街只在别的城市找到，地址里的城市或邮编可能写错了。';
      return '找不到这条街，请检查街道名和城市的拼写。';
    case 'invalid_input':
      if (said('PO box')) return '邮政信箱（PO Box、Postfach、BP）和自提柜不是街道地址，地址登记里没有。';
      return jp ? '没有识别出丁目、番、号或番地，请输入完整地址，例如 東京都世田谷区世田谷4-21-27。'
        : '没有识别出门牌号，请输入完整地址，例如 Unter den Linden 77, 10117 Berlin。';
    case 'upstream_error':
      if (jp && said('Japan Post postcode table is not loaded')) return '日本邮政的邮编表还没加载好，暂时读不了罗马字地址；用日文写的地址现在就能核实。';
      return `${src}暂时连不上或出错，请稍后再试。`;
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

// The health tooltip lists sources by name alone, so the national registers carry their country there
const UPSTREAM = {
  ...SOURCE,
  fr_ban: 'BAN（法国）', nl_pdok: 'BAG（荷兰）', be_register: 'CRAB/ICAR（比利时）', lu_geoportail: 'BD-Adresses（卢森堡）',
  ch_geoadmin: 'GWR（瑞士）', at_bev: 'BEV（奥地利）', es_cartociudad: 'CartoCiudad（西班牙）', no_kartverket: 'Kartverket（挪威）',
  fi_ryhti: 'Ryhti（芬兰）', is_hms: 'HMS（冰岛）', ee_inads: 'In-ADS（爱沙尼亚）', lv_vzd: 'VZD（拉脱维亚）', pl_gugik: 'PRG（波兰）',
  cz_ruian: 'RÚIAN（捷克）', si_gurs: 'GURS（斯洛文尼亚）',
  census_geocoder: 'Census 地理编码', nad: 'NAD 地址库', nad_coverage: 'NAD 覆盖范围', tigerweb: 'Census ZIP 范围', overture: 'Overture',
  photon: 'OpenStreetMap 搜索（Photon）', postcodes_io: '英国邮编（postcodes.io）', be_vlaanderen: '比利时（弗拉芒）', be_wallonie: '比利时（瓦隆）',
};
const ago = (s) => (s < 60 ? '刚刚' : s < 3600 ? `${Math.round(s / 60)} 分钟前` : `${Math.round(s / 3600)} 小时前`);

// Header indicator. /health reports how each upstream's latest call went, so a blocked Census shows up here
// instead of as a string of unexplained failures. Called again after queries to stay current.
export function showHealth(el) {
  fetch('health').then((r) => r.json()).then((h) => {
    const o = h.overture || {};
    const bad = Object.entries(h.upstreams || {}).filter(([, v]) => !v.ok);
    // other countries' sources don't turn the dot red (one register being down says nothing about the US), but
    // the label and the tooltip name them
    const abroad = Object.entries(h.intl_upstreams || {}).filter(([, v]) => !v.ok);
    const jp = h.japanpost && !h.japanpost.loaded ? [['japanpost', { error: h.japanpost.error || '还没加载', ago_s: 0 }]] : [];
    const name = (k) => (k === 'japanpost' ? '日本邮政邮编表' : UPSTREAM[k] || k);
    const state = bad.length ? (bad.length === 1 ? `${name(bad[0][0])}不可用` : `${bad.length} 个上游服务不可用`)
      : abroad.length + jp.length ? '部分海外数据源不可用' : h.status === 'ok' ? '服务正常' : '服务降级';
    const ov = o.enabled ? `Overture ${o.release || '已启用'}` : 'Overture 未启用';
    el.className = `health ${h.status === 'ok' ? 'ok' : 'bad'}`;
    const issues = [...bad, ...abroad, ...jp];
    el.title = issues.length
      ? issues.map(([k, v]) => `${name(k)}${v.ago_s ? `（${ago(v.ago_s)}）` : ''}：${v.error}`).join('\n')
      : `${state} · ${ov}`;
    el.innerHTML = `<i></i><span>${esc(state)}</span>${issues.length ? '' : `<span class="rel">· ${esc(ov)}</span>`}`;
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
    const prev = select.value;
    const opt = (c) => `<option value="${esc(c.code)}">${esc(c.name)}</option>`;
    const us = list.filter((c) => c.code === 'US'), jp = list.filter((c) => c.code === 'JP');
    const eu = list.filter((c) => c.code !== 'US' && c.code !== 'JP').sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
    select.innerHTML = '<option value="">自动识别</option>' + us.map(opt).join('') + jp.map(opt).join('')
      + `<optgroup label="欧洲">${eu.map(opt).join('')}</optgroup>`;
    // keep what the user picked while the list was loading; a value from the URL is matched case-insensitively,
    // and one the list doesn't have ('Germany', 'UK') stays as it is for the server to read
    const want = String((typeof selected === 'function' ? selected() : selected) || prev || '').trim();
    const hit = list.find((c) => c.code === want.toUpperCase());
    if (want && !hit) select.add(new Option(want, want));
    select.value = hit ? hit.code : want;
    return list;
  }).catch(() => []);
}
