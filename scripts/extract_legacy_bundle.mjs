import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync("index.html", "utf8");
const script = source.match(/<script>([\s\S]*)<\/script>/)[1];
const dataSource = script.slice(script.indexOf("const circled="), script.indexOf("const warmups="));
const context = {};
vm.createContext(context);
vm.runInContext(`${dataSource}; this.mock = mock;`, context);

const levels = [
  3, 7, 12, 4, 8,
  5, 9, 13, 6, 10,
  14, 11, 15, 16, 17,
  18, 19, 20, 21, 22,
  23, 24, 25, 26, 27
];

const partial = (index) => {
  if (index === 23) {
    return [
      { points: 1, tokens: ["AO=CO", "CO=AO"] },
      { points: 1, tokens: ["BO=DO", "DO=BO"] },
      { points: 1, tokens: ["맞꼭지", "AOB=COD", "COD=AOB"] },
      { points: 1, tokens: ["SAS"] }
    ];
  }
  if (index === 24) {
    return [
      { points: 2, tokens: ["P=(3,4)", "(3,4)", "P=3,4"] },
      { points: 1, tokens: ["a=-2", "기울기=-2"] },
      { points: 1, tokens: ["b=10", "a+b=8"] }
    ];
  }
  return [];
};

const problems = context.mock.map((q, i) => {
  const [grade, unitName] = q.unit.split(" ", 2);
  const semester = grade.replace("중", "");
  const answerSpec = q.type === "choice"
    ? { correct_index: q.answer }
    : { accepted: q.accept, partial: partial(i), rubric: q.rubric || "" };
  return {
    external_key: `math70-v2-${String(i + 1).padStart(3, "0")}`,
    title: `${i + 1}. ${q.unit}`,
    body_html: `<p>${q.stem}</p>`,
    diagram_svg: q.diagram || "",
    grade: grade.slice(0, 2),
    semester,
    unit: unitName || q.unit,
    tags: [grade, unitName || q.unit, i < 18 ? "기본 72점" : "확장"],
    answer_type: q.type || "choice",
    choices: q.options || [],
    answer_spec: answerSpec,
    explanation_html: `<ol>${q.steps.map((s) => `<li>${s}</li>`).join("")}</ol><p><strong>정답</strong>: ${q.answerText}</p><p><strong>오답 기준</strong>: ${q.trap}</p><p><strong>재시험</strong>: ${q.retry}</p>${q.rubric ? `<p><strong>채점 루브릭</strong>: ${q.rubric}</p>` : ""}`,
    level: levels[i],
    base_xp: 20 + levels[i] * 4,
    state: "published"
  };
});

const bundle = {
  schema_version: 1,
  source: "legacy index.html CBT v2",
  notes: "solved.ac mechanics were inspiration only. This bundle uses original content, original tier badge geometry, and does not copy solved.ac image files, logo, CSS, or branding.",
  problems,
  exams: [
    {
      slug: "math70-v2",
      title: "중등 수학 70점 돌파 모의고사 v2",
      time_limit_seconds: 7200,
      state: "published",
      items: problems.map((p, i) => ({
        sequence: i + 1,
        problem_external_key: p.external_key,
        points: 4
      }))
    }
  ]
};

fs.mkdirSync("content/bundles", { recursive: true });
fs.writeFileSync("content/bundles/math70-v2.json", `${JSON.stringify(bundle, null, 2)}\n`);
