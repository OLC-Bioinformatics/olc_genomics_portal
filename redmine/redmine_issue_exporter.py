#!/usr/bin/env python3

"""
Redmine issue exporter (append/update safe) for local archive and migration.

This script downloads issues from a Redmine instance, stores each issue as
JSON in a local directory, and keeps state to support incremental updates
in subsequent runs.

Usage:
    python redmine_issue_exporter.py --api_key <API_KEY> \
        [--project_ids cfia] [--updated_on <ISO8601>]

By default, it reads/writes state under the repo-relative
redmine/issue_export directory.

It also includes support for readonly or ``force`` refresh modes.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
import sys
import time
from urllib.parse import quote

from redminelib import Redmine
import redminelib.exceptions
import requests
import urllib3

# Local imports
from settings import API_KEY

# Suppress certificate warnings when an explicit insecure path is used.
from requests.packages.urllib3.exceptions import InsecureRequestWarning
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
DEFAULT_OUTPUT_DIR = os.path.join(REPO_ROOT, 'redmine', 'issue_export')
DEFAULT_STATE_FILE = os.path.join(DEFAULT_OUTPUT_DIR, 'state.json')
DEFAULT_ATTACHMENT_OUTPUT_DIR = os.path.join(REPO_ROOT, 'redmine', 'attachments')
DEFAULT_ENV_FILE = os.path.join(REPO_ROOT, 'env')


def load_env_file(env_file_path=None):
    """Load KEY=VALUE pairs from an env file into os.environ."""
    env_file_path = env_file_path or DEFAULT_ENV_FILE
    if not os.path.exists(env_file_path):
        return

    with open(env_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if key not in os.environ:
                os.environ[key] = value


def resolve_ca_bundle_path(ca_bundle_path=None):
    """Return the CA bundle path to use for HTTPS verification."""
    return (
        ca_bundle_path
        or os.environ.get('REQUESTS_CA_BUNDLE')
        or os.environ.get('SSL_CERT_FILE')
        or os.environ.get('PIP_CERT')
    )

# Known resource names supported for Redmine API exporting.
# Use singular forms for redminelib attribute access.
RESOURCE_NAME_MAP = {
    'issue': 'issue',
    'issues': 'issue',
    'project': 'project',
    'projects': 'project',
    'user': 'user',
    'users': 'user',
    'tracker': 'tracker',
    'trackers': 'tracker',
    'issue_status': 'issue_status',
    'issue_statuses': 'issue_status',
    'enumeration': 'enumeration',
    'enumerations': 'enumeration',
    'custom_field': 'custom_field',
    'custom_fields': 'custom_field',
    'version': 'version',
    'versions': 'version',
    'category': 'issue_category',
    'categories': 'issue_category',
    'time_entry': 'time_entry',
    'time_entries': 'time_entry',
    'membership': 'membership',
    'memberships': 'membership',
    'role': 'role',
    'roles': 'role',
    'wiki_page': 'wiki_page',
    'wiki_pages': 'wiki_page',
    'news': 'news',
    'document': 'document',
    'documents': 'document',
    'query': 'query',
    'queries': 'query',
}

def normalize_resource_name(name):
    name = name.strip().lower()
    if not name:
        return None
    if name in RESOURCE_NAME_MAP:
        return RESOURCE_NAME_MAP[name]
    if name.endswith('s') and name[:-1] in RESOURCE_NAME_MAP:
        return RESOURCE_NAME_MAP[name[:-1]]
    return None


def list_supported_resources():
    return sorted(set(RESOURCE_NAME_MAP.keys()))


ALL_EXPORT_RESOURCES = [
    'projects', 'users', 'trackers', 'issue_statuses', 'enumerations',
    'custom_fields', 'versions', 'categories', 'roles',
    'queries', 'wiki_pages', 'news', 'documents', 'time_entries'
]


def normalize_resources_list(resources):
    return [normalize_resource_name(r) for r in resources if normalize_resource_name(r)]


def setup_logging(log_level=logging.INFO):
    """Configure standard logging behavior.

    Args:
        log_level (int): Python logging level.
            (e.g., logging.INFO, logging.DEBUG).
    """
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )


def read_last_checked(file_path):
    """Read last checked timestamp from disk.

    Args:
        file_path (str): Path to the timestamp file.

    Returns:
        str or None: Timestamp string if file exists, otherwise None.
    """
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read().strip()
    return None


def write_last_checked(file_path, timestamp):
    """Write last checked timestamp to disk.

    Args:
        file_path (str): Path to the timestamp file.
        timestamp (str): ISO 8601 timestamp to write.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(timestamp)


def read_state(file_path):
    """Read JSON state file into a dict.

    Args:
        file_path (str): Path to the state JSON file.

    Returns:
        dict: Parsed state dictionary, or empty dict if file missing or invalid
    """
    print(file_path)
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                return json.load(file)
        except (json.JSONDecodeError, ValueError) as exc:
            logging.warning(
                'State file %s is invalid JSON, resetting to empty state: %s',
                file_path,
                exc,
            )
            return {}
    return {}


def write_state(file_path, state):
    """Write dictionary state as a JSON file.

    Args:
        file_path (str): Path to the state JSON file.
        state (dict): State values to persist.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as file:
        json.dump(state, file, indent=2, sort_keys=True)


def redmine_setup(api_key, redmine_url, timeout=60, ca_bundle_path=None):
    """Creates and returns Redmine API client.

    Args:
        api_key (str): Redmine API key.
        redmine_url (str): Base URL of the Redmine server.
        timeout (int): Request timeout in seconds.
        ca_bundle_path (str, optional): Path to a PEM bundle for HTTPS.

    Returns:
        redminelib.Redmine: Configured Redmine client.
    """
    verify = resolve_ca_bundle_path(ca_bundle_path) or True
    return Redmine(
        redmine_url,
        key=api_key,
        requests={
            'verify': verify,
            'timeout': timeout,
        }
    )


def is_transient_error(exc):
    """Detect transient HTTP errors that are retryable."""
    status = None

    response = getattr(exc, 'response', None)
    if response is not None and hasattr(response, 'status_code'):
        status = response.status_code

    if status in (429, 500, 502, 503, 504):
        return True

    text = str(exc).lower()
    for token in ('429', '500', '502', '503', '504', 'timeout'):
        if token in text:
            return True

    return False


def execute_with_retry(operation, max_retries=3, backoff=1.0):
    """Perform operation with retry on transient errors."""
    attempt = 0
    while True:
        try:
            return operation()
        except Exception as exc:
            attempt += 1
            if attempt > max_retries or not is_transient_error(exc):
                raise
            delay = backoff * (2 ** (attempt - 1))
            logging.warning(
                'Transient error on attempt %s/%s: %s, sleeping %.1f sec before retry',
                attempt,
                max_retries,
                exc,
                delay,
            )
            time.sleep(delay)


def safe_serializable_issue(issue):
    """Convert issue object to JSON-serializable dict.

    Args:
        issue: redminelib issue resource.

    Returns:
        dict: JSON-compatible dictionary.
    """

    def _to_json_compatible(value):
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, dict):
            return {k: _to_json_compatible(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_to_json_compatible(v) for v in value]

        if hasattr(value, 'to_dict'):
            try:
                return _to_json_compatible(value.to_dict())
            except Exception:
                return str(value)

        if hasattr(value, '__iter__') and not isinstance(
            value, (str, bytes, bytearray)
        ):
            try:
                return [_to_json_compatible(v) for v in value]
            except Exception:
                pass

        return str(value)

    data = issue.to_dict() if hasattr(issue, 'to_dict') else issue.__dict__
    return _to_json_compatible(data)


def get_issue_project_id(issue_data):
    """Extract project id from serialized issue payload."""
    attrs = issue_data.get('_decoded_attrs', issue_data)
    project = attrs.get('project') if isinstance(attrs, dict) else None
    if isinstance(project, dict):
        return project.get('id')
    return None


def get_issue_attachments(issue_data):
    """Return serialized attachment list from issue payload."""
    attrs = issue_data.get('_decoded_attrs', issue_data)
    if not isinstance(attrs, dict):
        return []
    attachments = attrs.get('attachments') or []
    return attachments if isinstance(attachments, list) else []


def get_serialized_issue_updated(issue_data):
    """Return serialized issue updated timestamp from top-level or attrs."""
    if issue_data.get('updated_on') or issue_data.get('updated_at'):
        return issue_data.get('updated_on') or issue_data.get('updated_at')

    attrs = issue_data.get('_decoded_attrs', issue_data)
    if not isinstance(attrs, dict):
        return None

    return attrs.get('updated_on') or attrs.get('updated_at')


def build_local_attachment_url(local_redmine_url, attachment_id, filename):
    """Build local Redmine attachment download URL."""
    encoded_filename = quote(filename)
    return (
        local_redmine_url.rstrip('/')
        + f'/attachments/download/{attachment_id}/{encoded_filename}'
    )


def get_attachment_storage_path(attachment_root, attachment_id, filename):
    """Return on-disk storage path for a downloaded attachment."""
    return os.path.join(attachment_root, str(attachment_id), filename)


def issue_attachments_need_sync(
    issue_data,
    attachment_project_id,
    attachment_root,
    local_redmine_url,
):
    """Check whether issue attachments need download or URL rewrite."""
    if attachment_project_id is None:
        return False

    if get_issue_project_id(issue_data) != attachment_project_id:
        return False

    for attachment in get_issue_attachments(issue_data):
        attachment_id = attachment.get('id')
        filename = attachment.get('filename') or str(attachment_id)
        if not attachment_id:
            continue

        expected_url = build_local_attachment_url(
            local_redmine_url,
            attachment_id,
            filename,
        )
        expected_path = get_attachment_storage_path(
            attachment_root,
            attachment_id,
            filename,
        )

        if attachment.get('content_url') != expected_url:
            return True

        if not os.path.exists(expected_path):
            return True

    return False


def download_issue_attachments(
    issue_data,
    attachment_project_id,
    attachment_root,
    local_redmine_url,
    api_key,
    timeout,
    max_retries,
    retry_backoff,
    ca_bundle_path=None,
):
    """Download issue attachments and rewrite their content URLs."""
    if attachment_project_id is None:
        return issue_data

    if get_issue_project_id(issue_data) != attachment_project_id:
        return issue_data

    for attachment in get_issue_attachments(issue_data):
        attachment_id = attachment.get('id')
        if not attachment_id:
            continue

        filename = attachment.get('filename') or str(attachment_id)
        original_url = attachment.get('content_url')
        if not original_url:
            logging.warning(
                'Attachment %s is missing content_url; skipping download',
                attachment_id,
            )
            continue

        local_path = get_attachment_storage_path(
            attachment_root,
            attachment_id,
            filename,
        )
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        expected_size = attachment.get('filesize')
        needs_download = True
        if os.path.exists(local_path):
            if expected_size is None:
                needs_download = False
            else:
                needs_download = os.path.getsize(local_path) != expected_size

        if needs_download:
            logging.info(
                'Downloading attachment %s to %s',
                attachment_id,
                local_path,
            )

            def fetch_attachment(
                source_url=original_url,
                destination_path=local_path,
            ):
                verify = resolve_ca_bundle_path(ca_bundle_path) or True
                response = requests.get(
                    source_url,
                    headers={'X-Redmine-API-Key': api_key},
                    verify=verify,
                    timeout=timeout,
                )
                response.raise_for_status()
                with open(destination_path, 'wb') as attachment_file:
                    attachment_file.write(response.content)
                return True

            try:
                execute_with_retry(
                    fetch_attachment,
                    max_retries=max_retries,
                    backoff=retry_backoff,
                )
            except Exception as exc:
                logging.error(
                    'Failed to download attachment %s: %s',
                    attachment_id,
                    exc,
                )
                continue

        attachment['original_content_url'] = original_url
        attachment['content_url'] = build_local_attachment_url(
            local_redmine_url,
            attachment_id,
            filename,
        )
        if attachment_root and os.path.isabs(local_path):
            try:
                common = os.path.commonpath([os.path.abspath(local_path), os.path.abspath(attachment_root)])
            except ValueError:
                common = None
            if common == os.path.abspath(attachment_root):
                attachment['local_storage_path'] = os.path.relpath(local_path, attachment_root)
            else:
                attachment['local_storage_path'] = local_path
        else:
            attachment['local_storage_path'] = local_path

    return issue_data


def retrieve_issues(
    redmine_instance,
    project_id=None,
    updated_on=None,
    start_id=None,
    sort='id:asc',
    status_id='*',
    batch_limit=100,
    include='journals,attachments,relations,watchers',
    max_issues=None,
    sleep_seconds=0.25,
    max_retries=3,
    retry_backoff=1.0,
):
    """Retrieve issues from the Redmine server with pagination."""
    issues = []
    offset = 0

    while True:
        params = {
            'limit': batch_limit,
            'offset': offset,
            'include': include,
            'sort': sort,
        }

        if project_id:
            params['project_id'] = project_id

        if updated_on:
            params['updated_on'] = f'>={updated_on}'

        if start_id is not None:
            params['id'] = f'>{start_id}'

        if status_id is not None:
            params['status_id'] = status_id

        logging.info('Requesting issues: %s', params)

        try:
            page_issues = execute_with_retry(
                lambda: list(redmine_instance.issue.filter(**params)),
                max_retries=max_retries,
                backoff=retry_backoff,
            )
        except Exception as exc:
            logging.error('Failed to retrieve issues after retries: %s', exc)
            break

        logging.info('Retrieved %d issues', len(page_issues))

        if not page_issues:
            # End of pages, no more issues to fetch.
            break

        issues.extend(page_issues)

        if max_issues and len(issues) >= max_issues:
            issues = issues[:max_issues]
            logging.info('Reached max_issues (%s), stopping fetch', max_issues)
            break

        if len(page_issues) < batch_limit:
            # Final page (fewer than batch_limit) means data is complete.
            break

        offset += batch_limit
        time.sleep(sleep_seconds)

    return issues


def save_issue(issue, issue_dir, issue_data=None):
    """Persist a single issue to JSON file.

    Args:
        issue: Redmine issue object.
        issue_dir (str): Directory where issues are stored.

    Returns:
        str: Path to the saved JSON file.
    """
    os.makedirs(issue_dir, exist_ok=True)
    filename = os.path.join(issue_dir, f'{issue.id}.json')

    if issue_data is None:
        issue_data = safe_serializable_issue(issue)

    with open(filename, 'w', encoding='utf-8') as fp:
        json.dump(issue_data, fp, ensure_ascii=False, indent=2)

    return filename


def safe_serializable_resource(resource_obj):
    """Convert a Redmine resource object to JSON-compatible dict."""
    if hasattr(resource_obj, 'to_dict'):
        data = resource_obj.to_dict()
    else:
        data = getattr(resource_obj, '__dict__', None) or {}

    def _to_json_compatible(value):
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, dict):
            return {k: _to_json_compatible(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_to_json_compatible(v) for v in value]
        if hasattr(value, 'to_dict'):
            try:
                return _to_json_compatible(value.to_dict())
            except Exception:
                return str(value)
        if hasattr(value, '__iter__') and not isinstance(value, (str, bytes, bytearray)):
            try:
                return [_to_json_compatible(v) for v in value]
            except Exception:
                pass
        return str(value)

    return _to_json_compatible(data)


def retrieve_resources(
    redmine_instance,
    resource_name,
    params=None,
    batch_limit=100,
    max_items=None,
    sleep_seconds=0.25,
    max_retries=3,
    retry_backoff=1.0,
):
    """Retrieve Redmine resources by pagination."""
    resources = []
    offset = 0
    while True:
        query = {
            'limit': batch_limit,
            'offset': offset,
        }
        if params:
            query.update(params)

        logging.info('Requesting %s: %s', resource_name, query)

        try:
            resource_endpoint = getattr(redmine_instance, resource_name)
        except Exception as exc:
            logging.warning('Unsupported Redmine resource %s: %s', resource_name, exc)
            break

        page = []

        try:
            # Some resources (project, user, etc.) do not support filter().
            no_filter_resources = {
                'project', 'user', 'role', 'tracker', 'issue_status',
                'enumeration', 'custom_field', 'version', 'query',
                'wiki_page', 'news', 'document', 'time_entry'
            }

            def fetch_page():
                if resource_name in no_filter_resources and hasattr(resource_endpoint, 'all'):
                    return list(resource_endpoint.all(**query))
                elif hasattr(resource_endpoint, 'filter'):
                    return list(resource_endpoint.filter(**query))
                elif hasattr(resource_endpoint, 'all'):
                    return list(resource_endpoint.all(**query))
                else:
                    logging.warning('Resource %s has neither filter nor all', resource_name)
                    return []

            page = execute_with_retry(
                fetch_page,
                max_retries=max_retries,
                backoff=retry_backoff,
            )
        except Exception as exc:
            logging.warning('Unable to fetch %s via filter/all handler after retries: %s', resource_name, exc)
            break

        if not page:
            break

        resources.extend(page)

        if max_items and len(resources) >= max_items:
            resources = resources[:max_items]
            break

        if len(page) < batch_limit:
            break

        offset += batch_limit
        time.sleep(sleep_seconds)

    logging.info('Retrieved %d %s', len(resources), resource_name)
    return resources


def save_resource(resource_obj, output_dir):
    """Persist a single resource object to JSON file."""
    os.makedirs(output_dir, exist_ok=True)

    resource_id = getattr(resource_obj, 'id', None)
    if resource_id is None:
        resource_id = getattr(resource_obj, 'name', 'unknown')

    filename = os.path.join(output_dir, f'{resource_id}.json')
    payload = safe_serializable_resource(resource_obj)

    with open(filename, 'w', encoding='utf-8') as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)

    return filename


def collect_user_ids_from_issue_files(issue_dir):
    """Collect user IDs from stored issue JSON files."""
    user_ids = set()

    if not os.path.isdir(issue_dir):
        return user_ids

    for fname in os.listdir(issue_dir):
        if not fname.endswith('.json'):
            continue
        path = os.path.join(issue_dir, fname)
        try:
            with open(path, 'r', encoding='utf-8') as fp:
                issue = json.load(fp)
        except Exception:
            continue

        # issue payload uses nested attributes maybe _decoded_attrs or direct
        data = issue.get('_decoded_attrs', issue)

        for key in ('author', 'assigned_to', 'responsible', 'project'):
            val = data.get(key)
            if isinstance(val, dict):
                uid = val.get('id')
                if isinstance(uid, int):
                    user_ids.add(uid)

        # watchers may have entries
        watchers = data.get('watchers')
        if isinstance(watchers, list):
            for w in watchers:
                if isinstance(w, dict):
                    uid = w.get('id')
                    if isinstance(uid, int):
                        user_ids.add(uid)

    return user_ids


def fetch_and_save_users(redmine, user_ids, output_base, sleep_seconds=0.25, max_retries=3, retry_backoff=1.0):
    """Fetch users by ID and save JSON; skip missing/forbidden gracefully."""
    user_dir = os.path.join(output_base, 'user')
    os.makedirs(user_dir, exist_ok=True)
    found = 0

    for uid in sorted(user_ids):
        try:
            user = execute_with_retry(
                lambda: redmine.user.get(uid),
                max_retries=max_retries,
                backoff=retry_backoff,
            )
        except Exception as exc:
            logging.debug('Couldn\'t fetch user %s after retries: %s', uid, exc)
            continue

        save_resource(user, user_dir)
        found += 1
        time.sleep(sleep_seconds)

    logging.info('Fetched and saved %s users from issue references', found)
    return found


def export_resources(
    redmine,
    output_base,
    resource_name,
    params=None,
    max_items=None,
    batch_limit=100,
    sleep_seconds=0.25,
    max_retries=3,
    retry_backoff=1.0,
):
    """Export a named set of resource objects from Redmine."""
    resource_dir = os.path.join(output_base, resource_name)
    os.makedirs(resource_dir, exist_ok=True)

    all_items = retrieve_resources(
        redmine,
        resource_name=resource_name,
        params=params,
        batch_limit=batch_limit,
        max_items=max_items,
        sleep_seconds=sleep_seconds,
        max_retries=max_retries,
        retry_backoff=retry_backoff,
    )

    for item in all_items:
        save_resource(item, resource_dir)

    state_file = os.path.join(output_base, f'{resource_name}_state.json')
    write_state(state_file, {
        'resource': resource_name,
        'count': len(all_items),
        'fetched_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    })

    return len(all_items)


def export_all_resources(
    redmine,
    output_base,
    resource_names,
    project_ids=None,
    max_items=None,
    batch_limit=100,
    sleep_seconds=0.25,
    max_retries=3,
    retry_backoff=1.0,
):
    """Export a set of resources defined by name list."""
    results = {}
    for name in resource_names:
        normalized = normalize_resource_name(name)
        if not normalized:
            logging.warning('Skipping unsupported resource: %s', name)
            results[name] = 0
            continue

        params = {}
        if normalized in ('issue', 'time_entry', 'wiki_page', 'document', 'news', 'membership') and project_ids:
            params['project_id'] = project_ids

        # output folder uses original token to preserve user intent
        results[name] = export_resources(
            redmine,
            output_base=output_base,
            resource_name=normalized,
            params=params,
            max_items=max_items,
            batch_limit=batch_limit,
            sleep_seconds=sleep_seconds,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
        )
    return results


def export_issues(
    redmine,
    output_base,
    project_ids,
    updated_on,
    state_file,
    force=False,
    max_issues=None,
    start_id=None,
    status_id="*",
    sleep_seconds=0.25,
    max_retries=3,
    retry_backoff=1.0,
    download_attachments=False,
    attachment_project_id=67,
    attachment_output_dir=DEFAULT_ATTACHMENT_OUTPUT_DIR,
    local_redmine_url="http://127.0.0.1:3000",
    request_timeout=60,
    ca_bundle_path=None,
):
    """Export issues from Redmine and maintain state for incremental runs.

    Args:
        redmine: Redmine API client.
        output_base (str): Base directory for issue output and map data.
        project_ids (list[str], optional): List of project identifiers.
            Used to restrict export to selected projects.
        updated_on (str): ISO timestamp for changed-since filtering.
        state_file (str): File path where current state is stored.
        force (bool): If True, re-save all issues regardless of changed state.

    Returns:
        dict: Updated state containing last_checked and max_issue_id.
    """
    data_dir = os.path.join(output_base, 'issues')
    map_file = os.path.join(output_base, 'issue_map.json')

    existing_map = read_state(map_file)
    max_issue_id = existing_map.get('max_issue_id', 0)
    persisted_last_id = existing_map.get('last_fetched_id')

    # Prefer explicit start_id when provided, otherwise resume from persisted
    # state.
    if start_id is None:
        start_id = persisted_last_id


    # Always fetch each issue individually with full expansion to ensure journals and all associated objects are included.
    expanded_include = 'journals,attachments,relations,watchers,time_entries,children,changesets'
    all_issues = []
    if project_ids:
        for pid in project_ids:
            logging.info('Fetching issues for project %s', pid)
            project_issues = retrieve_issues(
                redmine,
                project_id=pid,
                updated_on=updated_on,
                start_id=start_id,
                status_id=status_id,
                sort='id:asc',
                max_issues=max_issues,
                sleep_seconds=sleep_seconds,
                max_retries=max_retries,
                retry_backoff=retry_backoff,
                include=expanded_include,
            )
            all_issues.extend(project_issues)
    else:
        logging.info('Fetching issues for all projects')
        all_issues = retrieve_issues(
            redmine,
            project_id=None,
            updated_on=updated_on,
            start_id=start_id,
            status_id=status_id,
            sort='id:asc',
            max_issues=max_issues,
            sleep_seconds=sleep_seconds,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            include=expanded_include,
        )

    if not all_issues:
        logging.info(
            'No issues found to export with updated_on=%s',
            updated_on,
        )

    # Track the most recent updated timestamp seen (for next incremental run).
    last_updated = updated_on
    for issue_stub in all_issues:
        # Fetch the full expanded issue by ID to ensure all fields are present.
        try:
            issue = execute_with_retry(
                lambda: redmine.issue.get(issue_stub.id, include=expanded_include),
                max_retries=max_retries,
                backoff=retry_backoff,
            )
        except Exception as exc:
            logging.error('Failed to fetch expanded issue %s: %s', issue_stub.id, exc)
            continue

        issue_file = os.path.join(data_dir, str(issue.id) + '.json')

        if os.path.exists(issue_file) and not force:
            try:
                with open(issue_file, 'r', encoding='utf-8') as fp:
                    existing = json.load(fp)
            except (json.JSONDecodeError, ValueError):
                logging.warning(
                    'Skipping corrupted issue file %s, re-saving', issue_file
                )
                existing = {}

            existing_updated = get_serialized_issue_updated(existing)
            issue_updated = (
                getattr(issue, 'updated_on', None)
                or getattr(issue, 'updated_at', None)
            )
            # Normalize datetime values to ISO strings for stable comparison.
            if isinstance(issue_updated, datetime):
                issue_updated = issue_updated.strftime('%Y-%m-%dT%H:%M:%SZ')

            if isinstance(existing_updated, datetime):
                existing_updated = existing_updated.strftime(
                    '%Y-%m-%dT%H:%M:%SZ'
                )

            needs_attachment_sync = False
            if download_attachments:
                needs_attachment_sync = issue_attachments_need_sync(
                    existing,
                    attachment_project_id,
                    attachment_output_dir,
                    local_redmine_url,
                )

            if (
                issue_updated
                and existing_updated
                and issue_updated == existing_updated
                and not needs_attachment_sync
            ):
                logging.debug('Skipping unchanged issue %s', issue.id)
                max_issue_id = max(max_issue_id, issue.id)
                if not last_updated or issue_updated > last_updated:
                    last_updated = issue_updated
                continue

        project_id = 'unknown'
        try:
            project_val = getattr(issue, 'project', None)
            if project_val is None:
                project_id = 'unknown'
            elif isinstance(project_val, dict):
                project_id = project_val.get('id', 'unknown')
            else:
                project_id = getattr(project_val, 'id', 'unknown')
        except Exception:
            project_id = 'unknown'

        logging.info(
            'Saving issue %s (project %s)',
            issue.id,
            project_id,
        )

        issue_data = safe_serializable_issue(issue)
        if download_attachments:
            issue_data = download_issue_attachments(
                issue_data,
                attachment_project_id,
                attachment_output_dir,
                local_redmine_url,
                API_KEY,
                request_timeout,
                max_retries,
                retry_backoff,
                ca_bundle_path=ca_bundle_path,
            )

        save_issue(issue, data_dir, issue_data=issue_data)

        issue_updated = (
            getattr(issue, 'updated_on', None)
            or getattr(issue, 'updated_at', None)
        )
        if isinstance(issue_updated, datetime):
            issue_updated = issue_updated.strftime('%Y-%m-%dT%H:%M:%SZ')

        if issue_updated and (
            not last_updated
            or issue_updated > last_updated
        ):
            last_updated = issue_updated

        if issue.id > max_issue_id:
            max_issue_id = issue.id

    if isinstance(last_updated, datetime):
        last_checked_value = last_updated.strftime('%Y-%m-%dT%H:%M:%SZ')
    else:
        last_checked_value = (
            last_updated
            or datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        )

    if all_issues:
        start_id = max(issue_stub.id for issue_stub in all_issues)

    new_state = {
        'last_checked': last_checked_value,
        'max_issue_id': max_issue_id,
        'start_id': start_id,
    }
    write_state(state_file, new_state)
    write_state(map_file, new_state)

    return new_state


def parse_args():
    """Parse command line arguments.

    Returns:
        argparse.Namespace: Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser(
        description='Redmine issue downloader (append/update safe).'
    )
    parser.add_argument(
        '--redmine_url',
        default='https://redmine.biodiversity.agr.gc.ca/',
        help='Redmine base URL'
    )
    parser.add_argument(
        '--output_dir',
        default=DEFAULT_OUTPUT_DIR,
        help='Local output directory for issue JSON files'
    )
    parser.add_argument(
        '--project_ids',
        default=None,
        help='Comma-separated list of Redmine project identifiers'
             ' (name or id). Default: all projects'
    )
    parser.add_argument(
        '--updated_on',
        default=None,
        help='Fetch issues updated on or after this timestamp'
             ' (ISO 8601)'
    )
    parser.add_argument(
        '--state_file',
        default=DEFAULT_STATE_FILE,
        help='File path for stashing last checked / max issue id'
    )
    parser.add_argument(
        '--max_issues',
        type=int,
        default=None,
        help='Maximum number of issues to fetch in one run (sample mode).'
    )
    parser.add_argument(
        '--start_id',
        type=int,
        default=None,
        help='Optional starting issue id (exclusive) for ascending scan.'
    )
    parser.add_argument(
        '--status_id',
        default='*',
        help=(
            'Status filter for issues; use "*" for all (default), or '
            'specific id.'
        )
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Re-save all matching issues even if unchanged'
    )
    parser.add_argument(
        '--resources',
        default='projects,users,trackers,issue_statuses,enumerations,custom_fields,versions,categories,time_entries',
        help='Comma-separated list of resources to export via API (default common set; include "all" for all except issues)'
    )
    parser.add_argument(
        '--include-issues',
        action='store_true',
        help='Also export issues (when using --resources all or explicit issue)' 
    )
    parser.add_argument(
        '--root-project',
        default=None,
        help='Root project identifier for project-scoped export (includes children)'
    )
    parser.add_argument(
        '--list-resources',
        action='store_true',
        help='List supported resources and exit'
    )
    parser.add_argument(
        '--sleep-seconds',
        type=float,
        default=0.25,
        help='Sleep seconds between paged requests (default 0.25)'
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=60,
        help='HTTP request timeout seconds (default 60)'
    )
    parser.add_argument(
        '--retries',
        type=int,
        default=3,
        help='Max retry attempts for transient 429/5xx errors (default 3)'
    )
    parser.add_argument(
        '--retry-backoff',
        type=float,
        default=1.0,
        help='Base retry backoff in seconds (default 1.0)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    parser.add_argument(
        '--download-attachments',
        action='store_true',
        help='Download issue attachments and rewrite their URLs locally'
    )
    parser.add_argument(
        '--attachment-project-id',
        type=int,
        default=67,
        help='Only download attachments for issues in this project id'
    )
    parser.add_argument(
        '--attachment-output-dir',
        default=DEFAULT_ATTACHMENT_OUTPUT_DIR,
        help='Directory where downloaded attachments are stored'
    )
    parser.add_argument(
        '--local-redmine-url',
        default='http://127.0.0.1:3000',
        help='Base URL used when rewriting exported attachment links'
    )
    parser.add_argument(
        '--ca-bundle',
        default=None,
        help='Path to a PEM certificate bundle for HTTPS verification'
    )
    return parser.parse_args()


def main():
    """Entry point for script execution."""
    load_env_file()
    args = parse_args()
    setup_logging(logging.DEBUG if args.debug else logging.INFO)

    if args.list_resources:
        print('Supported resources:')
        print(', '.join(list_supported_resources()))
        return

    # Standardize project IDs list for processing.
    project_ids = (
        [pid.strip() for pid in args.project_ids.split(',')]
        if args.project_ids
        else []
    )

    # Setup API client. API_KEY comes from settings file.
    redmine = redmine_setup(
        api_key=API_KEY,
        redmine_url=args.redmine_url,
        timeout=args.timeout,
        ca_bundle_path=args.ca_bundle,
    )

    try:
        user_supported = bool(redmine.user)
    except redminelib.exceptions.ResourceError:
        user_supported = False

    # try:
    #     users_supported = bool(redmine.users)
    # except redminelib.exceptions.ResourceError:
    #     users_supported = False

    # logging.debug('Has user support: %s, has users attr: %s', user_supported, users_supported)

    # if user_supported:
    #     def safe_user_get(uid):
    #         try:
    #             return redmine.user.get(uid)
    #         except Exception as exc:
    #             logging.debug('User %s not available: %s', uid, exc)
    #             return None

    #     logging.debug('User 222: %s', safe_user_get(222))
    #     logging.debug('User 1222: %s', safe_user_get(1222))

    #     # Optionally scan ID range with stop-on-miss pattern
    #     ids = []
    #     max_scan = 1500
    #     miss_streak = 0
    #     for uid in range(1, max_scan + 1):
    #         u = safe_user_get(uid)
    #         if u is not None:
    #             ids.append(uid)
    #             miss_streak = 0
    #         else:
    #             miss_streak += 1
    #         if miss_streak >= 30:
    #             break

    #     logging.debug('Users found in scan: %s', ids[:20])
    #     logging.debug('Total users found from scan: %s', len(ids))
    # else:
    #     logging.debug('User list access not available for this API key.')

    # Determine project scope from root project (with children) if requested.
    if args.root_project:
        root = redmine.project.get(args.root_project, include='children')
        project_ids.append(getattr(root, 'identifier', getattr(root, 'id', None)))

        def gather_children(p):
            ch = getattr(p, 'children', [])
            ids = []
            for child in ch:
                ids.append(getattr(child, 'identifier', getattr(child, 'id', None)))
                ids.extend(gather_children(child))
            return ids

        project_ids.extend(gather_children(root))
        project_ids = [p for p in set(project_ids) if p]

    # Determine initial updated_on filter from CLI or saved state.
    if args.updated_on:
        last_checked = args.updated_on
    else:
        saved_state = read_state(args.state_file)
        last_checked = saved_state.get('last_checked')

    if last_checked:
        logging.info('Starting from updated_on: %s', last_checked)
    else:
        logging.info(
            'No last checked timestamp found, retrieving all issues '
            '(this may be slow).'
        )

    os.makedirs(args.output_dir, exist_ok=True)

    requested_resources = [r.strip() for r in args.resources.split(',') if r.strip()]
    if 'all' in [r.lower() for r in requested_resources]:
        normalized_resources = normalize_resources_list(ALL_EXPORT_RESOURCES)
    else:
        normalized_resources = normalize_resources_list(requested_resources)

    state = {}

    # Flow: issue export separately if requested explicitly, or using flag.
    do_issues = args.include_issues or 'issue' in normalized_resources
    issue_user_exported = 0
    if do_issues:
        state = export_issues(
            redmine,
            output_base=args.output_dir,
            project_ids=project_ids,
            updated_on=last_checked,
            state_file=args.state_file,
            force=args.force,
            max_issues=args.max_issues,
            start_id=args.start_id,
            status_id=args.status_id,
            sleep_seconds=args.sleep_seconds,
            max_retries=args.retries,
            retry_backoff=args.retry_backoff,
            download_attachments=args.download_attachments,
            attachment_project_id=args.attachment_project_id,
            attachment_output_dir=args.attachment_output_dir,
            local_redmine_url=args.local_redmine_url,
            request_timeout=args.timeout,
            ca_bundle_path=args.ca_bundle,
        )
        normalized_resources = [r for r in normalized_resources if r != 'issue']

        # Collect user IDs from issues and fetch per-user records.
        if user_supported:
            user_ids = collect_user_ids_from_issue_files(os.path.join(args.output_dir, 'issues'))
            issue_user_exported = fetch_and_save_users(
                redmine,
                user_ids,
                args.output_dir,
                sleep_seconds=args.sleep_seconds,
                max_retries=args.retries,
                retry_backoff=args.retry_backoff,
            )
            logging.info('Issue-linked users fetched: %s', issue_user_exported)
        else:
            logging.warning('Skipping per-user fetch because user API access is restricted')

    exported = export_all_resources(
        redmine,
        output_base=args.output_dir,
        resource_names=normalized_resources,
        project_ids=project_ids,
        max_items=args.max_issues,
        sleep_seconds=args.sleep_seconds,
        max_retries=args.retries,
        retry_backoff=args.retry_backoff,
    )

    if issue_user_exported:
        exported['user'] = max(exported.get('user', 0), issue_user_exported)


    logging.info('Resource export summary: %s', exported)

    logging.info(
        'Export complete. last_checked=%s, max_issue_id=%s',
        state.get('last_checked'),
        state.get('max_issue_id'),
    )


if __name__ == '__main__':
    main()
