"""Optional syntax metadata. Grammars improve context; they never restrict scope."""
from functools import lru_cache
from importlib import import_module, metadata
from pathlib import PurePosixPath

GRAMMARS = {
    '.js': ('javascript', 'language'), '.jsx': ('javascript', 'language'),
    '.mjs': ('javascript', 'language'), '.cjs': ('javascript', 'language'),
    '.ts': ('typescript', 'language_typescript'), '.tsx': ('typescript', 'language_tsx'),
    '.go': ('go', 'language'), '.rs': ('rust', 'language'),
    '.java': ('java', 'language'), '.c': ('c', 'language'), '.h': ('c', 'language'),
    '.cpp': ('cpp', 'language'), '.cc': ('cpp', 'language'), '.cxx': ('cpp', 'language'),
    '.hpp': ('cpp', 'language'), '.hh': ('cpp', 'language'),
}
FUNCTIONS = {'function_declaration', 'function_definition', 'function_item',
             'method_declaration', 'method_definition', 'constructor_declaration',
             'arrow_function', 'function_expression', 'generator_function_declaration',
             'generator_function'}
CLASSES = {'class_declaration', 'class_specifier', 'struct_specifier', 'struct_item',
           'interface_declaration', 'enum_declaration', 'enum_item', 'trait_item'}
CALLS = {'call_expression', 'method_invocation', 'macro_invocation'}
FAILURES = (ImportError, AttributeError, ValueError, TypeError, OSError, RuntimeError)


def profile():
    """Invalidate persisted metadata when optional parser packages change."""
    versions = {}
    for name in ['tree-sitter'] + ['tree-sitter-' + g for g in sorted({g for g, _ in GRAMMARS.values()})]:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    return {'version': 1, 'packages': versions}


@lru_cache(maxsize=16)
def get_parser(grammar, loader):
    from tree_sitter import Language, Parser
    module = import_module('tree_sitter_' + grammar)
    return Parser(Language(getattr(module, loader)()))


def extract(path, source):
    """Return symbols/call names or a fallback note. No downloads or source execution."""
    grammar = GRAMMARS.get(PurePosixPath(path).suffix.lower())
    if grammar is None:
        return None, None
    try:
        parser = get_parser(*grammar)
        raw = source.encode('utf-8')
        tree = parser.parse(raw)
        if tree.root_node.has_error:
            return None, 'Tree-sitter syntax errors; using lexical metadata'
        symbols, calls = [], []

        def text(node):
            return raw[node.start_byte:node.end_byte].decode('utf-8')

        def identifier(node):
            # Resolve syntactic names, not the target of dynamic dispatch.
            while node is not None:
                if node.type in {'identifier', 'field_identifier', 'property_identifier', 'type_identifier'}:
                    return text(node)
                next_node = None
                for field in ('name', 'declarator', 'field', 'property'):
                    next_node = node.child_by_field_name(field)
                    if next_node is not None:
                        break
                node = next_node
            return None

        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in FUNCTIONS | CLASSES:
                name = identifier(node)
                if name is None and node.type in {'arrow_function', 'function_expression', 'generator_function'}:
                    parent = node.parent
                    if parent is not None and parent.type in {'variable_declarator', 'pair'}:
                        name = identifier(parent.child_by_field_name('name') or parent.child_by_field_name('key'))
                # Anonymous functions still give useful boundaries, without invented names.
                symbols.append({'name': name or '<anonymous>', 'start': node.start_point[0] + 1,
                                'end': node.end_point[0] + (1 if node.end_point[1] else 0),
                                'kind': 'class' if node.type in CLASSES else 'function'})
            if node.type in CALLS:
                target = (node.child_by_field_name('function') or node.child_by_field_name('name')
                          or node.child_by_field_name('macro'))
                name = identifier(target)
                if name:
                    calls.append(name)
            stack.extend(reversed(node.named_children))
        return (symbols, calls), None
    except FAILURES:
        return None, 'Tree-sitter unavailable or incompatible; using lexical metadata'
