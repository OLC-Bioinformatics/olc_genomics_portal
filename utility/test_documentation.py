"""Check local Markdown links in the repository's user and operator docs."""

import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
LINK_PATTERN = re.compile(r'(?<!!)\[[^\]]+\]\(([^)]+)\)')
FENCE_PATTERN = re.compile(r'^\s*(`{3,}|~{3,})')


def _markdown_files(root):
    """Return the root README and documentation Markdown files."""
    paths = []
    readme = root / 'README.md'
    if readme.is_file():
        paths.append(readme)
    docs = root / 'docs'
    if docs.is_dir():
        paths.extend(sorted(docs.rglob('*.md')))
    return paths


def _local_path(target):
    """Return a local link path, or None for external and anchor-only links."""
    target = target.strip()
    if not target:
        return None
    if target.startswith('<') and '>' in target:
        target = target[1:target.index('>')]
    else:
        target = target.split(maxsplit=1)[0]
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return unquote(parsed.path)


def find_broken_local_links(root):
    """List unresolved relative Markdown links in README and docs."""
    failures = []
    for source in _markdown_files(root):
        fence = None
        for line_number, line in enumerate(
            source.read_text(encoding='utf-8').splitlines(), start=1
        ):
            marker = FENCE_PATTERN.match(line)
            if marker:
                marker_text = marker.group(1)
                if fence is None:
                    fence = (marker_text[0], len(marker_text))
                elif (
                    marker_text[0] == fence[0]
                    and len(marker_text) >= fence[1]
                ):
                    fence = None
                continue
            if fence is not None:
                continue
            for match in LINK_PATTERN.finditer(line):
                path = _local_path(match.group(1))
                if path is None:
                    continue
                target = (
                    root / path.lstrip('/')
                    if path.startswith('/')
                    else source.parent / path
                )
                if not target.exists():
                    failures.append(
                        f'{source.relative_to(root)}:{line_number}: '
                        f'broken link target {path}'
                    )
    return failures


def _documented_variable_mappings(root):
    """Return Dev variable-group and Key Vault mappings from the docs table."""
    guide = (root / 'docs' / 'deployment.md').read_text(encoding='utf-8')
    table = guide.split('| Azure DevOps variable |', 1)[1].split(
        '\nAfter setting all required values', 1
    )[0]
    mappings = []
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) != 3 or not cells[0].startswith('`'):
            continue
        group_name = cells[0].strip('`')
        env_name = cells[1].strip('`')
        key_vault_name = (
            cells[2].strip('`') if cells[2].startswith('`') else None
        )
        mappings.append((group_name, env_name, key_vault_name))
    return mappings


def _required_dev_variable_mappings(root):
    """Read required Dev group variables and their generated env-file names."""
    pipeline = (root / 'azure-pipelines-dev.yml').read_text(encoding='utf-8')
    secret_block = re.search(
        r'(?ms)^\s*secret_names = \(\n(.*?)^\s*\)',
        pipeline,
    )
    env_file_block = re.search(
        r'(?ms)^\s*env_file_names = \{\n(.*?)^\s*\}',
        pipeline,
    )
    if not secret_block or not env_file_block:
        raise ValueError('Could not find Dev pipeline secret mappings.')
    secret_names = re.findall(r'"([A-Z0-9_]+)"', secret_block.group(1))
    env_file_names = dict(
        re.findall(
            r'"([A-Z0-9_]+)": "([A-Z0-9_]+)"',
            env_file_block.group(1),
        )
    )

    display_name = "displayName: 'Deploy portal on dev VM'"
    deploy_step = pipeline.split(display_name, 1)[1]
    env_block = deploy_step.split('      env:\n', 1)[1]
    group_names = {}
    for line in env_block.splitlines():
        match = re.fullmatch(
            r'        ([A-Z0-9_]+): \$\(([A-Za-z0-9_]+)\)',
            line,
        )
        if not match:
            break
        group_names[match.group(1)] = match.group(2)

    missing = set(secret_names) - set(group_names)
    if missing:
        raise ValueError(
            'Dev pipeline task is missing secret mappings: '
            + ', '.join(sorted(missing))
        )
    return {
        (
            group_names[name],
            env_file_names.get(name, name),
        )
        for name in secret_names
    }


def _fullinfra_key_vault_mappings(root):
    """Read the Key Vault secret-to-env mapping from the full-infra pipeline."""
    pipeline = (root / 'azure-pipelines-full-infra.yml').read_text(
        encoding='utf-8'
    )
    secret_block = re.search(
        r'(?ms)^\s+secrets=\(\n(.*?)^\s+\)',
        pipeline,
    )
    if not secret_block:
        raise ValueError('Could not find full-infrastructure secret list.')
    secret_names = re.findall(r'^\s*([a-z0-9-]+)\s*$', secret_block.group(1), re.M)
    names_by_secret = dict(
        re.findall(
            r"(?m)^\s*([a-z0-9-]+)\) var_name='([A-Z0-9_]+)'",
            pipeline,
        )
    )
    missing = set(secret_names) - set(names_by_secret)
    if missing:
        raise ValueError(
            'Full-infrastructure pipeline is missing Key Vault mappings: '
            + ', '.join(sorted(missing))
        )
    return {
        (names_by_secret[secret], secret)
        for secret in secret_names
    }


class DocumentationLinkTests(unittest.TestCase):
    def test_repository_documentation_links_resolve(self):
        self.assertEqual(find_broken_local_links(ROOT), [])

    def test_reports_missing_local_link(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / 'README.md').write_text(
                '[Missing](docs/missing.md)\n', encoding='utf-8'
            )

            failures = find_broken_local_links(root)

        self.assertEqual(len(failures), 1)
        self.assertIn('README.md:1', failures[0])

    def test_ignores_external_anchor_and_fenced_code_links(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / 'README.md').write_text(
                '[External](https://example.test) [Anchor](#section)\n'
                '```markdown\n[Example](missing.md)\n```\n',
                encoding='utf-8',
            )

            failures = find_broken_local_links(root)

        self.assertEqual(failures, [])

    def test_deployment_variable_group_names_match_dev_pipeline(self):
        documented = {
            (group_name, env_name)
            for group_name, env_name, _ in _documented_variable_mappings(ROOT)
        }
        self.assertEqual(
            documented,
            _required_dev_variable_mappings(ROOT),
        )

    def test_deployment_key_vault_names_match_fullinfra_pipeline(self):
        documented = {
            (env_name, key_vault_name)
            for _, env_name, key_vault_name in _documented_variable_mappings(
                ROOT
            )
            if key_vault_name is not None
        }
        self.assertEqual(
            documented,
            _fullinfra_key_vault_mappings(ROOT),
        )

    def test_fullinfra_nanopore_agent_sku_macro_matches_its_setting_name(self):
        pipeline = (ROOT / 'azure-pipelines-full-infra.yml').read_text(
            encoding='utf-8'
        )
        self.assertRegex(
            pipeline,
            r'(?m)^\s*NANOPORE_NODE_AGENT_SKU: '
            r'\$\(NANOPORE_NODE_AGENT_SKU\)$',
        )


if __name__ == '__main__':
    unittest.main()
