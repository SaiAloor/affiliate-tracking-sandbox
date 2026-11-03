/*
 * Affiliate Tracking Sandbox: the browser version of the platform, health check
 * and troubleshooter. It reads the same contract.json, playbook.json and rules.json
 * as the Python code, and tests/test_parity.py checks that both give the same answers.
 *
 * No dependencies. Works in the browser (window.Sandbox) and in Node (module.exports).
 */
(function (root) {
  "use strict";

  let CONTRACT, PLAYBOOK, RULES, CLICK_RE;
  function init(contract, playbook, rules) {
    CONTRACT = contract; PLAYBOOK = playbook.issues; RULES = rules;
    CLICK_RE = new RegExp(CONTRACT.click_id_pattern);
  }

  const COMMISSION_RATE = 0.08;
  const TO_USD = { USD: 1.0, CAD: 0.73, EUR: 1.08, GBP: 1.27, AUD: 0.66 };
  const AUTH_CODES = new Set(["missing_api_key", "invalid_api_key"]);
  const INFORMATIONAL = {
    duplicate_fire: "Already handled: the platform dropped the extra copies. Optional cleanup: fire the tag once per order.",
    late_conversion: "Working as designed: these sales fell outside the 30-day lookback window.",
  };
  const LIVE_KEY = "live_demo_9f2c41", TEST_KEY = "test_demo_51ab07";
  const DAY = 86400000;

  // ---------- CSV ----------
  function parseCSV(text) {
    const rows = []; let row = [], field = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) { if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; } else field += c; }
      else if (c === '"') q = true;
      else if (c === ",") { row.push(field); field = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(field); field = ""; if (row.length > 1 || row[0] !== "") rows.push(row); row = [];
      } else field += c;
    }
    if (field !== "" || row.length) { row.push(field); rows.push(row); }
    const head = rows.shift() || [];
    return rows.map(r => Object.fromEntries(head.map((h, j) => [h, r[j] === undefined ? "" : r[j]])));
  }

  // ---------- platform ----------
  function error(code, field) {
    const spec = CONTRACT.errors[code];
    const body = { error: { code, message: spec.message.replace("{field}", field || ""), docs: "guide.html" + spec.docs } };
    if (field) body.error.field = field;
    return [spec.status, body];
  }

  function validate(p) {
    for (const f of CONTRACT.required) if (p[f] === undefined || p[f] === null || p[f] === "") return ["missing_field", f];
    if (!CLICK_RE.test(String(p.click_id))) return ["invalid_click_id", "click_id"];
    const s = String(p.amount).trim();
    const amount = s === "" ? NaN : Number(s);
    if (!Number.isFinite(amount) || amount <= 0) return ["invalid_amount", "amount"];
    if (amount > CONTRACT.max_amount) return ["amount_too_large", "amount"];
    if (!CONTRACT.currencies.includes(String(p.currency).toUpperCase())) return ["invalid_currency", "currency"];
    return null;
  }

  class Platform {
    constructor() { this.clicks = new Map(); this.conv = new Map(); this.errors = []; this.n = 0; }
    createClick(partner, at = Date.now()) {
      const id = "clk_" + Array.from({ length: 10 }, () => "abcdefghijklmnopqrstuvwxyz0123456789"[Math.floor(Math.random() * 36)]).join("");
      this.clicks.set(id, { partner, at });
      return id;
    }
    _err(at, method, p, code, field) {
      this.errors.push({ received_at: new Date(at).toISOString(), method, order_id: String(p.order_id || ""), click_id: String(p.click_id || ""), code, field: field || "" });
      return error(code, field);
    }
    record(p, method = "s2s", key = null, at = Date.now()) {
      if (method === "s2s") {
        if (!key) return this._err(at, method, p, "missing_api_key");
        if (key !== LIVE_KEY && key !== TEST_KEY) return this._err(at, method, p, "invalid_api_key");
      }
      const env = key === TEST_KEY ? "test" : "production";
      const bad = validate(p);
      if (bad) return this._err(at, method, p, ...bad);
      const click = this.clicks.get(p.click_id);
      if (!click) return this._err(at, method, p, "unknown_click", "click_id");
      if (at - click.at > CONTRACT.lookback_days * DAY) return this._err(at, method, p, "click_expired", "click_id");
      const oid = String(p.order_id);
      const ex = this.conv.get(oid);
      if (ex) { ex.duplicates++; return [200, { status: "duplicate", order_id: oid, message: "This order_id was already recorded, so it was not counted again." }]; }
      this.conv.set(oid, { order_id: oid, click_id: p.click_id, partner_id: click.partner, amount: Math.round(Number(p.amount) * 100) / 100,
        currency: String(p.currency).toUpperCase(), method, environment: env, status: "approved", refunded: 0, received_at: new Date(at).toISOString(), duplicates: 0 });
      return [201, { status: "approved", order_id: oid, partner_id: click.partner, environment: env }];
    }
    reverse(oid, amount) {
      const c = this.conv.get(oid);
      if (!c) return error("order_not_found", "order_id");
      const a = amount === undefined ? c.amount - c.refunded : Number(amount);
      if (c.refunded + a > c.amount + 0.005) return error("reversal_too_large", "amount");
      c.refunded = Math.round((c.refunded + a) * 100) / 100;
      c.status = c.refunded >= c.amount - 0.005 ? "reversed" : "partly_reversed";
      return [200, { status: c.status, order_id: oid, refunded: c.refunded }];
    }
    conversions() { return [...this.conv.values()]; }
  }

  // ---------- health check ----------
  const num = v => { const x = Number(v); return Number.isFinite(x) ? x : 0; };
  const r2 = v => Math.round(v * 100) / 100;

  function check(orders, conversions, apiErrors) {
    const conv = new Map(conversions.map(c => [c.order_id, c]));
    const errs = new Map();
    for (const e of apiErrors) { if (!errs.has(e.order_id)) errs.set(e.order_id, []); errs.get(e.order_id).push(e.code); }
    const out = [];
    const add = (o, issue, impact, detail) => out.push({ order_id: o.order_id, issue, title: PLAYBOOK[issue].title, commission_impact_usd: r2(impact), detail });
    for (const o of orders) {
      const amount = num(o.amount), fx = TO_USD[o.currency] ?? 1, fair = amount * fx * COMMISSION_RATE;
      const c = conv.get(o.order_id);
      if (o.partner_traffic === "yes" && !o.click_id) { add(o, "click_id_lost", -fair, "partner traffic, but the order has no click ID"); continue; }
      if (!o.click_id) continue;
      if (!c) {
        const codes = errs.get(o.order_id) || [];
        if (!codes.length) add(o, "conversion_missing", -fair, "order has a click ID but never reached the platform");
        else if (codes.includes("click_expired")) add(o, "late_conversion", 0, "rejected: click older than the lookback window");
        else if (codes.some(x => AUTH_CODES.has(x))) add(o, "auth_error", -fair, "rejected: " + codes[0]);
        else add(o, "validation_error", -fair, "rejected: " + codes[0]);
        continue;
      }
      const sent = num(c.amount);
      if (amount > 0 && sent / amount >= 95 && sent / amount <= 105) add(o, "amount_in_cents", (sent - amount) * fx * COMMISSION_RATE, `store ${amount.toFixed(2)}, platform ${sent.toFixed(2)}`);
      if (c.currency !== o.currency) add(o, "currency_mismatch", amount * ((TO_USD[c.currency] ?? 1) - fx) * COMMISSION_RATE, `store ${o.currency}, platform ${c.currency}`);
      const refunded = num(o.refunded_amount);
      if (refunded > 0 && num(c.refunded) < refunded - 0.01) add(o, "refund_not_reversed", (refunded - num(c.refunded)) * fx * COMMISSION_RATE, `refunded ${refunded.toFixed(2)} in the store, ${num(c.refunded).toFixed(2)} reported`);
      if (Math.trunc(num(c.duplicates)) > 0) add(o, "duplicate_fire", 0, `reported ${Math.trunc(num(c.duplicates)) + 1} times; extra copies were dropped`);
      if (o.is_test === "yes" && c.environment === "production") add(o, "test_order", sent * fx * COMMISSION_RATE, "store marks this as a test order");
    }
    return out;
  }

  function summarize(orders, findings) {
    const amt = new Map(orders.map(o => [o.order_id, num(o.amount) * (TO_USD[o.currency] ?? 1)]));
    const g = new Map();
    for (const f of findings) {
      if (!g.has(f.issue)) g.set(f.issue, { orders: 0, sales: 0, impact: 0 });
      const s = g.get(f.issue); s.orders++; s.sales += amt.get(f.order_id) || 0; s.impact += f.commission_impact_usd;
    }
    return [...g].map(([k, v]) => ({ issue: k, title: PLAYBOOK[k].title, orders: v.orders, sales_usd: r2(v.sales), commission_impact_usd: r2(v.impact),
      needs_fix: !(k in INFORMATIONAL), fix: k in INFORMATIONAL ? INFORMATIONAL[k] : PLAYBOOK[k].fix }))
      .sort((a, b) => (a.needs_fix === b.needs_fix ? 0 : a.needs_fix ? -1 : 1) || Math.abs(b.commission_impact_usd) - Math.abs(a.commission_impact_usd) || b.orders - a.orders);
  }

  // ---------- troubleshooter ----------
  function parseJSON(text) {
    try { return JSON.parse(text); } catch (e) {
      const m = text.match(/\{[\s\S]*\}/);
      if (m) { try { return JSON.parse(m[0]); } catch (e2) { return null; } }
      return null;
    }
  }
  const isObj = d => d && typeof d === "object" && !Array.isArray(d);

  function diagnose(text) {
    const scores = new Map(), reasons = new Map();
    const add = (issue, w, why) => { scores.set(issue, (scores.get(issue) || 0) + w); if (!reasons.has(issue)) reasons.set(issue, why); };
    const data = parseJSON(text);
    if (isObj(data) && isObj(data.error)) {
      const code = data.error.code || "";
      if (code in RULES.error_to_issue) add(RULES.error_to_issue[code], 10, `the API returned error code ${code}`);
    } else if (isObj(data) && ("amount" in data || "order_id" in data)) {
      const bad = validate(data);
      if (bad) {
        const [code, field] = bad;
        add(field === "click_id" ? "click_id_lost" : (RULES.error_to_issue[code] || "validation_error"), 10, `this request would be rejected: ${code} on ${field}`);
      } else {
        const amt = String(data.amount ?? "");
        if (/^\d{4,}$/.test(amt)) add("amount_in_cents", 6, `amount ${amt} has no decimal point and looks like cents`);
        if (!data.click_id) add("click_id_lost", 6, "no click_id in the request");
      }
    }
    const lower = scores.size ? "" : text.toLowerCase();
    if (lower) for (const c of RULES.clues) {
      const m = new RegExp(c.pattern).exec(lower);
      if (m) add(c.issue, c.weight, `the message mentions "${m[0]}"`);
    }
    return [...scores.keys()].sort((a, b) => scores.get(b) - scores.get(a))
      .map(k => ({ issue: k, score: scores.get(k), reason: reasons.get(k), ...PLAYBOOK[k] }));
  }

  const api = { init, parseCSV, validate, Platform, check, summarize, diagnose, LIVE_KEY, TEST_KEY, COMMISSION_RATE, TO_USD };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Sandbox = api;
})(typeof window !== "undefined" ? window : globalThis);
