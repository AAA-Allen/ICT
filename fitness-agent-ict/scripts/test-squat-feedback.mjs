import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "../fitness-agent-web/node_modules/typescript/lib/typescript.js";
const file = new URL(
  "../fitness-agent-web/src/services/squatFeedback.ts",
  import.meta.url,
);
const js = ts.transpileModule(fs.readFileSync(file, "utf8"), {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const { buildSquatFeedback } = await import(
  "data:text/javascript;base64," + Buffer.from(js).toString("base64")
);
const task = (observations, metrics = {}) => ({
  algorithm: {
    guidance: { observations },
    results: { biomechanics: { metrics } },
  },
});
const event = { start_sec: 1.2, end_sec: 2.3 };
const candidate = { type: "knee_inward", status: "candidate", events: [event] };
assert.match(buildSquatFeedback(task([candidate]))[0].reason, /1.20–2.30/);
assert.match(
  buildSquatFeedback(task([candidate]))[0].advice,
  /不必拼命向外顶膝/,
);
assert.equal(
  buildSquatFeedback(
    task([
      { ...candidate, status: "unknown", reason_code: "insufficient_evidence" },
    ]),
  )[0].id,
  "capture",
);
assert.equal(
  buildSquatFeedback(
    task([{ ...candidate, status: "unknown", reason_code: "no_candidate" }]),
  )[0].id,
  "no-candidate",
);
assert.equal(
  buildSquatFeedback(task([{ ...candidate, events: [] }]))[0].id,
  "capture",
);
assert.equal(
  buildSquatFeedback(
    task([
      { type: "heel_raise", status: "review_required", review_events: [event] },
    ]),
  )[0].id,
  "heel_raise",
);
for (const type of ["plie", "hold"])
  assert.equal(
    buildSquatFeedback(task([{ ...candidate, type }]))[0].label,
    "动作特征",
  );
assert.ok(
  !buildSquatFeedback(
    task([candidate], {
      bilateral_knee_difference_deg: { status: "estimated", median: 20 },
    }),
  )[0].reason.includes("20.0°"),
);
assert.equal(
  buildSquatFeedback(task([{ ...candidate, type: "new_type" }]))[0].id,
  "capture",
);
assert.equal(
  buildSquatFeedback(
    task([{ ...candidate, events: [{ start_sec: NaN, end_sec: 2 }] }]),
  )[0].id,
  "capture",
);
assert.equal(buildSquatFeedback(task([]))[0].id, "capture");
console.log("Squat feedback: 12 checks passed");

const legacyMotion = task([{type:'knee_inward',status:'unknown'}],{left_knee_flexion_deg:{status:'estimated',p95:82,coverage:.9}});
assert.ok(buildSquatFeedback(legacyMotion).some(c=>c.id==='motion-depth'));
assert.ok(!buildSquatFeedback(legacyMotion).some(c=>c.id==='capture'));
assert.equal(buildSquatFeedback(task([],{left_knee_flexion_deg:{status:'estimated',p95:80,coverage:.2}}))[0].id,'capture');
console.log('3 additional dual-path presentation checks passed');
