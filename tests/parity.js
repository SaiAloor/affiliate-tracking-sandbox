// Runs the browser engine on the sample month and the troubleshooter test messages,
// and prints the results as JSON for tests/test_parity.py to compare with Python.
const fs = require("fs");
const path = require("path");
const S = require("../docs/engine.js");
const root = path.join(__dirname, "..");
const j = p => JSON.parse(fs.readFileSync(path.join(root, p), "utf8"));
S.init(j("sandbox/contract.json"), j("sandbox/playbook.json"), j("sandbox/rules.json"));
const dir = process.argv[2] || path.join(root, "data");
const csv = n => S.parseCSV(fs.readFileSync(path.join(dir, n), "utf8"));
const findings = S.check(csv("store_orders.csv"), csv("platform_conversions.csv"), csv("platform_api_errors.csv"));
const cases = [...j("tests/troubleshoot_cases.json"), ...j("tests/troubleshoot_holdout.json")];
console.log(JSON.stringify({
  findings: findings.map(f => [f.order_id, f.issue, f.commission_impact_usd]),
  summary: S.summarize(csv("store_orders.csv"), findings).map(s => [s.issue, s.orders, s.commission_impact_usd]),
  diagnoses: cases.map(c => { const d = S.diagnose(c.text); return d.length ? d[0].issue : null; }),
}));
