#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Redmine issue importer from local JSON export.

Reads `issue_export/issues/*.json` and writes into a local SQLite database.
It preserves original issue IDs when possible by storing a `legacy_id` and
attempting to set `id` to that value; on collision it falls back to
autoincrement.

Usage:
    python redmine_issue_importer.py --source_dir ... --db_path ...

Attributes:
    None
"""
from datetime import datetime, timezone, timedelta
import argparse
import json
import logging
import os
import re
import sqlite3
import sys

# Third-party imports
from redminelib import Redmine as RemoteRedmine
import requests
try:
    import pymysql
except ImportError:
    pymysql = None

# Local imports
from settings import DEV_API_KEY

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


# Keeps track of the highest ID we’ve ever generated for each resource in this run.
target_max_cache = {}


def setup_logging(log_level=logging.INFO):
    """Configure standard logging behavior."""
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def normalize_datetime(value):
    """Normalize timestamp values to a stable ISO 8601 string.

    Args:
        value: Date/time as int timestamp, float, ISO string, or None.

    Returns:
        str or None: Normalized ISO 8601 value or None for null input.
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value).isoformat() + 'Z'

    if isinstance(value, str):
        try:
            # Convert UTC Z to timezone-aware +00:00 before isoformat.
            if value.endswith('Z'):
                return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).isoformat()
            if len(value) >= 6 and (value[-6] == '+' or value[-6] == '-') and value[-3] == ':':
                main_part = value[:-6]
                tz_part = value[-6:]
                dt = datetime.strptime(main_part, '%Y-%m-%dT%H:%M:%S')
                sign = 1 if tz_part[0] == '+' else -1
                hours = int(tz_part[1:3])
                minutes = int(tz_part[4:6])
                offset = timezone(sign * timedelta(hours=hours, minutes=minutes))
                return dt.replace(tzinfo=offset).isoformat()
            return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S').isoformat()
        except Exception:
            return value

    return str(value)


def normalize_identifier(value):
    if not value:
        return None
    value = str(value).strip().lower()
    # keep letters, digits, hyphen
    value = re.sub(r'[^a-z0-9]+', '-', value)
    value = value.strip('-')
    if not value:
        return None
    # ensure length within Redmine limits (e.g., 255)
    return value[:255]


def maybe_pause(message, interactive=False):
    if not interactive:
        return
    try:
        input('[PAUSE] {0}  Press ENTER to continue...'.format(message))
    except KeyboardInterrupt:
        print('\nInterrupted by user; exiting.')
        sys.exit(1)


def create_schema(conn):
    """Create SQLite schema for issues and supporting resources."""
    with conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY,
                legacy_id INTEGER UNIQUE,
                project_id INTEGER,
                tracker_id INTEGER,
                status_id INTEGER,
                author_id INTEGER,
                assigned_to_id INTEGER,
                priority_id INTEGER,
                category_id INTEGER,
                fixed_version_id INTEGER,
                parent_id INTEGER,
                subject TEXT,
                description TEXT,
                start_date TEXT,
                due_date TEXT,
                created_on TEXT,
                updated_on TEXT,
                closed_on TEXT,
                done_ratio INTEGER,
                is_private INTEGER,
                estimated_hours REAL,
                spent_hours REAL,
                total_spent_hours REAL,
                raw_json TEXT
            );
        ''')

        # Backfill on existing schema that may not yet have priority_id
        try:
            conn.execute('ALTER TABLE issues ADD COLUMN priority_id INTEGER')
        except Exception:
            pass

        # generic resource tables for other Redmine resources
        resource_tables = [
            'projects', 'users', 'trackers', 'issue_statuses', 'enumerations',
            'custom_fields', 'versions', 'issue_categories', 'roles', 'queries',
            'wiki_pages', 'news', 'documents', 'time_entries'
        ]

        for table in resource_tables:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS {0} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    legacy_id INTEGER UNIQUE,
                    name TEXT,
                    project_id INTEGER,
                    raw_json TEXT
                );
            '''.format(table))

        # track journals (notes/history) from issue exports
        conn.execute('''
            CREATE TABLE IF NOT EXISTS journals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_id INTEGER UNIQUE,
                issue_legacy_id INTEGER,
                user_legacy_id INTEGER,
                notes TEXT,
                created_on TEXT,
                raw_json TEXT
            );
        ''')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS issue_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_id INTEGER UNIQUE,
                issue_legacy_id INTEGER,
                filename TEXT,
                content_type TEXT,
                description TEXT,
                filesize INTEGER,
                local_storage_path TEXT,
                raw_json TEXT
            );
        ''')


def extract_issue_fields(issue_payload):
    """Extract fields from raw issue JSON for structured DB import."""
    attrs = issue_payload.get('_decoded_attrs', {})

    project = attrs.get('project')
    tracker = attrs.get('tracker')
    status = attrs.get('status')
    author = attrs.get('author')
    assigned_to = attrs.get('assigned_to')

    priority = attrs.get('priority') if isinstance(attrs.get('priority'), dict) else None
    category = attrs.get('category') if isinstance(attrs.get('category'), dict) else None
    fixed_version = attrs.get('fixed_version') if isinstance(attrs.get('fixed_version'), dict) else None
    parent = attrs.get('parent') if isinstance(attrs.get('parent'), dict) else None
    return {
        'legacy_id': attrs.get('id'),
        'project_id': project.get('id') if isinstance(project, dict) else None,
        'tracker_id': tracker.get('id') if isinstance(tracker, dict) else None,
        'status_id': status.get('id') if isinstance(status, dict) else None,
        'author_id': author.get('id') if isinstance(author, dict) else None,
        'assigned_to_id': assigned_to.get('id') if isinstance(assigned_to, dict) else None,
        'priority_id': priority.get('id') if priority else None,
        'category_id': category.get('id') if category else None,
        'fixed_version_id': fixed_version.get('id') if fixed_version else None,
        'parent_id': parent.get('id') if parent else None,
        'subject': attrs.get('subject'),
        'description': attrs.get('description'),
        'start_date': attrs.get('start_date'),
        'due_date': attrs.get('due_date'),
        'created_on': normalize_datetime(attrs.get('created_on')),
        'updated_on': normalize_datetime(attrs.get('updated_on')),
        'closed_on': normalize_datetime(attrs.get('closed_on')),
        'done_ratio': attrs.get('done_ratio'),
        'is_private': 1 if attrs.get('is_private') else 0,
        'estimated_hours': attrs.get('estimated_hours'),
        'spent_hours': attrs.get('spent_hours'),
        'total_spent_hours': attrs.get('total_spent_hours'),
    }


def import_issue_journals(conn, issue_legacy_id, issue_payload):
    """Store issue journals entries from export into SQLite."""
    try:
        attrs = issue_payload.get('_decoded_attrs', {})
        journals = attrs.get('journals') or []
    except Exception:
        journals = []

    if not journals:
        return

    with conn:
        for journal in journals:
            legacy_id = journal.get('id')
            if not legacy_id:
                continue

            existing = conn.execute(
                'SELECT id FROM journals WHERE legacy_id = ?',
                (legacy_id,),
            ).fetchone()
            if existing:
                continue

            notes = journal.get('notes')
            if not notes or not str(notes).strip():
                continue

            user_id = journal.get('user', {}).get('id') if isinstance(journal.get('user'), dict) else None
            created_on = normalize_datetime(journal.get('created_on'))

            conn.execute('''
                INSERT INTO journals (
                    legacy_id,
                    issue_legacy_id,
                    user_legacy_id,
                    notes,
                    created_on,
                    raw_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?
                )
            ''', (
                legacy_id,
                issue_legacy_id,
                user_id,
                notes,
                created_on,
                json.dumps(journal, ensure_ascii=False),
            ))


def import_issue_attachments(conn, issue_legacy_id, issue_payload):
    """Store issue attachments entries from export into SQLite."""
    try:
        attrs = issue_payload.get('_decoded_attrs', {})
        attachments = attrs.get('attachments') or []
    except Exception as exc:
        logging.exception('Failed to parse attachments for issue %s: %s', issue_legacy_id, exc)
        attachments = []

    logging.debug('Issue %s: found %d attachments in export', issue_legacy_id, len(attachments))
    if not attachments:
        return

    with conn:
        for attachment in attachments:
            legacy_id = attachment.get('id')
            if not legacy_id:
                logging.debug('Skipping attachment with missing legacy id for issue %s', issue_legacy_id)
                continue

            existing = conn.execute(
                'SELECT id FROM issue_attachments WHERE legacy_id = ?',
                (legacy_id,),
            ).fetchone()
            if existing:
                logging.debug('Attachment legacy_id %s already imported for issue %s; skipping', legacy_id, issue_legacy_id)
                continue

            conn.execute('''
                INSERT INTO issue_attachments (
                    legacy_id,
                    issue_legacy_id,
                    filename,
                    content_type,
                    description,
                    filesize,
                    local_storage_path,
                    raw_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
            ''', (
                legacy_id,
                issue_legacy_id,
                attachment.get('filename'),
                attachment.get('content_type'),
                attachment.get('description'),
                attachment.get('filesize'),
                attachment.get('local_storage_path'),
                json.dumps(attachment, ensure_ascii=False),
            ))
            logging.debug('Imported attachment legacy_id %s for issue %s (filename=%s)', legacy_id, issue_legacy_id, attachment.get('filename'))


def _resource_name_candidates(payload):
    if isinstance(payload, dict):
        for key in ('name', 'title', 'subject', 'login', 'identifier'):
            if payload.get(key):
                return payload.get(key)
    return None


def extract_resource_fields(resource_name, resource_payload):
    """Extract standard fields for generic resource import."""
    obj = resource_payload.get('_decoded_attrs', resource_payload)
    legacy_id = obj.get('id') if isinstance(obj, dict) else None
    project = obj.get('project') if isinstance(obj, dict) else None

    name = _resource_name_candidates(obj) if isinstance(obj, dict) else None

    return {
        'legacy_id': legacy_id,
        'name': name,
        'project_id': project.get('id') if isinstance(project, dict) else None,
        'raw_json': json.dumps(resource_payload, ensure_ascii=False),
    }


def import_issues(data_dir, db_path, force=False):
    """Import issues from JSON files into SQLite database.

    Args:
        data_dir (str): Path to directory containing issue JSON files.
        db_path (str): Path to SQLite database file.
        force (bool): When True, update existing rows even if legacy_id exists.
    """
    conn = sqlite3.connect(db_path)
    create_schema(conn)

    issue_files = sorted(
        (name for name in os.listdir(data_dir) if name.endswith('.json')),
        key=lambda n: int(os.path.splitext(n)[0])
    )

    imported = 0
    skipped = 0
    total = len(issue_files)

    with conn:
        for idx, name in enumerate(issue_files, start=1):
            if idx % 250 == 0 or idx == total:
                logging.info('Importing issues: %d/%d', idx, total)

            source = os.path.join(data_dir, name)
            with open(source, 'r', encoding='utf-8') as f:
                payload = json.load(f)

            fields = extract_issue_fields(payload)
            fields['raw_json'] = json.dumps(payload, ensure_ascii=False)

            existing = conn.execute(
                'SELECT id FROM issues WHERE legacy_id = ?',
                (fields['legacy_id'],),
            ).fetchone()

            if existing and not force:
                skipped += 1
                continue

            if existing:
                conn.execute('''
                    UPDATE issues SET
                        project_id=:project_id,
                        tracker_id=:tracker_id,
                        status_id=:status_id,
                        author_id=:author_id,
                        assigned_to_id=:assigned_to_id,
                        priority_id=:priority_id,
                        category_id=:category_id,
                        fixed_version_id=:fixed_version_id,
                        parent_id=:parent_id,
                        subject=:subject,
                        description=:description,
                        start_date=:start_date,
                        due_date=:due_date,
                        created_on=:created_on,
                        updated_on=:updated_on,
                        closed_on=:closed_on,
                        done_ratio=:done_ratio,
                        is_private=:is_private,
                        estimated_hours=:estimated_hours,
                        spent_hours=:spent_hours,
                        total_spent_hours=:total_spent_hours,
                        raw_json=:raw_json
                    WHERE legacy_id=:legacy_id
                ''', fields)

                import_issue_journals(conn, fields['legacy_id'], payload)
                import_issue_attachments(conn, fields['legacy_id'], payload)

                imported += 1
                continue

            # Insert preserving legacy_id as primary key when possible.
            try:
                conn.execute('''
                    INSERT INTO issues (
                        id, legacy_id, project_id, tracker_id,
                        status_id, author_id, assigned_to_id, priority_id,
                        category_id, fixed_version_id, parent_id,
                        subject, description, start_date, due_date,
                        created_on, updated_on, closed_on,
                        done_ratio, is_private, estimated_hours,
                        spent_hours, total_spent_hours, raw_json
                    ) VALUES (
                        :legacy_id, :legacy_id, :project_id, :tracker_id,
                        :status_id, :author_id, :assigned_to_id, :priority_id,
                        :category_id, :fixed_version_id, :parent_id,
                        :subject, :description, :start_date, :due_date,
                        :created_on, :updated_on, :closed_on,
                        :done_ratio, :is_private, :estimated_hours,
                        :spent_hours, :total_spent_hours, :raw_json
                    )
                ''', fields)
            except sqlite3.IntegrityError:
                conn.execute('''
                    INSERT INTO issues (
                        legacy_id, project_id, tracker_id,
                        status_id, author_id, assigned_to_id, priority_id,
                        category_id, fixed_version_id, parent_id,
                        subject, description, start_date, due_date,
                        created_on, updated_on, closed_on,
                        done_ratio, is_private, estimated_hours,
                        spent_hours, total_spent_hours, raw_json
                    ) VALUES (
                        :legacy_id, :project_id, :tracker_id,
                        :status_id, :author_id, :assigned_to_id, :priority_id,
                        :category_id, :fixed_version_id, :parent_id,
                        :subject, :description, :start_date, :due_date,
                        :created_on, :updated_on, :closed_on,
                        :done_ratio, :is_private, :estimated_hours,
                        :spent_hours, :total_spent_hours, :raw_json
                    )
                ''', fields)

            import_issue_journals(conn, fields['legacy_id'], payload)
            import_issue_attachments(conn, fields['legacy_id'], payload)
            imported += 1

    logging.info(
        'Import finished; imported=%s, skipped=%s',
        imported,
        skipped,
    )
    conn.close()


RESOURCE_DIR_MAP = {
    'projects': 'project',
    'users': 'user',
    'trackers': 'tracker',
    'issue_statuses': 'issue_status',
    'enumerations': 'enumeration',
    'custom_fields': 'custom_field',
    'versions': 'version',
    'issue_categories': 'issue_category',
    'roles': 'role',
    'queries': 'query',
    'wiki_pages': 'wiki_page',
    'news': 'news',
    'documents': 'document',
    'time_entries': 'time_entry',
}


def import_generic_resources(data_dir, conn, resources):
    """Import resources from issue_export directories into generic tables."""
    for resource in resources:
        normalized = resource.rstrip('s') + 's' if not resource.endswith('s') else resource
        table = normalized
        resource_dir = RESOURCE_DIR_MAP.get(normalized, normalized)
        source_dir = os.path.join(data_dir, resource_dir)
        if not os.path.isdir(source_dir):
            logging.warning('Source resource dir missing, skipping: %s', source_dir)
            continue

        imported = 0
        skipped = 0
        with conn:
            for fname in sorted(os.listdir(source_dir), key=lambda n: int(os.path.splitext(n)[0])):
                if not fname.endswith('.json'):
                    continue

                with open(os.path.join(source_dir, fname), 'r', encoding='utf-8') as f:
                    payload = json.load(f)

                fields = extract_resource_fields(resource, payload)
                existing = conn.execute(
                    'SELECT id FROM {0} WHERE legacy_id = ?'.format(table),
                    (fields['legacy_id'],),
                ).fetchone()

                if existing:
                    conn.execute('''
                        UPDATE {0} SET
                            name=:name,
                            project_id=:project_id,
                            raw_json=:raw_json
                        WHERE legacy_id=:legacy_id
                    '''.format(table), fields)
                    skipped += 1
                    continue

                conn.execute('''
                    INSERT INTO {0} (legacy_id, name, project_id, raw_json)
                    VALUES (:legacy_id, :name, :project_id, :raw_json)
                '''.format(table), fields)
                imported += 1

        logging.info('Resource %s import complete; imported=%s, updated=%s', resource, imported, skipped)


def get_default_resources():
    return [
        'projects', 'users', 'trackers', 'issue_statuses', 'enumerations',
        'custom_fields', 'versions', 'issue_categories', 'roles', 'queries',
        'wiki_pages', 'news', 'documents', 'time_entries'
    ]


def create_id_mapping_table(conn):
    with conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS id_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource TEXT NOT NULL,
                legacy_id INTEGER NOT NULL,
                new_id INTEGER NOT NULL,
                UNIQUE(resource, legacy_id)
            );
        ''')


def set_id_mapping(conn, resource, legacy_id, new_id):
    with conn:
        conn.execute('''
            INSERT OR REPLACE INTO id_mapping (resource, legacy_id, new_id)
            VALUES (?, ?, ?)
        ''', (resource, legacy_id, new_id))


def get_id_mapping(conn, resource, legacy_id):
    row = conn.execute(
        'SELECT new_id FROM id_mapping WHERE resource=? AND legacy_id=?',
        (resource, legacy_id),
    ).fetchone()
    return row[0] if row else None


def normalized_resource_name_for_api(resource):
    mapping = {
        'projects': 'project',
        'users': 'user',
        'trackers': 'tracker',
        'issue_statuses': 'issue_status',
        'enumerations': 'enumeration',
        'custom_fields': 'custom_field',
        'versions': 'version',
        'issue_categories': 'issue_category',
        'roles': 'role',
        'queries': 'query',
        'wiki_pages': 'wiki_page',
        'news': 'news',
        'documents': 'document',
        'time_entries': 'time_entry',
        'issues': 'issue',
    }
    return mapping.get(resource, resource)


def get_user_name_by_legacy_id(conn, legacy_user_id):
    if not legacy_user_id:
        return None
    row = conn.execute('SELECT name FROM users WHERE legacy_id = ?', (legacy_user_id,)).fetchone()
    return row[0] if row and row[0] else None


def translate_issue_record(raw, conn):
    # Map foreign key IDs using id_mapping for 1st-tier resource migration.
    for fk in ('project_id', 'tracker_id', 'status_id', 'author_id', 'assigned_to_id', 'priority_id', 'category_id', 'fixed_version_id'):
        value = raw.get(fk)
        if value is not None:
            if fk == 'category_id':
                resource = 'issue_categories'
            elif fk == 'fixed_version_id':
                resource = 'versions'
            else:
                resource = fk.replace('_id', 's')
            mapped = get_id_mapping(conn, resource, value)
            # priorities may be under 'enumerations' in export
            if mapped is None and fk == 'priority_id':
                mapped = get_id_mapping(conn, 'enumerations', value)
            if mapped:
                raw[fk] = mapped
    # parent issue mapping is special because it refers to issues
    parent_value = raw.get('parent_id')
    if parent_value is not None:
        mapped_parent = get_id_mapping(conn, 'issues', parent_value)
        if mapped_parent:
            raw['parent_id'] = mapped_parent
    return raw


def fetch_remote_resource_json(redmine, resource_path, params=None, api_key=None):
    params = params or {}
    url = redmine.url.rstrip('/') + '/{0}.json'.format(resource_path)
    headers = {}
    if api_key:
        headers['X-Redmine-API-Key'] = api_key
    try:
        resp = requests.get(url, params=params, headers=headers, verify=False, timeout=60)
        text = resp.text
        try:
            data = resp.json()
        except Exception as e:
            logging.warning('Could not parse JSON for %s: %s', url, e)
            data = {'raw': text}
        logging.debug('Remote raw fetch %s status=%s params=%s json_keys=%s', resource_path, resp.status_code, params, list(data.keys()) if isinstance(data, dict) else None)
        logging.debug('Remote raw payload: %s', data)
        return data
    except Exception as exc:
        logging.warning('Could not request %s %s: %s', url, params, exc)
        return None


PLURAL_RESOURCE_MAP = {
    'project': 'projects',
    'user': 'users',
    'tracker': 'trackers',
    'issue_status': 'issue_statuses',
    'enumeration': 'enumerations',
    'custom_field': 'custom_fields',
    'version': 'versions',
    'issue_category': 'issue_categories',
    'role': 'roles',
    'query': 'queries',
    'wiki_page': 'wiki_pages',
    'news': 'news',
    'document': 'documents',
    'time_entry': 'time_entries',
    'issue': 'issues',
}


def get_resource_path(resource):
    if resource.endswith('s'):
        return resource
    return PLURAL_RESOURCE_MAP.get(resource, resource + 's')


def get_db_connection(host, port, user, password, db):
    if pymysql is None:
        raise RuntimeError('pymysql is required for DB mode but is not installed')
    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=db,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.Cursor,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute('SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED')
    except Exception:
        pass
    return conn


def get_existing_remote_ids_db(db_conn, resource):
    table = get_resource_path(resource)
    ids = set()
    try:
        # If connection is not autocommit, refresh to see external changes.
        try:
            db_conn.commit()
        except Exception:
            pass
        with db_conn.cursor() as cur:
            cur.execute('SELECT id FROM `{0}` ORDER BY id ASC'.format(table))
            for row in cur.fetchall():
                if row and row[0] is not None:
                    ids.add(int(row[0]))
            cur.execute('SELECT COUNT(*) FROM `{0}`'.format(table))
            total_count = int(cur.fetchone()[0] or 0)
    except Exception as exc:
        logging.warning('Could not query DB for %s ids: %s', table, exc)
        return set(), None

    logging.info('DB existing ids for %s count=%s total_count=%s', table, len(ids), total_count)
    if ids:
        logging.debug('DB existing ids for %s min=%s max=%s sample=%s', table, min(ids), max(ids), sorted(list(ids))[:20])
    return ids, total_count


def get_current_max_remote_id_db(db_conn, resource):
    table = get_resource_path(resource)
    try:
        with db_conn.cursor() as cur:
            cur.execute('SELECT MAX(id) FROM `{0}`'.format(table))
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception as exc:
        logging.warning('Could not query DB for %s max id: %s', table, exc)
        return 0


def get_current_max_remote_id(redmine, resource, api_key=None):
    resource_path = get_resource_path(resource)
    try:
        data = fetch_remote_resource_json(redmine, resource_path, {'limit': 1, 'sort': 'id:desc'}, api_key=api_key)
        items = data.get(resource_path, []) if isinstance(data, dict) else []
        current_max = items[0].get('id') if items else 0
        total_count = data.get('total_count') if isinstance(data, dict) else None
        logging.debug('Remote current max for %s is %s (total_count=%s)', resource, current_max, total_count)
        return current_max
    except Exception as exc:
        logging.warning('Could not get max remote id for %s: %s', resource, exc)
        return 0


def get_existing_remote_ids(redmine, resource, api_key=None):
    resource_path = get_resource_path(resource)
    ids = set()
    offset = 0
    page_size = 100
    total_count = None

    previous_page_ids = None
    iteration = 0
    max_iterations = 1000

    while True:
        iteration += 1
        if iteration > max_iterations:
            logging.warning('Reached max iterations (%s) for %s; aborting fetch', max_iterations, resource_path)
            break

        data = fetch_remote_resource_json(redmine, resource_path, {
            'limit': page_size,
            'offset': offset,
            'sort': 'id:asc'
        }, api_key=api_key)

        if not isinstance(data, dict):
            logging.warning('Could not fetch %s data for existing IDs (resource=%s)', resource_path, resource)
            break

        items = data.get(resource_path, [])
        if not isinstance(items, list):
            logging.warning('Unexpected payload for %s: %s', resource_path, data)
            break

        page_ids = {item['id'] for item in items if isinstance(item, dict) and 'id' in item}
        for item_id in page_ids:
            ids.add(item_id)

        if total_count is None:
            total_count = data.get('total_count')

        logging.debug('Fetched %d/%s items for %s (offset=%s page_size=%s)', len(ids), total_count, resource_path, offset, page_size)

        if page_ids == previous_page_ids:
            logging.warning('Page content repeated for %s offset=%s; stopping to avoid infinite loop', resource_path, offset)
            break

        previous_page_ids = page_ids

        if total_count is not None and len(ids) >= total_count:
            break

        if not items or len(items) < page_size:
            break

        offset += page_size

    logging.info('Fetched existing ids for %s; count=%s total_count=%s', resource, len(ids), total_count)
    if total_count is not None and total_count > len(ids):
        logging.warning('Partial fetch for %s; %s/%s ids loaded', resource, len(ids), total_count)

    if ids:
        logging.debug('Remote existing ids for %s count=%s min=%s max=%s', resource, len(ids), min(ids), max(ids))
    else:
        logging.debug('Remote existing ids for %s count=0', resource)

    return ids, total_count


def get_default_project_id(redmine):
    try:
        project = redmine.project.all(limit=1).first()
        return project.id if project else None
    except Exception:
        return None


def normalize_name(value):
    if not value:
        return None
    return str(value).strip().lower()


def find_resource_id_by_name(redmine, resource_name, candidate_name):
    if not candidate_name:
        return None

    candidate_norm = normalize_name(candidate_name)
    if not candidate_norm:
        return None

    try:
        collection = getattr(redmine, resource_name).all(limit=500)
        for item in collection:
            item_name = getattr(item, 'name', None) or getattr(item, 'title', None) or ''
            if normalize_name(item_name) == candidate_norm:
                return item.id
    except Exception:
        pass
    return None


def get_redmine_user_id_by_name_or_login(redmine, candidate_name):
    if not candidate_name:
        return None

    candidate_norm = normalize_name(candidate_name)
    if not candidate_norm:
        return None

    try:
        users = redmine.user.all(limit=1000)
        for user in users:
            if normalize_name(getattr(user, 'name', '')) == candidate_norm:
                return user.id
            if normalize_name(getattr(user, 'login', '')) == candidate_norm:
                return user.id
    except Exception:
        pass
    return None


def find_project_resource_id_by_name(redmine, resource_name, project_id, candidate_name):
    if not candidate_name:
        return None

    candidate_norm = normalize_name(candidate_name)
    if not candidate_norm:
        return None

    try:
        if resource_name == 'issue_category' and project_id:
            collection = redmine.issue_category.all(project_id=project_id, limit=500)
        elif resource_name == 'version' and project_id:
            collection = redmine.version.all(project_id=project_id, limit=500)
        elif resource_name == 'user':
            return get_redmine_user_id_by_name_or_login(redmine, candidate_name)
        else:
            return find_resource_id_by_name(redmine, resource_name, candidate_name)

        for item in collection:
            item_name = getattr(item, 'name', None) or getattr(item, 'title', None) or ''
            if normalize_name(item_name) == candidate_norm:
                return item.id
    except Exception:
        pass

    if resource_name == 'user':
        return get_redmine_user_id_by_name_or_login(redmine, candidate_name)

    return find_resource_id_by_name(redmine, resource_name, candidate_name)


def ensure_user_project_membership(redmine, user_id, project_id):
    try:
        # find a role id to assign; default to first available role
        roles = redmine.role.all(limit=1)
        role_ids = [roles[0].id] if roles else [1]
        # create membership; if exists, this may return an error but we can ignore
        try:
            redmine.project_membership.create(project_id=project_id, user_id=user_id, role_ids=role_ids)
            logging.info('Added user %s to project %s membership', user_id, project_id)
        except Exception as exc:
            logging.debug('Could not create membership for user %s in project %s: %s', user_id, project_id, exc)
        return True
    except Exception as exc:
        logging.debug('Membership check failed for user %s project %s: %s', user_id, project_id, exc)
        return False


def create_dummy_resource(redmine, resource, dummy_id, keep=False):
    logging.debug('create_dummy_resource: resource=%s, dummy_id=%s, keep=%s', resource, dummy_id, keep)
    try:
        if resource == 'project':
            dummy = redmine.project.create(
                name='__dummy_proj_{0}__'.format(dummy_id),
                identifier='dummy-proj-{0}'.format(dummy_id)
            )
            created_id = dummy.id
            logging.debug('Created dummy project id=%s', created_id)
            if not keep:
                redmine.project.delete(created_id)
            return created_id

        elif resource == 'user':
            # avoid collision when repeated attempts use identical login/email
            # a timestamp microtag ensures we do not retry same login on failure
            unique_tag = int(datetime.now(timezone.utc).timestamp() * 1000)
            dummy_login = '__dummy_user_{0}_{1}__'.format(dummy_id, unique_tag)
            dummy_mail = 'dummy{0}_{1}@example.com'.format(dummy_id, unique_tag)

            dummy = redmine.user.create(
                login=dummy_login,
                firstname='Dummy',
                lastname=str(dummy_id),
                mail=dummy_mail,
                password='ChangeMe123!'
            )
            created_id = dummy.id
            logging.debug('Created dummy user id=%s (tag=%s)', created_id, unique_tag)
            if not keep:
                redmine.user.delete(created_id)
            return created_id


        elif resource == 'tracker':
            dummy = redmine.tracker.create(name='__dummy_tracker_{0}__'.format(dummy_id))
            created_id = dummy.id
            logging.debug('Created dummy tracker id=%s', created_id)
            if not keep:
                redmine.tracker.delete(created_id)
            return created_id

        elif resource == 'issue_status':
            dummy = redmine.issue_status.create(name='__dummy_status_{0}__'.format(dummy_id))
            created_id = dummy.id
            logging.debug('Created dummy issue_status id=%s', created_id)
            if not keep:
                redmine.issue_status.delete(created_id)
            return created_id

        elif resource == 'role':
            dummy = redmine.role.create(name='__dummy_role_{0}__'.format(dummy_id))
            created_id = dummy.id
            logging.debug('Created dummy role id=%s', created_id)
            if not keep:
                redmine.role.delete(created_id)
            return created_id

        elif resource == 'query':
            dummy = redmine.query.create(name='__dummy_query_{0}__'.format(dummy_id), is_public=False)
            created_id = dummy.id
            logging.debug('Created dummy query id=%s', created_id)
            if not keep:
                redmine.query.delete(created_id)
            return created_id

        elif resource == 'issue':
            project_id = get_default_project_id(redmine)
            if not project_id:
                logging.warning('No project found via API; using fallback project id=11 for dummy issue')
                project_id = 11

            # Validate fallback project exists (best-effort). If not, create one.
            try:
                if not redmine.project.get(project_id):
                    raise Exception('fallback project missing')
            except Exception:
                logging.warning('Fallback project id=11 not found; creating temporary project __dummy_proj_for_issue__')
                proj = redmine.project.create(name='__dummy_proj_for_issue__', identifier='__dummy_proj_for_issue_{0}__'.format(int(datetime.now(timezone.utc).timestamp())))
                project_id = proj.id

            tracker_objs = redmine.tracker.all(limit=1)
            status_objs = redmine.issue_status.all(limit=1)
            tracker_obj = tracker_objs[0] if tracker_objs else None
            status_obj = status_objs[0] if status_objs else None
            tracker_id = tracker_obj.id if tracker_obj else None
            status_id = status_obj.id if status_obj else None

            if not tracker_id or not status_id:
                raise RuntimeError('No tracker/status for dummy issue')

            dummy = redmine.issue.create(
                project_id=project_id,
                tracker_id=tracker_id,
                status_id=status_id,
                subject='__dummy_issue_{0}__'.format(dummy_id),
                description='dummy'
            )
            created_id = dummy.id
            logging.debug('Created dummy issue id=%s', created_id)
            if not keep:
                redmine.issue.delete(created_id)
            return created_id

        else:
            return None
    except Exception as exc:
        logging.warning('Could not create/delete dummy %s %s: %s %s', resource, dummy_id, type(exc).__name__, exc)
        return None


def ensure_target_id_sequence(redmine, resource, target_legacy, api_key=None, db_conn=None):
    if db_conn is not None:
        existing_ids, total_count = get_existing_remote_ids_db(db_conn, resource)
        existing_max = max(existing_ids) if existing_ids else 0
        current_max = existing_max
    else:
        existing_ids, total_count = get_existing_remote_ids(redmine, resource, api_key=api_key)
        existing_max = max(existing_ids) if existing_ids else 0
        current_max = get_current_max_remote_id(redmine, resource, api_key=api_key)

    if total_count is not None and len(existing_ids) < total_count:
        logging.info('Detected partial existing ids list for %s: fetched=%s total=%s; will continue with current_max=%s',
                     resource, len(existing_ids), total_count, current_max)

    cached_max = target_max_cache.get(resource, 0)
    current_max = max(current_max, existing_max, cached_max)

    logging.info('ensure_target_id_sequence %s target=%s current_max=%s', resource, target_legacy, current_max)
    logging.debug('Existing %s ids count=%s sample=%s', resource, len(existing_ids), sorted(list(existing_ids))[:20])

    if target_legacy in existing_ids:
        logging.info('Target %s already exists for %s; no stubs required', target_legacy, resource)
        return True

    if db_conn is not None:
        # DB mode: allow inserting missing IDs below max, and reserve creation for above max.
        if target_legacy < existing_max:
            logging.info('Target %s is a gap under existing_max %s for %s; will insert directly',
                         target_legacy, existing_max, resource)
            return True

        if target_legacy == existing_max + 1:
            logging.info('Target %s is next sequential id for %s; will create live record', target_legacy, resource)
            return True

        # target_legacy > existing_max + 1 → create fillers from existing_max+1 … target_legacy-1
        logging.info('Target %s is above existing_max %s for %s; stubbing %s..%s',
                     target_legacy, existing_max, resource, existing_max + 1, target_legacy - 1)
        current_max = existing_max
        target_goal = target_legacy - 1

        while current_max < target_goal:
            next_id_fill = current_max + 1
            if next_id_fill in existing_ids:
                logging.debug('Existing gap fill %s already exists for %s; advancing', next_id_fill, resource)
                current_max = next_id_fill
                continue

            logging.info('Creating dummy stub %s for %s to approach target %s (current_max=%s)',
                         next_id_fill, resource, target_legacy, current_max)
            created_id = create_dummy_resource(redmine, resource, next_id_fill, keep=False)
            if created_id is None:
                logging.warning('Failed to create dummy stub %s for %s; aborting sequence', next_id_fill, resource)
                return False

            if created_id <= current_max:
                logging.warning('Created id %s for %s did not advance (current_max=%s); aborting',
                                created_id, resource, current_max)
                return False

            target_max_cache[resource] = max(target_max_cache.get(resource, 0), created_id)
            existing_ids.add(created_id)
            current_max = created_id

        logging.info('Stub filling complete for %s target=%s; current_max=%s', resource, target_legacy, current_max)
        return True

    # API mode preserves strict >current_max
    if target_legacy <= current_max:
        logging.warning('Target %s for %s is <= current max %s; cannot preserve exact ID', target_legacy, resource, current_max)
        return False

    target_goal = target_legacy - 1

    while current_max < target_goal:
        next_id_fill = current_max + 1

        if next_id_fill in existing_ids:
            logging.debug('Skipping candidate %s for %s as it already exists', next_id_fill, resource)
            current_max = next_id_fill
            continue

        logging.info('Creating dummy stub for %s to approach target %s (current_max=%s next=%s)', resource, target_legacy, current_max, next_id_fill)
        created_id = create_dummy_resource(redmine, resource, next_id_fill, keep=False)
        if created_id is None:
            logging.warning('Failed to create dummy stub for %s at target %s; aborting sequence', resource, next_id_fill)
            return False

        if created_id <= current_max:
            logging.warning('Created id %s for %s did not advance from current_max %s; aborting', created_id, resource, current_max)
            return False

        logging.debug('Created dummy %s id=%s, deleting placeholder auto-next', resource, created_id)
        target_max_cache[resource] = max(target_max_cache.get(resource, 0), created_id)
        existing_ids.add(created_id)
        current_max = created_id

    logging.info('Stub filling complete for %s target=%s; now current_max=%s', resource, target_legacy, current_max)
    return True


def sanitize_note_text(text):
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    # Keep printable text and whitespace, replace control chars with spaces.
    return ''.join(ch if ch.isprintable() or ch in '\n\r\t' else ' ' for ch in text)


def push_issue_journals(redmine, conn, issue_legacy_id, created_issue_id):
    cur = conn.execute(
        'SELECT * FROM journals WHERE issue_legacy_id=? ORDER BY created_on ASC',
        (issue_legacy_id,),
    )
    for journal_row in cur.fetchall():
        notes = journal_row[4] if len(journal_row) > 4 else None
        journal_created = journal_row[5] if len(journal_row) > 5 else None
        journal_user_id = journal_row[3] if len(journal_row) > 3 else None
        if not notes or not str(notes).strip():
            continue

        # Preserve source author/timestamp context in imported log note.
        prefix_parts = []
        if journal_user_id:
            journal_author_name = get_user_name_by_legacy_id(conn, journal_user_id)
            if journal_author_name:
                prefix_parts.append('Originally by {0}'.format(journal_author_name))
            else:
                prefix_parts.append('Originally by user_id {0}'.format(journal_user_id))
        if journal_created:
            prefix_parts.append('on {0}'.format(journal_created))
        if prefix_parts:
            notes_text = '{0}: {1}'.format(' - '.join(prefix_parts), notes)
        else:
            notes_text = notes

        notes_text = sanitize_note_text(notes_text)

        try:
            redmine.issue.update(created_issue_id, notes=notes_text)
        except Exception as exc:
            logging.warning('Journal update failed for issue %s legacy %s; retrying without prefix', created_issue_id, issue_legacy_id)
            logging.debug('Failed notes payload: %r', notes_text)
            try:
                redmine.issue.update(created_issue_id, notes=sanitize_note_text(notes))
                continue
            except Exception:
                logging.exception('Failed to push journal for issue %s (legacy %s) after retry: %s', created_issue_id, issue_legacy_id, exc)


def build_attachment_size_pool(remote_issue):
    pool = {}
    for attachment in getattr(remote_issue, 'attachments', []):
        filename = getattr(attachment, 'filename', None)
        if not filename:
            continue
        pool.setdefault(filename, []).append(getattr(attachment, 'filesize', None))
    return pool


def consume_attachment_match(pool, filename, filesize):
    available_sizes = pool.get(filename, [])
    if not available_sizes:
        return False

    if filesize is not None:
        for idx, value in enumerate(available_sizes):
            if value == filesize:
                available_sizes.pop(idx)
                return True

    available_sizes.pop(0)
    return True


def push_issue_attachments(redmine, conn, issue_legacy_id, created_issue_id, api_key=None, attachment_root=None):
    """Upload attachments for a created issue from SQLite migration cache."""
    logging.debug('push_issue_attachments issue_legacy_id=%s created_issue_id=%s', issue_legacy_id, created_issue_id)

    # Skip if no attachments are in export for this issue.
    cur = conn.execute(
        'SELECT legacy_id, filename, content_type, description, filesize, local_storage_path FROM issue_attachments WHERE issue_legacy_id=? ORDER BY legacy_id ASC',
        (issue_legacy_id,),
    )
    attachments = cur.fetchall()
    logging.debug('Found %d cached attachments for issue %s', len(attachments), issue_legacy_id)
    if not attachments:
        return

    # Track existing attachment counts by filename and size so duplicate names
    # can still be restored when the issue legitimately contains repeats.
    try:
        remote_issue = redmine.issue.get(created_issue_id, include='attachments')
        existing_attachment_pool = build_attachment_size_pool(remote_issue)
        logging.debug('Target issue %s has %d existing attachment names', created_issue_id, len(existing_attachment_pool))
    except Exception as exc:
        existing_attachment_pool = {}
        logging.debug('Could not fetch existing attachments for issue %s: %s', created_issue_id, exc)

    for legacy_id, filename, content_type, description, filesize, local_storage_path in attachments:
        if consume_attachment_match(existing_attachment_pool, filename, filesize):
            logging.info('Attachment %s already exists on target issue %s; skipping', filename, created_issue_id)
            continue

        if not local_storage_path:
            logging.warning('No local_storage_path for attachment legacy %s; skipping', legacy_id)
            continue

        if not os.path.isfile(local_storage_path) and attachment_root:
            candidate = os.path.join(attachment_root, local_storage_path)
            if os.path.isfile(candidate):
                logging.debug(
                    'Resolved missing attachment relative path for legacy %s to %s',
                    legacy_id,
                    candidate,
                )
                local_storage_path = candidate
            else:
                candidate = os.path.join(attachment_root, str(legacy_id), filename)
                if os.path.isfile(candidate):
                    logging.debug(
                        'Resolved missing attachment path for legacy %s to %s',
                        legacy_id,
                        candidate,
                    )
                    local_storage_path = candidate
                else:
                    candidate = os.path.join(attachment_root, filename)
                    if os.path.isfile(candidate):
                        logging.debug(
                            'Resolved missing attachment path for legacy %s to %s',
                            legacy_id,
                            candidate,
                        )
                        local_storage_path = candidate

        if not os.path.isfile(local_storage_path):
            logging.warning('Attachment file missing on disk, skipping: %s', local_storage_path)
            continue

        upload_token = None

        # First try redminelib native upload API if available.
        try:
            if hasattr(redmine, 'upload') and callable(redmine.upload):
                try:
                    upload_obj = redmine.upload(
                        path=local_storage_path,
                        filename=filename,
                        content_type=content_type or 'application/octet-stream',
                    )
                except TypeError:
                    # Some redminelib versions use a different signature.
                    try:
                        with open(local_storage_path, 'rb') as f:
                            upload_obj = redmine.upload(
                                f,
                                filename=filename,
                                content_type=content_type or 'application/octet-stream',
                            )
                    except TypeError:
                        upload_obj = None

                if upload_obj is not None:
                    if hasattr(upload_obj, 'token'):
                        upload_token = upload_obj.token
                    elif isinstance(upload_obj, dict):
                        upload_token = upload_obj.get('token') or (upload_obj.get('upload', {}) or {}).get('token')
                    elif isinstance(upload_obj, str):
                        upload_token = upload_obj

                    if upload_token:
                        logging.debug('Created upload token via redmine.upload for file %s', filename)
                    else:
                        logging.debug('redmine.upload did not return token for %s', filename)
            else:
                logging.debug('redmine.upload is not callable; falling back to direct HTTP upload for %s', filename)
        except Exception as exc:
            logging.warning('Could not create upload using redmine.upload: %s; fallback to direct HTTP upload', exc)

        # Fallback: direct Redmine REST API upload endpoint.
        if upload_token is None:
            try:
                content_type_value = content_type or 'application/octet-stream'
                url = redmine.url.rstrip('/') + '/uploads.json'
                headers = {
                    'Content-Type': content_type_value,
                }
                if api_key:
                    headers['X-Redmine-API-Key'] = api_key
                elif hasattr(redmine, 'key') and redmine.key:
                    headers['X-Redmine-API-Key'] = redmine.key

                with open(local_storage_path, 'rb') as f:
                    data = f.read()

                resp = requests.post(url, headers=headers, data=data, verify=False, timeout=120)
                if resp.status_code in (200, 201):
                    upload_json = resp.json()
                    upload_token = upload_json.get('upload', {}).get('token')
                    logging.debug('Created upload token via HTTP for file %s (status=%s)', filename, resp.status_code)
                else:
                    logging.warning('Upload HTTP request failed for %s: %s %s', filename, resp.status_code, resp.text[:200])
                    if resp.status_code == 406 and content_type_value != 'application/octet-stream':
                        logging.debug('Retrying upload token request for %s with application/octet-stream', filename)
                        headers['Content-Type'] = 'application/octet-stream'
                        resp = requests.post(url, headers=headers, data=data, verify=False, timeout=120)
                        if resp.status_code in (200, 201):
                            upload_json = resp.json()
                            upload_token = upload_json.get('upload', {}).get('token')
                            logging.debug('Created upload token via HTTP for file %s on fallback (status=%s)', filename, resp.status_code)
                        else:
                            logging.warning('Fallback upload HTTP request failed for %s: %s %s', filename, resp.status_code, resp.text[:200])
            except Exception as exc:
                logging.exception('Failed HTTP upload token request for attachment %s: %s', filename, exc)

        if not upload_token:
            logging.error('No upload token available for attachment %s; skipping', filename)
            continue

        try:
            update_payload = {
                'uploads': [
                    {
                        'token': upload_token,
                        'filename': filename,
                        'content_type': content_type or 'application/octet-stream',
                        'description': description or '',
                    }
                ]
            }
            redmine.issue.update(created_issue_id, **update_payload)
            existing_attachment_pool.setdefault(filename, []).append(filesize)
            logging.info('Uploaded attachment %s to issue %s (legacy %s)', filename, created_issue_id, issue_legacy_id)
        except Exception as exc:
            logging.exception('Failed to attach %s to issue %s: %s', filename, created_issue_id, exc)


def create_resource_payload(resource, row):
    # Use minimal fields from raw_json for import into target Redmine.
    data = {}
    if row.get('raw_json'):
        try:
            obj = json.loads(row['raw_json'])
            attrs = obj.get('_decoded_attrs', obj)
        except Exception:
            attrs = {}
    else:
        attrs = {}

    if resource == 'project':
        data['name'] = attrs.get('name') or row.get('name')
        identifier = attrs.get('identifier') or data['name']
        data['identifier'] = normalize_identifier(identifier)
        data['description'] = attrs.get('description')
        data['is_public'] = attrs.get('is_public', True)
    elif resource == 'user':
        login = attrs.get('login') or row.get('name') or 'user_{0}'.format(row.get('legacy_id'))
        data['login'] = normalize_identifier(login) or 'user_{0}'.format(row.get('legacy_id'))
        data['firstname'] = attrs.get('firstname') or 'Auto'
        data['lastname'] = attrs.get('lastname') or 'User{0}'.format(row.get('legacy_id'))
        data['mail'] = attrs.get('mail') or 'user{0}@example.com'.format(row.get('legacy_id'))
        data['password'] = 'ChangeMe123!'
    elif resource == 'tracker':
        data['name'] = attrs.get('name') or row.get('name')
    elif resource == 'issue_status':
        data['name'] = attrs.get('name') or row.get('name')
        if attrs.get('is_closed') is not None:
            data['is_closed'] = attrs.get('is_closed')
        if attrs.get('is_default') is not None:
            data['is_default'] = attrs.get('is_default')
    elif resource == 'role':
        data['name'] = attrs.get('name') or row.get('name')
    elif resource == 'query':
        data['name'] = attrs.get('name') or row.get('name')
        if attrs.get('is_public') is not None:
            data['is_public'] = attrs.get('is_public')
    elif resource == 'time_entry':
        # time entries may be created via API; skip in initial version
        return None
    else:
        data['name'] = attrs.get('name') or row.get('name')

    # Remove None values for cleaner API payloads
    return {k: v for k, v in data.items() if v is not None}


def push_cube_to_redmine(redmine, conn, interactive=False, api_key=None, db_conn=None, attachment_root=None):
    """Push records from SQLite into Redmine instance, preserving mappings."""
    # Order matters: projects -> users -> trackers/statuses -> roles -> others -> issues
    queue = [
        'projects', 'users', 'trackers', 'issue_statuses',
        'roles', 'queries', 'wiki_pages', 'news',
        'documents', 'time_entries', 'issues'
    ]

    for resource in queue:
        api_name = normalized_resource_name_for_api(resource)

        # Do not pause on project insertion; it has no immediate PK feedback in the UI.
        if resource != 'projects':
            maybe_pause('About to process resource {0}'.format(resource), interactive)

        # Skip non-write-ready resources until later
        if resource in ('time_entries', 'documents', 'wiki_pages'):
            logging.info('Skipping resource on push: %s', resource)
            continue

        if resource == 'issues':
            # issue creation depends on parent mapping complete
            if db_conn is not None:
                existing_remote_ids, _ = get_existing_remote_ids_db(db_conn, 'issue')
            else:
                existing_remote_ids, _ = get_existing_remote_ids(redmine, 'issue', api_key=api_key)
            cur = conn.execute('SELECT * FROM issues ORDER BY legacy_id ASC')
            cols = [d[0] for d in cur.description]
            issue_count = 0
            for raw_row in cur.fetchall():
                issue_count += 1
                if issue_count % 50 == 0:
                    logging.info('Pushing issues: %d processed in current batch', issue_count)

                row = dict(zip(cols, raw_row))
                if get_id_mapping(conn, 'issues', row['legacy_id']):
                    continue

                if row['legacy_id'] in existing_remote_ids:
                    logging.info(
                        'Skipping creation for issue legacy %s: target already exists (id=%s)',
                        row['legacy_id'],
                        row['legacy_id'],
                    )
                    set_id_mapping(conn, 'issues', row['legacy_id'], row['legacy_id'])
                    continue

                maybe_pause('About to push issue legacy {0}'.format(row['legacy_id']), interactive)
                if not ensure_target_id_sequence(redmine, 'issue', row['legacy_id'], api_key=api_key, db_conn=db_conn):
                    logging.warning('Skipping issue legacy %s because ID sequence cannot be preserved.', row['legacy_id'])
                    continue

                issue_data = {
                    'subject': row['subject'] or 'Legacy-{0}'.format(row['legacy_id']),
                    'description': row['description'] or '',
                }

                for fld in ('start_date', 'due_date', 'done_ratio', 'estimated_hours', 'spent_hours', 'priority_id', 'category_id', 'fixed_version_id', 'parent_id', 'created_on', 'updated_on', 'closed_on'):
                    if row.get(fld) is not None:
                        issue_data[fld] = row[fld]

                if row.get('is_private') is not None:
                    issue_data['is_private'] = bool(row['is_private'])

                # Ensure associations are mapped to destination IDs
                mapped = translate_issue_record({
                    'project_id': row['project_id'],
                    'tracker_id': row['tracker_id'],
                    'status_id': row['status_id'],
                    'author_id': row['author_id'],
                    'assigned_to_id': row['assigned_to_id'],
                    'priority_id': row.get('priority_id'),
                    'category_id': row.get('category_id'),
                    'fixed_version_id': row.get('fixed_version_id'),
                }, conn)
                for fk in (
                    'project_id', 'tracker_id', 'status_id', 'author_id',
                    'assigned_to_id', 'priority_id', 'category_id',
                    'fixed_version_id'
                ):
                    if mapped.get(fk) is not None:
                        issue_data[fk] = mapped[fk]

                # Keep intended status for post-create enforcement when workflows may override create defaults.
                desired_status_id = issue_data.get('status_id')

                # Validate project exists on target before create to avoid /projects/<id>/issues 404
                project_target_id = issue_data.get('project_id')
                if project_target_id is None:
                    logging.warning('Skipping issue %s because project_id is missing after mapping', row['legacy_id'])
                    continue

                project_ok = False
                if db_conn is not None:
                    try:
                        with db_conn.cursor() as cur:
                            cur.execute('SELECT 1 FROM `projects` WHERE id = %s', (project_target_id,))
                            project_ok = cur.fetchone() is not None
                    except Exception as exc:
                        logging.debug('Could not verify project %s in DB source: %s', project_target_id, exc)
                else:
                    try:
                        redmine.project.get(project_target_id)
                        project_ok = True
                    except Exception as exc:
                        logging.debug('Could not fetch project %s in API source: %s', project_target_id, exc)

                if not project_ok:
                    logging.warning('Skipping issue legacy %s because target project %s does not exist', row['legacy_id'], project_target_id)
                    continue

                # Propagate custom fields when available (id/value pairs)
                try:
                    raw_obj = json.loads(row.get('raw_json', '{}') or '{}')
                    decoded = raw_obj.get('_decoded_attrs', raw_obj)
                    custom_fields = decoded.get('custom_fields') or []
                    if isinstance(custom_fields, list) and custom_fields:
                        cf_payload = []
                        for cf in custom_fields:
                            if isinstance(cf, dict) and cf.get('id') is not None:
                                value = cf.get('value')
                                cf_payload.append({'id': cf['id'], 'value': value})
                        if cf_payload:
                            issue_data['custom_fields'] = cf_payload

                    # Try best-effort mapping by source names when id mapping failed.
                    if issue_data.get('status_id') is None:
                        status_name = decoded.get('status', {}).get('name')
                        mapped_status = find_resource_id_by_name(redmine, 'issue_status', status_name)
                        if mapped_status:
                            issue_data['status_id'] = mapped_status
                            desired_status_id = mapped_status

                    category_name = decoded.get('category', {}).get('name')
                    if issue_data.get('category_id') is None:
                        mapped_category = find_project_resource_id_by_name(
                            redmine,
                            'issue_category',
                            project_target_id,
                            category_name,
                        )
                        if mapped_category:
                            issue_data['category_id'] = mapped_category
                    elif issue_data.get('category_id') is not None:
                        mapped_category = find_project_resource_id_by_name(
                            redmine,
                            'issue_category',
                            project_target_id,
                            category_name,
                        )
                        if mapped_category:
                            issue_data['category_id'] = mapped_category
                        else:
                            issue_data.pop('category_id', None)

                    version_name = decoded.get('fixed_version', {}).get('name')
                    if issue_data.get('fixed_version_id') is None:
                        mapped_version = find_project_resource_id_by_name(
                            redmine,
                            'version',
                            project_target_id,
                            version_name,
                        )
                        if mapped_version:
                            issue_data['fixed_version_id'] = mapped_version
                    elif issue_data.get('fixed_version_id') is not None:
                        mapped_version = find_project_resource_id_by_name(
                            redmine,
                            'version',
                            project_target_id,
                            version_name,
                        )
                        if mapped_version:
                            issue_data['fixed_version_id'] = mapped_version
                        else:
                            issue_data.pop('fixed_version_id', None)

                    assigned_to_name = decoded.get('assigned_to', {}).get('name')
                    if assigned_to_name:
                        mapped_assignee = get_redmine_user_id_by_name_or_login(redmine, assigned_to_name)
                        if mapped_assignee:
                            issue_data['assigned_to_id'] = mapped_assignee

                    # Drop invalid project-scoped links if mapping could not be found
                    if issue_data.get('category_id') is not None and not isinstance(issue_data['category_id'], int):
                        issue_data.pop('category_id', None)
                    if issue_data.get('fixed_version_id') is not None and not isinstance(issue_data['fixed_version_id'], int):
                        issue_data.pop('fixed_version_id', None)
                    if issue_data.get('assigned_to_id') is not None and not isinstance(issue_data['assigned_to_id'], int):
                        issue_data.pop('assigned_to_id', None)
                except Exception:
                    pass

                logging.debug('Pushing issue legacy %s: payload project=%s tracker=%s status=%s author=%s assigned_to=%s start_date=%s due_date=%s done_ratio=%s',
                              row['legacy_id'], issue_data.get('project_id'), issue_data.get('tracker_id'), issue_data.get('status_id'), issue_data.get('author_id'), issue_data.get('assigned_to_id'), issue_data.get('start_date'), issue_data.get('due_date'), issue_data.get('done_ratio'))

                if issue_data.get('assigned_to_id') and issue_data.get('project_id'):
                    ensure_user_project_membership(redmine, issue_data['assigned_to_id'], issue_data['project_id'])

                try:
                    created = redmine.issue.create(**issue_data)
                except Exception as exc:
                    message = str(exc)
                    if 'Assignee is invalid' in message and issue_data.get('assigned_to_id'):
                        logging.warning('Assignee invalid for issue %s; retrying without assignee', row['legacy_id'])
                        issue_data.pop('assigned_to_id', None)
                        try:
                            created = redmine.issue.create(**issue_data)
                        except Exception as exc2:
                            logging.exception('Failed to push issue %s after removing assignee: %s', row['legacy_id'], exc2)
                            continue
                    else:
                        logging.exception('Failed to push issue %s: %s', row['legacy_id'], exc)
                        continue

                logging.info('Created issue %s -> %s', row['legacy_id'], created.id)
                set_id_mapping(conn, 'issues', row['legacy_id'], created.id)

                # Enforce status if desired status is known and API may choose defaults.
                if desired_status_id is not None:
                    try:
                        current_status_id = None
                        if hasattr(created, 'status') and getattr(created, 'status') is not None:
                            current_status_id = getattr(created.status, 'id', None)
                        if current_status_id is None:
                            current_status_id = getattr(created, 'status_id', None)
                        if current_status_id is not None and int(current_status_id) != int(desired_status_id):
                            redmine.issue.update(created.id, status_id=desired_status_id)
                            logging.info('Updated issue %s status from %s to %s', created.id, current_status_id, desired_status_id)
                    except Exception as exc:
                        logging.debug('Could not enforce status for issue %s: %s', created.id, exc)

                # Keep original timestamp and closed date values when possible.
                date_update_payload = {}
                for date_field in ('created_on', 'updated_on', 'closed_on'):
                    if row.get(date_field):
                        date_update_payload[date_field] = row[date_field]
                if date_update_payload:
                    try:
                        redmine.issue.update(created.id, **date_update_payload)
                        logging.info('Updated date fields for issue %s: %s', created.id, date_update_payload)
                    except Exception as exc:
                        logging.debug('Could not update timestamp/closed fields for issue %s: %s', created.id, exc)

                # Add a note to preserve original author and creation information when API cannot set author directly.
                try:
                    raw_obj = json.loads(row.get('raw_json', '{}') or '{}')
                    decoded = raw_obj.get('_decoded_attrs', raw_obj)
                    original_author = decoded.get('author', {}).get('name') if isinstance(decoded.get('author'), dict) else None
                    if not original_author and row.get('author_id'):
                        original_author = get_user_name_by_legacy_id(conn, row.get('author_id'))
                    if original_author:
                        author_note = 'Original author: {0}'.format(original_author)
                        redmine.issue.update(created.id, notes=author_note)
                except Exception as exc:
                    logging.debug('Could not add original author note for issue %s: %s', created.id, exc)

                push_issue_journals(redmine, conn, row['legacy_id'], created.id)
                push_issue_attachments(
                    redmine,
                    conn,
                    row['legacy_id'],
                    created.id,
                    api_key=api_key,
                    attachment_root=attachment_root,
                )
            continue

        table = resource
        if db_conn is not None:
            existing_remote_ids, _ = get_existing_remote_ids_db(db_conn, api_name)
        else:
            existing_remote_ids, _ = get_existing_remote_ids(redmine, api_name, api_key=api_key)
        cur = conn.execute('SELECT * FROM {0} ORDER BY legacy_id ASC'.format(table))
        cols = [d[0] for d in cur.description]
        for raw_row in cur.fetchall():
            row = dict(zip(cols, raw_row))
            if row['legacy_id'] is None:
                continue
            if get_id_mapping(conn, resource, row['legacy_id']):
                continue

            if row['legacy_id'] in existing_remote_ids:
                logging.info(
                    'Skipping creation for %s legacy %s: target already exists (id=%s)',
                    resource,
                    row['legacy_id'],
                    row['legacy_id'],
                )
                set_id_mapping(conn, resource, row['legacy_id'], row['legacy_id'])
                continue

            if resource != 'projects':
                maybe_pause('About to push {0} legacy {1}'.format(resource, row['legacy_id']), interactive)
            if not ensure_target_id_sequence(redmine, api_name, row['legacy_id'], api_key=api_key, db_conn=db_conn):
                logging.warning('Skipping %s legacy %s because ID sequence cannot be preserved.', resource, row['legacy_id'])
                continue

            payload = create_resource_payload(api_name, row)
            if not payload:
                continue

            try:
                created = getattr(redmine, api_name).create(**payload)
                set_id_mapping(conn, resource, row['legacy_id'], created.id)
            except Exception as exc:
                logging.exception('Failed to push %s %s (legacy %s): %s', resource, row.get('name'), row['legacy_id'], exc)


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Redmine issue importer.'
    )
    parser.add_argument(
        "--source_dir",
        default="/data/web/redmine/issue_export/issues",
        help="Directory containing issue JSON export files",
    )
    parser.add_argument(
        '--db_path',
        default='/data/web/redmine/redmine_issues.db',
        help='SQLite database path',
    )
    parser.add_argument(
        '--import_resources',
        default=','.join(get_default_resources()),
        help='Comma-separated resource names to import (default: all major resources)',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force update existing rows even if found by legacy_id',
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging',
    )
    parser.add_argument(
        '--migrate_redmine',
        action='store_true',
        help='After seeding SQLite, push into Redmine via API',
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Pause at key migration steps for inspection',
    )
    parser.add_argument(
        '--redmine_url',
        default='http://redmine:3000',
        help='Redmine base URL for destination',
    )
    parser.add_argument(
        '--api_key',
        default=DEV_API_KEY,
        help='Redmine API key to authenticate with destination (defaults to DEV_API_KEY)',
    )
    parser.add_argument(
        '--use_db_source',
        action='store_true',
        help='Use direct database lookups instead of Redmine API for existing id detection',
    )
    parser.add_argument('--db_host', default='mariadb', help='Database host (docker-compose service name)')
    parser.add_argument('--db_port', type=int, default=3306, help='Database port')
    parser.add_argument('--db_user', default='root', help='Database user')
    parser.add_argument('--db_password', default='redmine_root_password', help='Database password')
    parser.add_argument('--db_name', default='redmine', help='Database name')
    parser.add_argument(
        "--attachment-root",
        default="/data/web/redmine/attachments",
        help="Local filesystem path to exported attachment files for importing into Redmine",
    )
    return parser.parse_args()


def main():
    """Script entry point."""
    args = parse_args()
    setup_logging(logging.DEBUG if args.debug else logging.INFO)

    logging.info('Import mode: %s', 'DB source' if args.use_db_source else 'API source')

    if not os.path.isdir(args.source_dir):
        logging.error('Source directory does not exist: %s', args.source_dir)
        return

    conn = sqlite3.connect(args.db_path)
    create_schema(conn)

    resources = [r.strip() for r in args.import_resources.split(',') if r.strip()]
    if resources:
        source_root = os.path.dirname(args.source_dir.rstrip('/'))
        import_generic_resources(source_root, conn, resources)

    conn.close()

    import_issues(args.source_dir, args.db_path, force=args.force)
    logging.info('SQLite issue import complete, now starting migration to Redmine...')

    if args.migrate_redmine:
        if not args.api_key and not args.use_db_source:
            logging.error('API key required for --migrate_redmine with API source')
            return

        db_conn = None
        if args.use_db_source:
            try:
                db_conn = get_db_connection(
                    host=args.db_host,
                    port=args.db_port,
                    user=args.db_user,
                    password=args.db_password,
                    db=args.db_name,
                )
            except Exception as exc:
                logging.error('Cannot connect to DB %s:%s/%s: %s', args.db_host, args.db_port, args.db_name, exc)
                return

        attachment_root = args.attachment_root
        if not attachment_root:
            source_dir = args.source_dir.rstrip('/')
            source_parent = os.path.dirname(source_dir)
            if os.path.basename(source_dir) == 'issues':
                attachment_root = os.path.join(os.path.dirname(source_parent), 'attachments')
            else:
                attachment_root = os.path.join(source_parent, 'attachments')
        if attachment_root and not os.path.isdir(attachment_root):
            logging.warning('Attachment root not found or inaccessible: %s', attachment_root)
            attachment_root = None
        if attachment_root:
            logging.info('Using attachment root: %s', attachment_root)

        remote = RemoteRedmine(args.redmine_url, key=args.api_key, requests={'verify': False, 'timeout': 60})
        conn = sqlite3.connect(args.db_path)
        create_id_mapping_table(conn)

        push_cube_to_redmine(
            remote,
            conn,
            interactive=args.interactive,
            api_key=args.api_key,
            db_conn=db_conn,
            attachment_root=attachment_root,
        )
        conn.close()

        if db_conn is not None:
            db_conn.close()


if __name__ == '__main__':
    main()
