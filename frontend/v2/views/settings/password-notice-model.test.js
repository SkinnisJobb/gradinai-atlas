import { test } from "node:test";
import assert from "node:assert/strict";
import { noticeState, minutesOf, stepUpHref, formError, RETURN_PARAM } from "./password-notice-model.js";

const SSO = { step_up: "sso", step_up_url: "/auth/oidc/step-up", step_up_seconds_left: 0, step_up_max_age: 300 };
const me = (/** @type {any} */ setup, extra = {}) => ({ kind: "session", is_service: false, password_setup: setup, ...extra });

test("no password_setup, no notice", () => {
  assert.equal(noticeState(null), null);
  assert.equal(noticeState({ kind: "session" }), null);
  assert.equal(noticeState(me(SSO, { kind: "token" })), null);
  assert.equal(noticeState(me(SSO, { is_service: true })), null);
});

test("before the step-up: confirm first, closed until asked", () => {
  assert.deepEqual(noticeState(me(SSO)), { mode: "confirm", url: "/auth/oidc/step-up", minutes: 5, open: false });
});

test("dismissed stays hidden, unless the person is coming back from the IdP", () => {
  assert.equal(noticeState(me(SSO), { dismissed: true }), null);
  assert.equal(noticeState(me(SSO), { dismissed: true, returning: true })?.open, true);
});

test("confirmed: the form, with the time left", () => {
  const s = noticeState(me({ ...SSO, step_up_seconds_left: 130 }));
  assert.deepEqual(s, { mode: "form", url: "/auth/oidc/step-up", minutes: 2, open: true });
});

test("step-up off, or an odd url: the notice says so and offers nothing", () => {
  assert.equal(noticeState(me({ step_up: "off" }))?.mode, "off");
  assert.equal(noticeState(me({ ...SSO, step_up_url: "https://evil.example/x" }))?.mode, "off");
});

test("minutes are whole and at least one", () => {
  assert.equal(minutesOf(0), 1);
  assert.equal(minutesOf(59), 1);
  assert.equal(minutesOf(300), 5);
});

test("the step-up link names the password and comes back here", () => {
  const href = stepUpHref("/auth/oidc/step-up", "/board?node=x");
  assert.ok(href.startsWith("/auth/oidc/step-up?password=1&next="));
  const next = decodeURIComponent(href.split("&next=")[1]);
  assert.equal(next, `/board?node=x&${RETURN_PARAM}=1`);
  assert.equal(decodeURIComponent(stepUpHref("/s", "/board").split("&next=")[1]), `/board?${RETURN_PARAM}=1`);
});

test("the form refuses an empty or mismatched password", () => {
  assert.equal(formError("", ""), "Choose a password.");
  assert.equal(formError("a", "b"), "The two passwords do not match.");
  assert.equal(formError("same", "same"), "");
});
