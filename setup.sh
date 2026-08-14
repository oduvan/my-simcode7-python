#!/bin/sh
# setup.sh — one command that prepares everything needed to test this city locally.
#
#   ./setup.sh
#
# Run this FIRST in a fresh environment (new machine, new container, new
# session). Nothing else has to be done before it. When it finishes,
#
#   robocity-sim run main.py
#
# runs your controller against the real game engine straight away.
#
# What it does:
#   1. installs the SimCode local test tooling (the `simcode` client library +
#      the `robocity-sim` CLI) if it isn't installed already;
#   2. makes sure `robocity-sim` is on your PATH;
#   3. runs a 1-tick local test, which downloads + caches the game engine under
#      ~/.cache/simcode/ and proves the whole setup actually works.
#
# It is idempotent and fast to re-run: if the tool is already installed it does
# not reinstall anything, and with the engine already cached the check takes a
# moment. If setup cannot complete it stops and says what was missing.
#
# This is LOCAL TESTING ONLY. It does not deploy anything — deploying is a
# separate action (commit + push to the default branch, then resync the city).

set -eu

TOOLS_URL="git+https://github.com/oduvan/simcode-robocity-python-tools"
ORIG_PATH="$PATH"   # your shell's PATH, before this script adds anything to its own

say()  { printf '%s\n' "$*"; }
fail() { printf 'setup.sh: %s\n' "$*" >&2; exit 1; }

here=$(dirname -- "$0")
cd -- "$here"

# Directories pip may have put the `robocity-sim` console script in. Each probe
# reports its own failure instead of being silently swallowed.
script_dirs() {
	if command -v python3 >/dev/null 2>&1; then
		if userbase=$(python3 -m site --user-base 2>&1); then
			printf '%s/bin\n' "$userbase"
		else
			say "setup.sh: note: could not read 'python3 -m site --user-base': $userbase" >&2
		fi
		if scripts=$(python3 -c 'import sysconfig; print(sysconfig.get_path("scripts"))' 2>&1); then
			printf '%s\n' "$scripts"
		else
			say "setup.sh: note: could not read sysconfig scripts path: $scripts" >&2
		fi
	fi
	printf '%s\n' /usr/local/bin
	printf '%s\n' "$HOME/.local/bin"
}

# Where an already-installed robocity-sim lives, even when PATH can't see it.
find_tool_dir() {
	script_dirs | while IFS= read -r d; do
		[ -n "$d" ] || continue
		if [ -x "$d/robocity-sim" ]; then
			printf '%s\n' "$d"
			break
		fi
	done
}

# Append one line to a file WITHOUT ever welding onto its last line: a file
# that doesn't end in a newline is common, and `foo >> file` would produce
# `export FOO=barexport PATH=...` — silently changing the user's variable.
append_line() {
	file=$1
	text=$2
	# Command substitution strips trailing newlines, so a non-empty result
	# means the last byte is NOT a newline.
	if [ -s "$file" ] && [ -n "$(tail -c 1 -- "$file" 2>/dev/null)" ]; then
		printf '\n' >>"$file"
	fi
	printf '%s\n' "$text" >>"$file"
}

# Put a directory on PATH for FUTURE shells, so the next session (or the next
# command an assistant runs, which is usually a brand-new shell) just works.
# This is the only thing this script writes outside the repo; it touches only
# shell startup files, one line each, at most once, and names every file it
# changed. The three files are not redundant: a login sh/bash reads ~/.profile,
# an interactive bash reads ~/.bashrc, zsh reads neither.
persist_on_path() {
	dir=$1
	line="export PATH=\"$dir:\$PATH\"  # added by SimCode setup.sh"
	touched=0
	for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc"; do
		[ -f "$rc" ] || continue
		if grep -Fq "$dir" "$rc" 2>/dev/null; then
			touched=1        # already there — nothing to do
			continue
		fi
		append_line "$rc" "$line" || continue
		say "PATH:     added $dir to $rc (for future shells)"
		touched=1
	done
	if [ "$touched" -eq 0 ]; then
		append_line "$HOME/.profile" "$line" || return 0
		say "PATH:     added $dir to $HOME/.profile (for future shells)"
	fi
}

install_tool() {
	# pip's own output is kept in a log and shown only if every attempt fails,
	# so a recovered attempt doesn't look like a broken setup.
	log=${TMPDIR:-/tmp}/simcode-setup-pip.log
	: >"$log" || log=/dev/null

	# 1) The plain install. Correct inside a virtualenv, and on any Python whose
	#    site-packages we may write to (pip falls back to a --user install by
	#    itself when the system one isn't writable).
	if python3 -m pip install "$TOOLS_URL" >>"$log" 2>&1; then return 0; fi

	# 2) Debian/Ubuntu (and Homebrew) mark their system Python "externally
	#    managed" (PEP 668) and refuse BOTH a plain and a --user install. We
	#    install anyway rather than stopping: this script has to be ONE command
	#    with nothing required before it, and a virtualenv the user must
	#    activate by hand would not leave `robocity-sim` runnable straight after
	#    setup. --break-system-packages only lifts the refusal; pip still writes
	#    to the first writable location (/usr/local as root, ~/.local
	#    otherwise), never into the distro's own packages.
	#
	#    NOTE — this is only safe because the tools package has NO runtime
	#    dependencies: nothing else is resolved, so pip cannot replace a
	#    distro-managed library. If the package ever gains a dependency, revisit
	#    this flag (a pipx install or a dedicated venv + a launcher would then be
	#    the safe shape).
	if grep -q "externally-managed-environment" "$log" 2>/dev/null; then
		say "tooling:  this Python is externally managed (PEP 668) — installing with --break-system-packages"
	else
		say "tooling:  the plain install failed — retrying with --break-system-packages"
	fi
	if python3 -m pip install --break-system-packages "$TOOLS_URL" >>"$log" 2>&1; then return 0; fi
	if python3 -m pip install --user --break-system-packages "$TOOLS_URL" >>"$log" 2>&1; then return 0; fi

	# Everything failed — show why.
	printf '\n--- pip output (%s) ---\n' "$log" >&2
	cat "$log" >&2 || true
	printf -- '--- end of pip output ---\n\n' >&2
	return 1
}

# --- 1. the tooling -------------------------------------------------------
toolbin=""
if command -v robocity-sim >/dev/null 2>&1; then
	say "tooling:  already installed ($(command -v robocity-sim)) — skipping install"
else
	# Cheap probe of the usual install directories BEFORE deciding to
	# reinstall: re-running in the same shell that couldn't see the tool must
	# not pay for a full re-clone + wheel build.
	toolbin=$(find_tool_dir)
	if [ -n "$toolbin" ]; then
		PATH="$toolbin:$PATH"
		export PATH
		say "tooling:  already installed ($toolbin/robocity-sim) — skipping install"
		say "PATH:     $toolbin is not on your PATH"
		persist_on_path "$toolbin"
	else
		say "tooling:  robocity-sim not found — installing $TOOLS_URL"

		command -v python3 >/dev/null 2>&1 ||
			fail "python3 not found. Install Python 3.10+ and re-run ./setup.sh"
		python3 -m pip --version >/dev/null 2>&1 ||
			fail "pip is not available for $(command -v python3). Install it (python3 -m ensurepip --upgrade, or your distro's python3-pip) and re-run ./setup.sh"
		command -v git >/dev/null 2>&1 ||
			fail "git not found. The tooling installs from a git URL, so git is required."

		install_tool ||
			fail "pip could not install $TOOLS_URL — the pip output above names the real
  failure (no network, no git, an unsupported Python, ...)."

		if ! command -v robocity-sim >/dev/null 2>&1; then
			# Installed, but its scripts directory is not on PATH.
			toolbin=$(find_tool_dir)
			[ -n "$toolbin" ] ||
				fail "pip reported success but robocity-sim was not found. Look for it under
  \"\$(python3 -m site --user-base)/bin\" (Scripts\\ on Windows), add that
  directory to PATH, and re-run ./setup.sh"
			PATH="$toolbin:$PATH"
			export PATH
			say "PATH:     robocity-sim installed to $toolbin, which is not on your PATH"
			persist_on_path "$toolbin"
		fi
		say "tooling:  installed ($(command -v robocity-sim))"
	fi
fi

# --- 2. how YOUR shell will invoke it -------------------------------------
# The PATH this script built applies to this script only. Everything printed
# below must be a command the user can actually paste into their own shell.
toolpath=$(command -v robocity-sim)
if (PATH="$ORIG_PATH"; export PATH; command -v robocity-sim >/dev/null 2>&1); then
	on_path=1
	invoke="robocity-sim"
else
	on_path=0
	invoke="$toolpath"
	[ -n "$toolbin" ] || toolbin=$(dirname -- "$toolpath")
fi

# --- 3. the engine + a real 1-tick test -----------------------------------
# The engine is downloaded from the server on first use and cached under
# ~/.cache/simcode/, so this both warms the cache and verifies the install.
say "engine:   running a 1-tick local test to warm the engine cache..."
if robocity-sim run main.py --ticks 1 >/dev/null 2>&1; then
	say "engine:   ready — local runs start instantly from now on"
else
	say "engine:   WARNING: the warm-up run did not complete."
	say "          Setup itself is done; this usually means the server is"
	say "          unreachable (offline), and the engine will be downloaded on"
	say "          your first real run. To see the actual error, run it directly:"
	say "              $invoke run main.py --ticks 1"
fi

# --- 4. what to do next ---------------------------------------------------
say ""
if [ "$on_path" -eq 1 ]; then
	say "Setup complete. Test your controller with:"
	say "    robocity-sim run main.py"
else
	say "Setup complete — but robocity-sim is NOT on this shell's PATH yet."
	say "Run this once in your current shell (it is already saved for new shells):"
	say "    export PATH=\"$toolbin:\$PATH\""
	say "Then test your controller with:"
	say "    robocity-sim run main.py"
	say "Or call it by full path right now:"
	say "    $invoke run main.py"
fi
