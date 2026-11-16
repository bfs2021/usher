"""Refuses a Bash command that a coding session must not run.

Claude Code runs this before every Bash command (`.claude/settings.json`, `hooks.PreToolUse`) with the call as
JSON on stdin. Exit 2 refuses the command and shows the reason on stderr; exit 0 lets it run. It refuses:

- a push that skips the check for identifying details: one with `--no-verify`, or one where `core.hooksPath` is
  not `.githooks` (CONTRIBUTING.md, Checking before a push);
- `gh issue` and `gh pr`, which go through GitHub's GraphQL API that sessions can't reach;
- anything that would print the PRIVACY_PATTERNS variable: expanding it, reading it from code, or listing the
  whole environment. Mentioning the name, as in `grep -rn PRIVACY_PATTERNS tools/`, is fine.

It catches mistakes, not a determined attempt to get round it, and never reads or prints any variable's value.
The permission deny rules in the same settings file cover the plainest forms too.
"""

import json
import os
import re
import shlex
import subprocess
import sys

NAME = r"PRIVACY_PATTERNS(?![A-Za-z0-9_])"
# Checked on the whole command: the shell expands these even inside double quotes and here-documents.
EXPANSION = re.compile(r"\$\{?[#!]?" + NAME)
# Every process's environment as a file.
PROC_ENVIRON = re.compile(r"/proc/[^/\s]+/environ")
# Code that reads the variable by name, in Python, the shell's awk, Ruby, Perl, PHP, Go or Node.
READS = re.compile(
    "|".join(
        (
            r"\b(?:environ|ENV|ENVIRON)\s*\[\s*b?['\"]?" + NAME,
            r"\benvironb?\s*\.\s*(?:get|pop|setdefault)\s*\(\s*b?['\"]" + NAME,
            r"(?i:getenv)b?\s*\(\s*b?['\"]" + NAME,
            r"\bENV\s*(?:\.\s*fetch\s*\(\s*|\{\s*)['\"]?" + NAME,
            r"\bprocess\s*\.\s*env\s*(?:\.\s*|\[\s*['\"])" + NAME,
        )
    )
)
# Code that hands over the whole environment, which holds the variable.
WHOLE = re.compile(r"\bos\s*\.\s*environb?\b(?!\s*\[|\s*\.\s*(?:get|pop|setdefault)\b)|\bprocess\s*\.\s*env\b(?!\s*[.\[])|%ENV\b|\bin\s+ENVIRON\b")
HEREDOC = re.compile(r"(?<!<)<<-?(?!<)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PUNCTUATION = ";&|()<>`\n"
WRAPPERS = {"command", "builtin", "exec", "nohup", "time", "sudo"}
SHELLS = {"sh", "bash", "dash", "zsh", "ksh"}
SEARCHES = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
# git subcommands whose arguments are text to keep (a commit message, for example), not code to run.
GIT_TEXT = {"commit", "tag", "notes", "grep", "log"}
# Commands that only copy a here-document into a file or a message, so its body is text, not code.
TEXT_WRITERS = {"cat", "tee", "git"}
GIT_OPTIONS_WITH_VALUE = {"--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
ENVIRONMENT = object()  # what `env` alone stands for: a listing of the whole environment

NO_VERIFY = "a push with --no-verify skips the check for identifying details (CONTRIBUTING.md, Checking before a push). Push without it."
HOOKS_OFF = (
    "the pre-push check for identifying details is off where this push runs. "
    "Run `git config core.hooksPath .githooks` in the repository first (CONTRIBUTING.md, Checking before a push)."
)
NO_FOLDER = "can't tell which repository this push runs in. Run it from the repository's folder, or with git -C <folder>."
GRAPHQL = "gh issue and gh pr use GitHub's GraphQL API, which coding sessions can't reach. Use gh api with the REST endpoints instead."
PRINTS = (
    "this would print the PRIVACY_PATTERNS variable or the whole environment, and the patterns are never printed. "
    "tools/identifying_details.py reads them itself; to find code that uses the variable, search for its name."
)


class State:
    """What the command has done so far: the folder it is in, and core.hooksPath values it set."""

    def __init__(self, cwd):
        self.cwd = cwd
        self.hooks_paths = {}


def split_heredocs(text):
    """The command without its here-document bodies, and the bodies."""
    kept, bodies, pending = [], [], []
    for line in text.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            else:
                bodies.append(line)
            continue
        kept.append(line)
        pending.extend(delimiter for _, delimiter in HEREDOC.findall(line))
    return "\n".join(kept), "\n".join(bodies)


def split_commands(text):
    """Each simple command as a list of words, split at ; & | && || ( ) ` and new lines. Raises ValueError on an open quote."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    commands, words, in_comment = [], [], False
    for token in lexer:
        if token and set(token) <= set(PUNCTUATION) and not token.startswith(("<", ">")):
            if words:
                commands.append(words)
            words = []
            in_comment = in_comment and "\n" not in token
            continue
        if in_comment or token.startswith("#"):
            in_comment = True
            continue
        words.append(token)
    if words:
        commands.append(words)
    return commands


def drop_options(words):
    while words and words[0].startswith("-"):
        words = words[1:]
    return words


def unwrap(words):
    """The command that runs, without leading assignments and wrappers such as `env` or `timeout`."""
    while words:
        first = words[0]
        if ASSIGNMENT.match(first):
            words = words[1:]
        elif first in WRAPPERS:
            words = drop_options(words[1:])
        elif first == "timeout":
            words = drop_options(words[1:])[1:]
        elif first == "env":
            rest = words[1:]
            while rest and (rest[0].startswith("-") or ASSIGNMENT.match(rest[0])):
                rest = rest[2:] if rest[0] in ("-u", "-C", "-S", "--unset", "--chdir", "--split-string") else rest[1:]
            if not rest:
                return ENVIRONMENT
            words = rest
        else:
            return words
    return words


def resolve(base, path):
    """The folder `path` names from `base`, or None when it can't be known before the command runs."""
    if path == "-" or "$" in path or "`" in path:
        return None
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base, path)) if base else None


def configured_hooks_path(folder):
    try:
        found = subprocess.run(["git", "-C", folder, "config", "--get", "core.hooksPath"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return found.stdout.strip()


def record_hooks_path(args, folder, state):
    """Notes a `git config` that sets or unsets core.hooksPath, so a push later in the same command sees it."""
    names = [w for w in args if not w.startswith("-")]
    unset = "--unset" in args or names[:1] == ["unset"]
    if names[:1] in (["set"], ["unset"]):
        names = names[1:]
    if not folder or not names or names[0].lower() != "core.hookspath":
        return
    if unset:
        state.hooks_paths[folder] = ""
    elif len(names) > 1:
        state.hooks_paths[folder] = names[1]


def check_git(words, state):
    folder, overrides, index = state.cwd, {}, 1
    while index < len(words) and words[index].startswith("-"):
        option = words[index]
        if option in ("-C", "-c") and index + 1 < len(words):
            value = words[index + 1]
            if option == "-C":
                folder = resolve(folder, value)
            else:
                key, _, setting = value.partition("=")
                overrides[key.lower()] = setting
            index += 2
        else:
            index += 2 if option in GIT_OPTIONS_WITH_VALUE else 1
    if index >= len(words):
        return None
    subcommand, rest = words[index], words[index + 1 :]
    if subcommand == "config":
        record_hooks_path(rest, folder, state)
        return None
    if subcommand != "push":
        return None
    # git takes any unambiguous start of a long option, so --no-verif is --no-verify too.
    if any(w.startswith("--no-veri") for w in rest):
        return NO_VERIFY
    if "core.hookspath" in overrides:
        hooks_path = overrides["core.hookspath"]
    elif folder is None:
        return NO_FOLDER
    elif folder in state.hooks_paths:
        hooks_path = state.hooks_paths[folder]
    else:
        hooks_path = configured_hooks_path(folder)
    return None if hooks_path and os.path.normpath(hooks_path) == ".githooks" else HOOKS_OFF


def lists_environment(command, args):
    names = [w for w in args if not w.startswith("-")]
    options = "".join(w for w in args if w.startswith("-"))
    if command == "printenv":
        return not names or "PRIVACY_PATTERNS" in names
    if command == "set":
        return not args
    if command == "export":
        return not names
    if command in ("declare", "typeset"):
        return "PRIVACY_PATTERNS" in names or (not names and "f" not in options.lower())
    if command == "ps":  # BSD-style `ps e` shows each process's environment
        return bool(args) and not args[0].startswith("-") and "e" in args[0]
    return False


def git_subcommand(args):
    words = drop_options(args)
    return words[0] if words else ""


def is_text(command, args):
    """Whether the arguments are text to search for or keep, such as a pattern or a commit message, not code."""
    if command in SEARCHES:
        return True
    if command == "git":
        return git_subcommand(args) in GIT_TEXT
    if command == "xargs":  # `... | xargs grep -n <name>` searches too
        return any(os.path.basename(w) in SEARCHES for w in args)
    return False


def only_writes_text(commands):
    """Whether every command just copies text (cat, tee, or a git commit message), so here-document bodies are text."""
    for words in commands:
        words = unwrap(words)
        if words is ENVIRONMENT or not words:
            return False
        command = os.path.basename(words[0])
        if command not in TEXT_WRITERS or (command == "git" and git_subcommand(words[1:]) not in GIT_TEXT):
            return False
    return True


def check_command(words, state, depth):
    words = unwrap(words)
    if words is ENVIRONMENT:
        return PRINTS
    if not words:
        return None
    command, args = os.path.basename(words[0]), words[1:]
    for word in words:
        if "$(" in word or "`" in word:
            reason = scan(word, state, depth + 1)
            if reason:
                return reason
    if command in SHELLS:
        flags = [w for w in args if w.startswith("-") and not w.startswith("--")]
        scripts = drop_options(args)
        if any("c" in f for f in flags) and scripts:
            return scan(scripts[0], state, depth + 1)
    if command == "eval":
        return scan(" ".join(args), state, depth + 1)
    if command == "cd":
        targets = drop_options(args)
        state.cwd = resolve(state.cwd, targets[0] if targets else "~")
        return None
    if command == "gh" and drop_options(args)[:1] in (["issue"], ["pr"]):
        return GRAPHQL
    if lists_environment(command, args):
        return PRINTS
    joined = " ".join(words)
    if is_text(command, args):
        return check_git(words, state) if command == "git" else None
    if READS.search(joined) or WHOLE.search(joined) or PROC_ENVIRON.search(joined):
        return PRINTS
    if command == "git":
        return check_git(words, state)
    return None


def scan(text, state, depth=0):
    """The reason to refuse `text`, or None to let it run."""
    if depth > 5:
        return None
    if EXPANSION.search(text):
        return PRINTS
    text, bodies = split_heredocs(text)
    try:
        commands = split_commands(text)
    except ValueError:
        # The shell refuses an open quote too, so only code that would print the variable matters here.
        return PRINTS if READS.search(text) or WHOLE.search(text) or READS.search(bodies) or WHOLE.search(bodies) else None
    if not only_writes_text(commands) and (READS.search(bodies) or WHOLE.search(bodies) or PROC_ENVIRON.search(bodies)):
        return PRINTS
    for words in commands:
        reason = check_command(words, state, depth)
        if reason:
            return reason
    return None


def main():
    try:
        return guard(json.load(sys.stdin))
    except Exception:  # noqa: BLE001 - odd input, even nested too deep to parse, lets the command run quietly
        return 0


def guard(event):
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return 0
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return 0
    cwd = event.get("cwd")
    reason = scan(command, State(cwd if isinstance(cwd, str) and cwd else os.getcwd()))
    if reason is None:
        return 0
    print(f"session_guard: refused: {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
