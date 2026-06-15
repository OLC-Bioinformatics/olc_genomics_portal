#!/usr/bin/env python3

"""
Script to render template files for remote deployment based on environment
variables
"""

# Standard library imports
from pathlib import Path
import os
import secrets
import sys


def parse(path: Path) -> dict[str, str]:
    """
    Parse env file with KEY=VALUE lines, ignoring comments and empty lines.
    
    Parameters:
        path (Path): Path to the env file.

    Returns:
        dict[str, str]: A dictionary of key-value pairs from the env file.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    with path.open(encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or '=' not in line:
                continue
            key, _, value = line.rstrip('\n').partition('=')
            values[key] = value
    return values


def render_templates(remote_dir: Path, env_values: dict[str, str]) -> None:
    """
    Render template files with environment values.

    Parameters:
        remote_dir (Path): Path to the remote directory.
        env_values (dict[str, str]): Dictionary of environment values.
    """
    secrets_src = remote_dir / 'redmine' / 'config' / 'secrets.yml.template'
    secrets_dst = remote_dir / 'redmine' / 'config' / 'secrets.yml'
    text = secrets_src.read_text(encoding='utf-8')
    secret_key = env_values.get('SECRET_KEY_BASE') or env_values.get(
        'secret-key-base'
    )
    if not secret_key:
        secret_key = secrets.token_hex(64)
        print(
            'Generated SECRET_KEY_BASE fallback for Redmine', file=sys.stderr
        )
    text = text.replace('${SECRET_KEY_BASE}', secret_key)
    secrets_dst.write_text(text, encoding='utf-8')

    db_src = remote_dir / 'redmine' / 'config' / 'database.yml.template'
    db_dst = remote_dir / 'redmine' / 'config' / 'database.yml'
    db_text = db_src.read_text(encoding='utf-8')
    db_password = (
        env_values.get('REDMINE_DB_PASSWORD')
        or env_values.get('REDMINE_DATABASE_PASSWORD')
        or env_values.get('redmine-database-password')
    )
    if not db_password:
        raise SystemExit(
            'Required REDMINE_DB_PASSWORD / REDMINE_DATABASE_PASSWORD / '
            'redmine-database-password is missing in env file'
        )
    db_text = db_text.replace('${REDMINE_DB_PASSWORD}', db_password)
    db_dst.write_text(db_text, encoding='utf-8')

    src = remote_dir / 'nginx' / 'templates' / 'django.template'
    dst = remote_dir / 'nginx' / 'sites-enabled' / 'django'
    text = src.read_text(encoding='utf-8')
    django_server_name = env_values.get('DJANGO_SERVER_NAME')
    if not django_server_name:
        raise SystemExit(
            'Required env file value DJANGO_SERVER_NAME is missing'
        )
    text = text.replace('${DJANGO_SERVER_NAME}', django_server_name)
    dst.write_text(text, encoding='utf-8')

    https_dst = remote_dir / 'nginx' / 'sites-enabled' / 'django-https'
    deploy_env = env_values.get('DEPLOY_ENVIRONMENT', 'dev').lower()
    if deploy_env == 'prod':
        redmine_server_name = env_values.get('REDMINE_SERVER_NAME')
        if not redmine_server_name:
            raise SystemExit(
                'Required env file value REDMINE_SERVER_NAME is missing'
            )
        src = remote_dir / 'nginx' / 'templates' / 'django-https.template'
        text = src.read_text(encoding='utf-8')
        text = text.replace('${DJANGO_SERVER_NAME}', django_server_name)
        text = text.replace('${REDMINE_SERVER_NAME}', redmine_server_name)
        https_dst.write_text(text, encoding='utf-8')
    else:
        if https_dst.exists():
            https_dst.unlink()


if __name__ == '__main__':
    remote_directory = Path(os.environ.get('REMOTE_DIR', ''))
    if not remote_directory:
        raise SystemExit('REMOTE_DIR environment variable is required')
    environment_values = parse(remote_directory / 'env')
    render_templates(remote_directory, environment_values)
