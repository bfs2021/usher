#!/usr/bin/env python3
"""Look for identifying details in the commits a push would publish, before they leave this machine.

Tests runs the same check after a push (the "Look for identifying details" step in .github/workflows/tests.yml),
but by then a public branch, and a pull request's commits under refs/pull/*, can already be read. This runs first.

The patterns are one extended regular expression per line, matched case-insensitively, as in Tests. They come from
the PRIVACY_PATTERNS environment variable, else the file PRIVACY_PATTERNS_FILE names, else DEFAULT_FILE, and never
from inside the repository. Only commit, file and line are printed: never a pattern, the text it matched, or a
path that matches one. Like Tests, it reads text files only (git grep -I); it also checks each commit's message and
file paths, but not author names, tag messages or ref names.

    python3 tools/identifying_details.py [<remote>]       the commits on HEAD that <remote> (origin) doesn't have
    python3 tools/identifying_details.py --pre-push <remote>   the refs git's pre-push hook passes on stdin
    python3 tools/identifying_details.py --tree <file>     the tracked files against the patterns in <file>, printing
                                                           path:line (Tests runs this; a matching path is only counted)
    python3 tools/identifying_details.py --fields <patterns file> <title file> <description file>
                                                           a pull request's title and description against the patterns,
                                                           printing only which of the two matched (Tests runs this)
    python3 tools/identifying_details.py --squash <patterns file> <subject file> <message file>
                                                           the subject and message Auto-merge is about to give a squash
                                                           commit, printing only which of the two matched
    python3 tools/identifying_details.py --head-message <patterns file>
                                                           the newest commit's message (Tests runs this on main, where
                                                           no pull request text is read), printing only that it matched
    python3 tools/identifying_details.py --check-patterns <file>   only check that each line is a valid pattern
                                                                    (Tests runs this before its own search)

Exit status 0 when nothing was found, 1 when something was or the check couldn't run (so the push stops).
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_FILE = Path("~/.config/usher/privacy-patterns")
NO_COMMIT = "0" * 40
HOW_TO_PROVIDE = (
    "Put the patterns, one extended regular expression per line, in the PRIVACY_PATTERNS environment variable,\n"
    f"or in a file outside the repository: {DEFAULT_FILE}, or the path in PRIVACY_PATTERNS_FILE.\n"
    "CONTRIBUTING.md says how."
)


class CheckError(Exception):
    """The check couldn't run; the message says why and never includes a pattern."""


def _git(*args, check=True):
    result = subprocess.run(["git", *args], capture_output=True, encoding="utf-8", errors="replace", check=False)
    if check and result.returncode != 0:
        raise CheckError(f"git {args[0]} failed (exit {result.returncode}).")
    return result


def read_patterns(top):
    """The patterns as text, one per line without blank lines, or a CheckError saying how to provide them or which
    line isn't valid."""
    text = os.environ.get("PRIVACY_PATTERNS", "")
    if not text.strip():
        named = os.environ.get("PRIVACY_PATTERNS_FILE", "")
        path = Path(named or DEFAULT_FILE).expanduser().resolve()
        if path.is_relative_to(top):
            raise CheckError("The patterns file is inside the repository. Keep it outside, where git can't commit it.")
        if not path.is_file():
            raise CheckError("No patterns for identifying details, so the push is stopped.\n" + HOW_TO_PROVIDE)
        text = path.read_text(encoding="utf-8")
    check_patterns(text)
    lines = [line for line in text.replace("\r", "").split("\n") if line.strip()]
    if not lines:
        raise CheckError("The patterns for identifying details are empty, so the push is stopped.\n" + HOW_TO_PROVIDE)
    return "\n".join(lines) + "\n"


def _rejects(command, cwd):
    # Both tools exit 2 or more on a pattern they can't compile, and their error text quotes it, so it is dropped.
    return subprocess.run(command, cwd=cwd, capture_output=True, check=False).returncode > 1


def check_patterns(text):
    """A CheckError naming the first line (counting blank ones, as the patterns are written) that git grep -E or
    grep -E can't compile. Each line is tried on its own, in an empty folder, so nothing is searched; the tools'
    error text, which quotes the pattern, is never shown."""
    with tempfile.TemporaryDirectory() as folder:
        empty = os.path.join(folder, "empty")
        os.mkdir(empty)
        one = os.path.join(folder, "pattern")
        for number, line in enumerate(text.replace("\r", "").split("\n"), start=1):
            if not line.strip():
                continue
            with open(one, "w", encoding="utf-8") as handle:
                handle.write(line + "\n")
            if _rejects(["git", "grep", "--no-index", "-q", "-i", "-E", "-f", one], empty) or _rejects(
                ["grep", "-q", "-a", "-i", "-E", "-f", one, os.devnull], empty
            ):
                raise CheckError(f"Line {number} of the patterns isn't a valid extended regular expression, so the check stopped.")


def commits_to_check(tip, remote, known_remote_commit=None):
    """The commits reachable from tip that the remote doesn't have yet: each one becomes readable once pushed."""
    exclude = [f"--remotes={remote}"]
    if known_remote_commit and _git("cat-file", "-e", f"{known_remote_commit}^{{commit}}", check=False).returncode == 0:
        exclude.append(known_remote_commit)
    return _git("rev-list", tip, "--not", *exclude).stdout.split()


def _matched(result):
    # git grep and grep exit 1 when nothing matches; anything above that is an error, whose text can quote a pattern.
    if result.returncode > 1:
        raise CheckError("A pattern isn't a valid extended regular expression, or the search failed.")
    return result.returncode == 0


def _grep(pattern_file, text, *options):
    # -a: a message or path list is text even when grep's locale would call it binary.
    result = subprocess.run(
        ["grep", "-a", "-i", "-E", *options, "-f", pattern_file],
        input=text,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout if _matched(result) else ""


def _places(output, commit):
    """(path, line number) per match in git grep --null output: 'rev:path\\0number\\0text\\n' repeated.

    Split on NUL, not on lines: a path can hold a line break, the matched text (from a text file) can't hold a NUL,
    and only the text, which is dropped, runs from a number to the next newline."""
    fields = output.split("\0")
    path = fields[0]
    for index in range(1, len(fields) - 1, 2):
        yield path.removeprefix(commit + ":"), fields[index]
        path = fields[index + 1].partition("\n")[2]


def _tree_findings(label, commit, pattern_file):
    """Places in a commit's tree that match: 'abc1234 path:12', a count of hidden paths, or a line saying a path isn't
    shown. A path that matches, or holds a line break, is counted, never printed: printing it would show the detail.
    The text search is the same as Tests' (git grep -n -I -i -E -f), on the commit's tree, or with no commit on the
    tracked files of the working tree."""
    found = []
    prefix = f"{label} " if label else ""
    paths = _git("ls-tree", "-r", "-z", "--name-only", commit).stdout if commit else _git("ls-files", "-z").stdout
    hidden = set(filter(None, _grep(pattern_file, paths, "-z").split("\0")))
    if hidden:
        found.append(f"{prefix}{len(hidden)} file path(s) that match (not shown; find them with the patterns)")
    # --null keeps a path with a colon whole; the matched text after the line number is dropped.
    result = _git("grep", "--no-color", "-n", "-I", "-i", "-E", "--null", "-f", pattern_file, *([commit] if commit else []), "--", check=False)
    if _matched(result):
        for path, number in _places(result.stdout, commit or ""):
            if path in hidden or "\n" in path or not number.isdigit():
                found.append(f"{prefix}a file whose path isn't shown (it matches, or holds a line break)")
            else:
                found.append(f"{prefix}{path}:{number}")
    return found


def _matches(pattern_file, text):
    return bool(_grep(pattern_file, text, "-c"))  # -c prints a count only on a match; no match exits 1 and prints nothing


def find_details(commits, pattern_file):
    """Places that match, per commit: 'abc1234 path:12', 'abc1234 commit message, line 3', or a hidden path."""
    found = []
    seen_trees = set()
    for commit in commits:
        short = commit[:7]
        message = _git("log", "-1", "--encoding=UTF-8", "--format=%B", commit).stdout
        found += [f"{short} commit message, line {line.split(':', 1)[0]}" for line in _grep(pattern_file, message, "-n").split("\n") if line]
        tree = _git("rev-parse", f"{commit}^{{tree}}").stdout.strip()
        if tree in seen_trees:
            continue
        seen_trees.add(tree)
        found += _tree_findings(short, commit, pattern_file)
    return list(dict.fromkeys(found))


def pushed_commits(remote, ref_lines):
    """The new commits of every ref git's pre-push hook names: '<local ref> <sha> <remote ref> <sha>' per line."""
    commits = []
    for line in ref_lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == NO_COMMIT:  # a deleted ref publishes nothing
            continue
        for commit in commits_to_check(parts[1], remote, None if parts[3] == NO_COMMIT else parts[3]):
            if commit not in commits:
                commits.append(commit)
    return commits


def _check_fields(argv, names, what, sentence):
    """Two text files against the patterns in a third, naming only the ones that matched."""
    flag = argv[0]
    if len(argv) != 4:
        raise CheckError(f"Usage: identifying_details.py {flag} <patterns file> <{names[0]} file> <{names[1]} file>")
    try:
        fields = [(name, Path(path).read_text(encoding="utf-8")) for name, path in zip(names, argv[2:], strict=True)]
    except (OSError, UnicodeError):
        raise CheckError(f"The {what} couldn't be read.") from None
    found = [name for name, text in fields if _matches(argv[1], text)]
    if not found:
        return 0
    print("Identifying details found in " + sentence.format(" and ".join(found)))
    return 1


def run(argv, stdin):
    if argv[:1] == ["--check-patterns"]:
        if len(argv) != 2:
            raise CheckError("Usage: identifying_details.py --check-patterns <file>")
        try:
            text = Path(argv[1]).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise CheckError("The patterns file couldn't be read as UTF-8 text.") from None
        check_patterns(text)
        return 0
    if argv[:1] == ["--fields"]:
        return _check_fields(argv, ("title", "description"), "pull request text", "the pull request {}. Remove them from the text.")
    if argv[:1] == ["--squash"]:
        return _check_fields(argv, ("subject", "message"), "squash commit text", "the squash commit {}. It is not merged.")
    if argv[:1] == ["--head-message"]:
        if len(argv) != 2:
            raise CheckError("Usage: identifying_details.py --head-message <patterns file>")
        message = _git("log", "-1", "--encoding=UTF-8", "--format=%B", "HEAD", check=False)
        if message.returncode:
            raise CheckError("The newest commit's message couldn't be read, so it was not checked.")
        if _matches(argv[1], message.stdout):
            print("Identifying details found in the newest commit's message.")
            return 1
        return 0
    if argv[:1] == ["--tree"]:
        if len(argv) != 2:
            raise CheckError("Usage: identifying_details.py --tree <patterns file>")
        found = list(dict.fromkeys(_tree_findings("", None, argv[1])))
        if found:
            print("Identifying details found. Remove them and use the environment instead:")
            # Indented, as the pre-push output is: a path starting with :: must not read as a workflow command.
            print("\n".join(f"  {place}" for place in found))
            return 1
        return 0
    top = Path(_git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
    patterns = read_patterns(top)
    if argv[:1] == ["--pre-push"]:
        remote = argv[1] if len(argv) > 1 else "origin"
        commits = pushed_commits(remote, stdin.read().splitlines())
    else:
        remote = argv[0] if argv else "origin"
        commits = commits_to_check("HEAD", remote)
        if not commits:
            print(f"{remote} has every commit on HEAD already; checking HEAD itself.")
            commits = [_git("rev-parse", "HEAD").stdout.strip()]
    if not commits:
        print("Nothing new to push, so no identifying details to look for.")
        return 0
    with tempfile.TemporaryDirectory() as folder:
        pattern_file = os.path.join(folder, "patterns")
        with open(pattern_file, "w", encoding="utf-8") as handle:  # a temporary file outside the repo, removed below
            handle.write(patterns)
        found = find_details(commits, pattern_file)
    if found:
        print("Identifying details found, so the push is stopped. Remove them, rewrite the commits that hold them")
        print("(they aren't pushed yet, so amending or rebasing is safe) and push again:")
        print("\n".join(f"  {place}" for place in found))
        return 1
    count = len(patterns.splitlines())
    print(f"No identifying details in {len(commits)} commit(s) ({count} patterns checked).")
    return 0


def main():
    try:
        return run(sys.argv[1:], sys.stdin)
    except CheckError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
