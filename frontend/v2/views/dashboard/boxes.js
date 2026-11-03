// @ts-check
// views/dashboard/boxes.js — the dashboard's boxes, in the order they are drawn
// (TASK-1447). My tasks (TASK-1450) is the first.
//
// TO ADD A BOX (TASK-1450 Mina tasks, TASK-1451 Mina beslut, TASK-1452 Flöde
// och agenter, and Väntar på beslut):
// 1. Write `views/dashboard/<id>.js` that does
//      `export default defineBox({ id, title, load, count?, drawn?, render })`
//    with `defineBox` from `../../core/dashboard.js` (the contract is there).
// 2. Import it here and put it in BOXES where it should be drawn.
// 3. Add the file to ASSET_FILES in backend/routers/assets.py.
// Keep its pure logic (what `load` returns turned into rows, `count`, `drawn`)
// testable with `node --test`, in a `*.test.js` beside it.

import myTasks from "./my-tasks.js";

/** @type {import("../../core/dashboard.js").Box[]} */
export const BOXES = [myTasks];
