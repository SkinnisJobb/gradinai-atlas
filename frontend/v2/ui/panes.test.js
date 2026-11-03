import { test } from "node:test";
import assert from "node:assert/strict";
import { columns, collapsedKey, isClick, dragTarget, MIN, BAR, PANES, DEFAULT_DASHBOARD } from "./panes.js";

const sum = (c, total) => {
  // the graph gets what its bar and the fixed columns leave
  return total - BAR - (c.template.includes(`${c.board}px`) ? c.board + BAR : 0) - (c.template.includes(`${c.panel}px`) ? c.panel + BAR : 0);
};

test("graph only: its bar and one flexible column", () => {
  assert.equal(columns(1600, { boardOn: false, panelOn: false, board: 500, panel: 480 }).template, `${BAR}px minmax(${MIN.graph}px, 1fr)`);
});

test("an unset board width starts at 36% of the stage", () => {
  const c = columns(3440, { boardOn: true, panelOn: false, board: 0, panel: 480 });
  assert.equal(c.board, Math.round(3440 * 0.36));
});

test("widths that do not fit are clamped so the graph keeps its minimum", () => {
  const c = columns(1400, { boardOn: true, panelOn: true, board: 2000, panel: 900 });
  assert.ok(c.panel >= MIN.panel && c.board >= MIN.board);
  assert.ok(sum(c, 1400) >= MIN.graph, `graph got ${sum(c, 1400)}`);
});

test("no pane goes below its minimum, even dragged to nothing", () => {
  const c = columns(3440, { boardOn: true, panelOn: true, board: -50, panel: 10 });
  assert.equal(c.board, MIN.board);
  assert.equal(c.panel, MIN.panel);
});

test("a hidden pane contributes neither a bar nor a width", () => {
  const c = columns(2000, { boardOn: false, panelOn: true, board: 600, panel: 480 });
  assert.equal(c.template, `${BAR}px minmax(${MIN.graph}px, 1fr) ${BAR}px 480px`);
});

test("nothing collapsed is the same layout as no collapsed state at all", () => {
  const want = { boardOn: true, panelOn: true, board: 600, panel: 480 };
  const none = columns(2400, want);
  const all = columns(2400, { ...want, collapsed: { graph: false, board: false, panel: false } });
  assert.equal(all.template, none.template);
  assert.equal(none.template, `${BAR}px minmax(${MIN.graph}px, 1fr) ${BAR}px 600px ${BAR}px 480px`);
  assert.equal(none.flex, "graph");
});

test("a collapsed pane contributes its bar only, and keeps its width for later", () => {
  const c = columns(2400, { boardOn: true, panelOn: true, board: 600, panel: 480, collapsed: { board: true } });
  assert.equal(c.template, `${BAR}px minmax(${MIN.graph}px, 1fr) ${BAR}px ${BAR}px 480px`);
  assert.equal(c.board, 600);
});

test("with the graph collapsed, the next expanded pane takes the rest", () => {
  const c = columns(2400, { boardOn: true, panelOn: true, board: 600, panel: 480, collapsed: { graph: true } });
  assert.equal(c.flex, "board");
  assert.equal(c.template, `${BAR}px ${BAR}px minmax(${MIN.board}px, 1fr) ${BAR}px 480px`);
  const p = columns(2400, { boardOn: false, panelOn: true, board: 600, panel: 480, collapsed: { graph: true } });
  assert.equal(p.flex, "panel");
  assert.equal(p.template, `${BAR}px ${BAR}px minmax(${MIN.panel}px, 1fr)`);
});

test("with the graph collapsed, the fixed pane is clamped against the flexible pane's minimum", () => {
  const c = columns(1200, { boardOn: true, panelOn: true, board: 600, panel: 5000, collapsed: { graph: true } });
  assert.equal(c.panel, 1200 - 3 * BAR - MIN.board);
});

test("every pane collapsed leaves only the bars", () => {
  const c = columns(2400, { boardOn: true, panelOn: true, board: 600, panel: 480, collapsed: { graph: true, board: true, panel: true } });
  assert.equal(c.flex, null);
  assert.equal(c.template, `${BAR}px ${BAR}px ${BAR}px`);
  assert.equal(c.board, 600);
  assert.equal(c.panel, 480);
});

test("the graph can be hidden (a pop-out window) and then has no bar", () => {
  const c = columns(2000, { graphOn: false, boardOn: true, panelOn: false, board: 600, panel: 480 });
  assert.equal(c.flex, "board");
  assert.equal(c.template, `${BAR}px minmax(${MIN.board}px, 1fr)`);
});

test("collapsed state is kept per pane under pane.<name>.collapsed", () => {
  assert.equal(collapsedKey("board"), "pane.board.collapsed");
});

test("a pointer that moves less than a few px is a click", () => {
  assert.equal(isClick(0), true);
  assert.equal(isClick(3), true);
  assert.equal(isClick(4), false);
  assert.equal(isClick(40), false);
});

// The dashboard (TASK-1447): a fourth pane, leftmost, hidden until opened.

test("four panes, the dashboard leftmost", () => {
  assert.deepEqual([...PANES], ["dashboard", "graph", "board", "panel"]);
  assert.equal(collapsedKey("dashboard"), "pane.dashboard.collapsed");
});

test("with the dashboard not shown, the layout is exactly as before it existed", () => {
  for (const want of [
    { boardOn: true, panelOn: true, board: 600, panel: 480 },
    { boardOn: false, panelOn: false, board: 0, panel: 480 },
    { boardOn: true, panelOn: false, board: 0, panel: 480, collapsed: { graph: true } },
  ]) {
    const c = columns(2400, want);
    assert.ok(!c.on.includes("dashboard"));
    assert.ok(!c.template.includes(`${DEFAULT_DASHBOARD}px`));
  }
  assert.equal(columns(2400, { dashboard: 900, boardOn: true, panelOn: true, board: 600, panel: 480 }).template,
    `${BAR}px minmax(${MIN.graph}px, 1fr) ${BAR}px 600px ${BAR}px 480px`);
});

test("the dashboard shown: it comes first with a width of its own, and the graph still flexes", () => {
  const c = columns(2400, { dashboardOn: true, dashboard: 860, boardOn: true, panelOn: false, board: 600, panel: 480 });
  assert.equal(c.flex, "graph");
  assert.equal(c.template, `${BAR}px 860px ${BAR}px minmax(${MIN.graph}px, 1fr) ${BAR}px 600px`);
});

test("an unset dashboard width starts with room for two boxes", () => {
  const c = columns(2400, { dashboardOn: true, dashboard: 0, boardOn: false, panelOn: false });
  assert.equal(c.dashboard, DEFAULT_DASHBOARD);
});

test("the dashboard gives way first when the stage is narrow, and never below its minimum", () => {
  const c = columns(2000, { dashboardOn: true, dashboard: 1200, boardOn: true, panelOn: true, board: 500, panel: 480 });
  assert.equal(c.board, 500);
  assert.equal(c.panel, 480);
  assert.equal(c.dashboard, 2000 - 4 * BAR - MIN.graph - 500 - 480);
  const tiny = columns(900, { dashboardOn: true, dashboard: 1200, boardOn: true, panelOn: true, board: 500, panel: 480 });
  assert.equal(tiny.dashboard, MIN.dashboard);
});

test("collapse the graph and the work board and the dashboard gets the full width", () => {
  const c = columns(2400, { dashboardOn: true, dashboard: 860, boardOn: true, panelOn: false, board: 600, collapsed: { graph: true, board: true } });
  assert.equal(c.flex, "dashboard");
  assert.equal(c.template, `${BAR}px minmax(${MIN.dashboard}px, 1fr) ${BAR}px ${BAR}px`);
});

test("the dashboard collapses behind its bar like any pane", () => {
  const c = columns(2400, { dashboardOn: true, dashboard: 860, boardOn: true, panelOn: false, board: 600, collapsed: { dashboard: true } });
  assert.equal(c.template, `${BAR}px ${BAR}px minmax(${MIN.graph}px, 1fr) ${BAR}px 600px`);
  assert.equal(c.dashboard, 860, "its width is kept for later");
});

test("a drag on a fixed pane's bar resizes that pane; on the graph's bar, the dashboard on its left", () => {
  const c = columns(2400, { dashboardOn: true, dashboard: 860, boardOn: true, panelOn: true, board: 600, panel: 480 });
  assert.deepEqual(dragTarget("board", c), { pane: "board", sign: -1 });
  assert.deepEqual(dragTarget("panel", c), { pane: "panel", sign: -1 });
  assert.deepEqual(dragTarget("dashboard", c), { pane: "dashboard", sign: -1 });
  assert.deepEqual(dragTarget("graph", c), { pane: "dashboard", sign: 1 });
});

test("without the dashboard, the graph's bar only clicks, as before", () => {
  const c = columns(2400, { boardOn: true, panelOn: true, board: 600, panel: 480 });
  assert.equal(dragTarget("graph", c), null);
  const d = columns(2400, { dashboardOn: true, dashboard: 860, boardOn: true, panelOn: true, board: 600, panel: 480, collapsed: { dashboard: true } });
  assert.equal(dragTarget("graph", d), null, "a collapsed dashboard has no width to drag");
  assert.equal(dragTarget("dashboard", d), null);
  const g = columns(2400, { boardOn: true, panelOn: true, board: 600, panel: 480, collapsed: { graph: true } });
  assert.equal(dragTarget("board", g), null, "the flexible board, with only a collapsed graph on its left");
  assert.equal(dragTarget("graph", g), null);
});
