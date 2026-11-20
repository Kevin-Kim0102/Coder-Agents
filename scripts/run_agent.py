#!/usr/bin/env python3
"""Dispatch local agent tasks without a shell."""
import argparse
import os
from pathlib import Path
import sys
import tempfile

import agent_providers
from cli_connections import Connections
from agent_roles import RoleStore, task_prompt


def role_invocation(role_id, remaining, task):
    role, args, prompt = prepare_role(role_id, remaining, task)
    return role['provider'], args, prompt


def prepare_role(role_id, remaining, task):
    reserved = {"--provider", "--model", "--effort", "--no-effort", "--check", "--cli-path"}
    if any(arg.split("=", 1)[0] in reserved for arg in remaining):
        raise ValueError("使用 --role 时，CLI、模型和思考强度由角色配置决定，不能覆盖或执行 --check")
    role = RoleStore().get(role_id, executable=True)
    args = [*remaining, "--model", role["model"]]
    args += ["--effort", role["effort"]] if role["effort"] else ["--no-effort"]
    return role, args, task_prompt(role, task)


def resume_selection(remaining):
    """Use a local record only when native session AND canonical cwd match."""
    values = {}
    for index, value in enumerate(remaining):
        name, separator, inline = value.partition('=')
        if name in {'--resume-id', '--cwd'}:
            values[name] = inline if separator else remaining[index + 1] if index + 1 < len(remaining) else ''
    if not values.get('--resume-id') or not values.get('--cwd'):
        return None
    import agent_tasks
    for state in agent_tasks.list_tasks():
        if state.get('provider') == 'claude' and state.get('session_id') == values['--resume-id'] \
                and isinstance(state.get('cwd'), str) and isinstance(state.get('model'), str) \
                and Path(state['cwd']).resolve() == Path(values['--cwd']).expanduser().resolve():
            return {'model': state['model'], 'effort': state.get('effort')}
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--provider", choices=tuple(agent_providers.PROVIDERS))
    parser.add_argument("--role", help="Global role ID from roles list")
    args, remaining = parser.parse_known_args(argv)
    prompt = None
    # Inherited role metadata must not label an independent direct provider call.
    os.environ.pop('CODER_AGENTS_ROLE_ID', None)
    os.environ.pop('CODER_AGENTS_ROLE_NAME', None)
    if args.role and "--help" not in remaining:
        try:
            if args.provider:
                raise ValueError("--role 与 --provider 不能同时使用，请在角色设置中选择 CLI")
            role, remaining, prompt = prepare_role(args.role, remaining, sys.stdin.read())
            args.provider = role['provider']
            os.environ['CODER_AGENTS_ROLE_ID'] = role['id']
            os.environ['CODER_AGENTS_ROLE_NAME'] = role['name']
        except (ValueError, OSError) as error:
            parser.error(str(error))
    args.provider = args.provider or Connections().snapshot()["default_provider"] or "claude"
    if not args.role and args.provider == 'claude' and '--help' not in remaining:
        # Product defaults apply only when the caller did not supply a model/effort pair.
        names = {arg.split('=', 1)[0] for arg in remaining}
        has_model = '--model' in names
        has_effort = '--effort' in names or '--no-effort' in names
        if not has_model:
            defaults = resume_selection(remaining) or Connections().defaults('claude')
            remaining += ['--model', defaults['model']]
            if not has_effort:
                remaining += ['--effort', defaults['effort']] if defaults.get('effort') else ['--no-effort']
    if "--help" in remaining:
        print("Backend: --provider kiro|claude (default: saved connection, otherwise claude)", flush=True)
        print("Roles: --role ID (reads current global role; replaces provider/model/effort)", flush=True)
    script = Path(__file__).resolve().with_name(agent_providers.provider(args.provider)["runner"])
    if prompt is not None:
        with tempfile.TemporaryFile() as stream:
            stream.write(prompt.encode("utf-8")); stream.seek(0)
            os.dup2(stream.fileno(), 0)
    os.execv(sys.executable, [sys.executable, str(script), *remaining])


if __name__ == "__main__":
    sys.exit(main())
