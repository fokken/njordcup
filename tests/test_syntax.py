import unittest
from unittest.mock import patch

from njordcup.index import build_index, parse_file
from njordcup import syntax


class SyntaxFallbackTests(unittest.TestCase):
    def test_unavailable_parser_and_text_mode_keep_all_lines(self):
        source = 'function serve() {\n  execute(input);\n}\n// trailing code\n'
        with patch('njordcup.syntax.get_parser', side_effect=ImportError('missing')):
            parsed = parse_file('app.js', source, 80)
        self.assertEqual(parsed['parser'], 'lexical')
        self.assertTrue(any('unavailable' in n for n in parsed['notes']))
        self.assertEqual([i for c in parsed['chunks'] for i in range(c['start'], c['end'] + 1)], [1, 2, 3, 4])
        with patch('njordcup.syntax.get_parser') as parser:
            self.assertEqual(parse_file('app.js', source, 80, 'text')['parser'], 'text')
            self.assertEqual(parse_file('app.custom', source, 80)['parser'], 'lexical')
        parser.assert_not_called()

    def test_package_changes_invalidate_cached_metadata(self):
        sources = {'app.py': 'def serve():\n    pass\n'}
        with patch('njordcup.syntax.profile', return_value={'version': 'a'}):
            first = build_index(sources, list(sources))
            reused = build_index(sources, list(sources), first)
        with patch('njordcup.syntax.profile', return_value={'version': 'b'}):
            rebuilt = build_index(sources, list(sources), first)
        self.assertEqual(reused['stats']['reused_files'], 1)
        self.assertEqual(rebuilt['stats']['reused_files'], 0)


class SyntaxGrammarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            for grammar in set(syntax.GRAMMARS.values()):
                syntax.get_parser(*grammar)
        except syntax.FAILURES as exc:
            raise unittest.SkipTest('Install .[syntax] to run real-grammar tests') from exc

    def test_real_grammars_extract_ranges_calls_and_preserve_full_source(self):
        examples = {
            'app.js': 'function serve() {\n  execute(input);\n}\n',
            'app.ts': 'function serve(input: string): void {\n  execute(input);\n}\n',
            'app.tsx': 'function serve() {\n  execute(input);\n  return <div/>;\n}\n',
            'app.go': 'package app\nfunc serve() {\n execute(input)\n}\n',
            'app.rs': 'fn serve() {\n execute(input);\n}\n',
            'App.java': 'class App {\n void serve() {\n execute(input);\n }\n}\n',
            'app.c': 'void serve() {\n execute(input);\n}\n',
            'app.cpp': 'void serve() {\n execute(input);\n}\n',
        }
        for path, source in examples.items():
            with self.subTest(path=path):
                result = parse_file(path, source, 60)
                self.assertEqual(result['parser'], 'tree_sitter')
                symbol = next(s for s in result['symbols'] if s['name'] == 'serve')
                self.assertGreater(symbol['end'], symbol['start'])
                self.assertIn('execute', result['calls'])
                self.assertNotIn('serve', result['calls'])
                self.assertEqual([i for c in result['chunks'] for i in range(c['start'], c['end'] + 1)],
                                 list(range(1, len(source.splitlines()) + 1)))

    def test_arrow_unicode_comments_and_dependency_context(self):
        sources = {'app.ts': '// function fake() { bogus(); }\nconst café = (x: string) => {\n return execute(x);\n};\n',
                   'helper.ts': 'export function execute(x: string) { return x; }'}
        index = build_index(sources, list(sources))
        parsed = index['files']['app.ts']
        self.assertEqual([s['name'] for s in parsed['symbols']], ['café'])
        self.assertEqual(parsed['calls'], ['execute'])
        self.assertEqual(index['dependencies']['app.ts'], ['helper.ts'])
        self.assertEqual(index['stats']['indexing_methods']['tree_sitter'], 2)
        self.assertEqual(build_index(sources, list(sources), index)['stats']['reused_files'], 2)

    def test_malformed_source_falls_back_without_losing_coverage(self):
        source = 'function broken( {\nexecute(input);\n'
        parsed = parse_file('app.js', source, 500)
        self.assertEqual(parsed['parser'], 'lexical')
        self.assertIn('Tree-sitter syntax errors; using lexical metadata', parsed['notes'])
        self.assertEqual(parsed['chunks'][-1]['end'], 2)
