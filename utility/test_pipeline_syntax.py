"""Validate Azure pipeline YAML and embedded shell syntax."""

import subprocess
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent
SHELL_KEYS = {'bash', 'script', 'inlineScript'}


def _scripts(value, path='pipeline'):
    """Yield shell-script strings and their YAML key paths."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f'{path}.{key}'
            if key in SHELL_KEYS and isinstance(child, str):
                yield child_path, child
            else:
                yield from _scripts(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _scripts(child, f'{path}[{index}]')


class PipelineSyntaxTests(unittest.TestCase):
    def test_pipeline_yaml_parses(self):
        paths = sorted(ROOT.glob('azure-pipelines*.yml'))
        self.assertTrue(paths, 'No Azure pipeline YAML files were found.')
        for path in paths:
            with self.subTest(pipeline=path.name):
                with path.open(encoding='utf-8') as stream:
                    yaml.safe_load(stream)

    def test_embedded_shell_scripts_parse(self):
        script_count = 0
        for path in sorted(ROOT.glob('azure-pipelines*.yml')):
            with path.open(encoding='utf-8') as stream:
                pipeline = yaml.safe_load(stream)
            for script_path, script in _scripts(pipeline, path.name):
                script_count += 1
                result = subprocess.run(
                    ['bash', '-n'],
                    input=script,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f'{script_path} has invalid shell syntax:\n{result.stderr}',
                )
        self.assertGreater(script_count, 0, 'No shell scripts were found.')


if __name__ == '__main__':
    unittest.main()
