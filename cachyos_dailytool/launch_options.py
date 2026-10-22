"""Steam-kompatible Startoptionen (wie im Galaxy-Port).

Syntax wie im Steam-Client:
  * ``VAR=wert`` am Anfang setzt Umgebungsvariablen (``MANGOHUD=1 %command%``)
  * ``%command%`` steht für den eigentlichen Programmbefehl, alles davor ist ein Wrapper
    (``mangohud %command%``, ``gamescope -f -- %command%``)
  * alles nach ``%command%`` wird als Argument an das Programm übergeben
  * ohne ``%command%`` werden alle Token als Programmargumente angehängt (``-windowed``)
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from .i18n import tr

_ENV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
COMMAND_TOKEN = "%command%"


@dataclass
class LaunchOptions:
    env: dict[str, str] = field(default_factory=dict)
    wrapper: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    has_command_token: bool = False
    raw: str = ""

    def apply(self, argv: list[str]) -> list[str]:
        return [*self.wrapper, *argv, *self.args]

    def is_empty(self) -> bool:
        return not (self.env or self.wrapper or self.args)


class LaunchOptionsError(ValueError):
    pass


def parse(text: str) -> LaunchOptions:
    text = (text or "").strip()
    opts = LaunchOptions(raw=text)
    if not text:
        return opts
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise LaunchOptionsError(tr("Startoptionen ungültig: {0}").format(exc)) from exc
    i = 0
    while i < len(tokens):
        m = _ENV_RE.match(tokens[i])
        if not m or tokens[i].lower() == COMMAND_TOKEN:
            break
        opts.env[m.group(1)] = m.group(2)
        i += 1
    rest = tokens[i:]
    idx = next((k for k, t in enumerate(rest) if t.lower() == COMMAND_TOKEN), None)
    if idx is None:
        opts.args = rest
        return opts
    opts.has_command_token = True
    opts.wrapper = rest[:idx]
    opts.args = [t for t in rest[idx + 1:] if t.lower() != COMMAND_TOKEN]
    return opts


def apply(text: str, argv: list[str]) -> tuple[list[str], dict[str, str]]:
    opts = parse(text)
    return opts.apply(argv), dict(opts.env)


def merge(*texts: str) -> str:
    """Kombiniert mehrere Startoptionen (global + Prefix + Anwendung) zu einer Zeile."""
    env: dict[str, str] = {}
    wrappers: list[str] = []
    args: list[str] = []
    any_cmd = False
    for t in texts:
        if not t:
            continue
        o = parse(t)
        env.update(o.env)
        wrappers.extend(o.wrapper)
        args.extend(o.args)
        any_cmd = any_cmd or o.has_command_token
    parts = [f"{k}={shlex.quote(v)}" if v else f"{k}=" for k, v in env.items()]
    parts += [shlex.quote(w) for w in wrappers]
    if any_cmd or wrappers or env:
        parts.append(COMMAND_TOKEN)
    parts += [shlex.quote(a) for a in args]
    return " ".join(parts)


def describe(text: str, argv: list[str] | None = None) -> str:
    try:
        o = parse(text)
    except LaunchOptionsError as exc:
        return str(exc)
    full = o.apply(argv or ["<programm>"])
    env = " ".join(f"{k}={v}" for k, v in o.env.items())
    return (env + " " + shlex.join(full)).strip()


def parse_env_line(text: str) -> dict[str, str]:
    """``KEY=wert KEY2="wert mit leer"`` → dict (für das Feld Umgebungsvariablen)."""
    out: dict[str, str] = {}
    try:
        tokens = shlex.split(text or "")
    except ValueError:
        tokens = (text or "").split()
    for t in tokens:
        m = _ENV_RE.match(t)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def format_env(env: dict[str, str]) -> str:
    return " ".join(f"{k}={shlex.quote(v)}" if v else f"{k}=" for k, v in sorted(env.items()))
