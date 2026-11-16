<!-- Title: Conventional Commits, `type(scope): summary`, imperative, at most 72 characters, no version. It becomes the squash commit's subject. -->

## Summary

<!-- The problem and the behaviour you expect, in a few lines. -->

## What changed

<!-- The change itself. Mention the server only if the change touches one of these files, otherwise say nothing about it: `Dockerfile`, `deploy/compose.yaml`, `deploy/usher-update` or `deploy/usher-autodeploy.*`; then say what has to be run on the server. -->

## How to test

<!-- How to see that it works. It names the command to run, not what it printed: no test count or result. -->

<!-- yes when the change alters how any screen looks, so it is checked on a phone; no otherwise. -->
Changes how a screen looks:

<!-- The Tests workflow checks the lines below (.github/scripts/pr_gates.py).
Add `Reviewer: ran` on its own line once a review has read the diff (CONTRIBUTING.md, Review). It is required when the change touches sign-in or sessions, anything kept on the device (such as `web/sw.js` or `web/data.js`), the workflows, the Dockerfile, compose files or `deploy/`, or changes more than 300 lines.
A change to `usher/` or `web/` that changes nothing under `tests/` needs a line `Tests: not needed — <reason>`.
A change to `usher/`, `web/`, `deploy/` or the `Dockerfile` that adds no line to `CHANGELOG.md` needs a line `Changelog: not needed — <reason>`; a change to only `.github/`, `tests/`, the coding-session settings folder or documentation leaves `CHANGELOG.md` unchanged (CONTRIBUTING.md, Changelog). -->

Fixes #
