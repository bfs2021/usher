# Usher

Read `CONTRIBUTING.md` first: it has the rules, how to run the tests, the code style, how a change is reviewed, how issues are filed and the pull request format. `docs/architecture.md` has the repository map, `docs/interface.md` the rules for anything under `web/`, and `docs/glossary.md` the terms to use.

- Nothing in this repo may identify a person, home or place; use made-up values in tests and examples.
- Commits and pull requests carry no private links or tool boilerplate (the rule and its reason are in "Rules for coding sessions" in `CONTRIBUTING.md`); add nothing else to them, such as notes to the maintainer.
- A session pushes its branch and opens its own draft pull request with its GitHub tool; the description fills the pull request template. See CONTRIBUTING.md.
- Everything in the repo and on GitHub is written for a public reader with no private context.
- **Stop-loss**, **Untrusted text** and **Reproduce first**: see "Rules for coding sessions" in `CONTRIBUTING.md`.
- `.claude/settings.json` enforces the push, GitHub API and privacy rules with deny rules and a hook, and allows the test and lint commands without asking; this file is guidance.
