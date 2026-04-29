"""
@file CadQueryValidator.py
@brief Validate CadQuery input code
@description
  Strict whitelist for imports/builtins/method-calls + AST-level guards.
  Phase 3.5: relaxed to allow common Python constructs needed for parametric
  modelling (functions, loops, comprehensions, math/typing imports) without
  opening arbitrary execution.

@author 30hours
"""

import ast
import re
from typing import Optional, Tuple


class CadQueryValidator:
    """
    @brief Validate a CadQuery user script before execution.

    Two-pass design:
      1. Collect every locally-bound name (function defs, assignment targets,
         loop variables, comprehension targets, function/lambda parameters,
         except-as, with-as, imports). These are then trusted as call targets
         and as roots of attribute chains -- they cannot leak the real
         interpreter because dangerous builtins (eval/exec/__import__/getattr)
         are banned both in the validator and in the execution sandbox.
      2. Walk the AST: enforce import whitelist, builtin whitelist, dunder
         attribute/name ban, banned-statement list, while-True guard.
    """

    def __init__(self):
        # explicitly define allowed import structure
        # 'as' (set) restricts the alias name when 'import X as Y' is used.
        # 'functions' (set) restricts the symbols importable via 'from X import …'
        # AND the attribute names usable on the module object (X.foo(...)).
        self.allowed_imports = {
            'cadquery': {'as': {'cq'}},  # only allow "import cadquery as cq"
            'math': {'functions': {
                'sin', 'cos', 'tan',
                'asin', 'acos', 'atan', 'atan2',
                'pi', 'e', 'tau',
                'sqrt', 'pow', 'exp', 'log', 'log10', 'log2',
                'radians', 'degrees',
                'ceil', 'floor', 'trunc',
                'fabs', 'hypot', 'copysign',
                'inf', 'nan', 'isnan', 'isinf', 'isfinite',
            }},
            'numpy': {
                'as': {'np'},
                'functions': {
                    # array creation and manipulation
                    'array', 'zeros', 'ones', 'linspace', 'arange',
                    # math operations
                    'sin', 'cos', 'tan', 'arcsin', 'arccos', 'arctan', 'arctan2',
                    'deg2rad', 'rad2deg', 'pi',
                    'sqrt', 'square', 'power', 'exp', 'log', 'log10',
                    # statistics
                    'mean', 'median', 'std', 'min', 'max',
                    # linear algebra
                    'dot', 'cross', 'transpose',
                    # rounding
                    'floor', 'ceil', 'round',
                    # array operations
                    'concatenate', 'stack', 'reshape', 'flatten',
                },
            },
            'typing': {'functions': {
                'List', 'Tuple', 'Dict', 'Optional', 'Union',
                'Any', 'Set', 'FrozenSet', 'Iterable', 'Iterator',
                'Sequence', 'Callable', 'Mapping',
            }},
        }

        # expanded set of allowed CadQuery operations (cq.<attr> / chain attrs)
        self.allowed_cq_operations = {
            # core operations
            'Workplane', 'box', 'circle', 'cylinder', 'sphere',
            'extrude', 'revolve', 'union', 'cut', 'fillet',
            'chamfer', 'vertices', 'edges', 'faces', 'shell',
            'offset2D', 'offset', 'wire', 'rect', 'polygon',
            'polyline', 'spline', 'close', 'moveTo', 'lineTo',
            'line', 'vLineTo', 'hLineTo', 'mirrorY', 'mirrorX',
            'translate', 'rotate', 'size',
            # additional 2D operations
            'center', 'radiusArc', 'threePointArc', 'ellipse',
            'ellipseArc', 'close', 'section', 'slot',
            # 3D operations
            'loft', 'sweep', 'twistExtrude', 'ruled',
            'wedge', 'cone', 'hull', 'mirror', 'hole', 'cboreHole',
            'cskHole',
            # selection operations
            'all', 'size', 'item', 'itemAt', 'first', 'last',
            'end', 'vertices', 'faces', 'edges', 'wires', 'solids',
            'shells', 'compounds', 'vals', 'add', 'combine',
            # workplane operations
            'workplane', 'plane', 'transformed',
            'center', 'pushPoints', 'cutBlind', 'cutThruAll',
            'close', 'toPending', 'workplaneFromTagged',
            # selector strings as attributes
            'tag', 'end', 'val', 'wire', 'solid', 'face',
            # direction selectors
            'rarray', 'polarArray', 'grid',
            # boolean operations
            'intersect', 'combine', 'each',
            # measurement and inspection
            'val', 'vals', 'dump',
            # string constants for plane selection
            'XY', 'YZ', 'XZ', 'front', 'back', 'left',
            'right', 'top', 'bottom',
            # common string selectors (kept for back-compat; not really attrs)
            '|Z', '>Z', '<Z', '|X', '>X', '<X',
            '|Y', '>Y', '<Y', '#Z', '#X', '#Y',
        }

        # whitelisted Python builtins
        self.allowed_builtins = {
            # type constructors / casts
            'float', 'int', 'bool', 'str', 'list', 'tuple', 'dict', 'set',
            'frozenset',
            # constants
            'True', 'False', 'None',
            # iteration helpers
            'range', 'len', 'enumerate', 'zip', 'map', 'filter',
            'sorted', 'reversed', 'iter', 'next',
            # numeric helpers
            'min', 'max', 'sum', 'abs', 'round', 'divmod', 'pow',
            # introspection (safe subset)
            'isinstance', 'type',
            # debug (output captured server-side, not returned to client)
            'print',
            # exceptions the LLM sometimes raises
            'Exception', 'ValueError', 'TypeError', 'IndexError',
            'KeyError', 'RuntimeError', 'StopIteration', 'ZeroDivisionError',
            'ArithmeticError', 'AssertionError', 'NotImplementedError',
        }

        # AST node types whose mere presence is an automatic rejection.
        self._banned_nodes = (
            ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith,
            ast.ClassDef,
            ast.Await, ast.Yield, ast.YieldFrom,
            ast.Global, ast.Nonlocal,
        )

        self.errors = []
        self.user_names = set()

    # -- helpers ----------------------------------------------------------
    def _add_target_names(self, node: ast.AST) -> None:
        """Record every name bound by an assignment / for / comprehension target."""
        if isinstance(node, ast.Name):
            self.user_names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for el in node.elts:
                self._add_target_names(el)
        elif isinstance(node, ast.Starred):
            self._add_target_names(node.value)
        # ast.Attribute and ast.Subscript targets do not bind a new name

    def _collect_user_names(self, tree: ast.AST) -> None:
        """Walk the tree once to gather every locally-bound name."""
        self.user_names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.user_names.add(node.name)
                args = node.args
                for a in list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs):
                    self.user_names.add(a.arg)
                if args.vararg:
                    self.user_names.add(args.vararg.arg)
                if args.kwarg:
                    self.user_names.add(args.kwarg.arg)
            elif isinstance(node, ast.Lambda):
                args = node.args
                for a in list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs):
                    self.user_names.add(a.arg)
                if args.vararg:
                    self.user_names.add(args.vararg.arg)
                if args.kwarg:
                    self.user_names.add(args.kwarg.arg)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    self._add_target_names(t)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                self._add_target_names(node.target)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                self._add_target_names(node.target)
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                for gen in node.generators:
                    self._add_target_names(gen.target)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        self._add_target_names(item.optional_vars)
            elif isinstance(node, ast.ExceptHandler):
                if node.name:
                    self.user_names.add(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self.user_names.add(alias.asname or alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.user_names.add(alias.asname or alias.name)

    @staticmethod
    def _is_dunder(name: str) -> bool:
        return name.startswith('__') and name.endswith('__') and len(name) >= 4

    @staticmethod
    def _while_test_is_truthy_constant(test: ast.AST) -> bool:
        """True for `while True`, `while 1`, `while 'x'`, etc. (constant truthy)."""
        if isinstance(test, ast.Constant):
            try:
                return bool(test.value)
            except Exception:
                return False
        return False

    @staticmethod
    def _has_break(body) -> bool:
        """Any Break statement reachable in this body (descending into nested loops too)."""
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Break):
                    return True
        return False

    # -- validators -------------------------------------------------------
    def check_import(self, node: ast.AST) -> None:
        """Validate imports against whitelist."""
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in self.allowed_imports:
                    self.errors.append(f"Import of '{alias.name}' is not allowed")
                    continue
                spec = self.allowed_imports[alias.name]
                if alias.asname and 'as' in spec and alias.asname not in spec['as']:
                    expected = ', '.join(sorted(spec['as']))
                    self.errors.append(
                        f"Module '{alias.name}' must be imported as one of: {expected}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            if module is None or module not in self.allowed_imports:
                self.errors.append(f"Import from '{module}' is not allowed")
                return
            spec = self.allowed_imports[module]
            allowed_funcs = spec.get('functions')
            if allowed_funcs is None:
                # module is whitelisted but no per-symbol restriction defined:
                # be conservative and reject "from X import *" / unknown symbols
                self.errors.append(
                    f"'from {module} import …' is not allowed (use 'import {module}' instead)"
                )
                return
            for alias in node.names:
                if alias.name == '*':
                    self.errors.append(f"'from {module} import *' is not allowed")
                    continue
                if alias.name not in allowed_funcs:
                    self.errors.append(
                        f"Import of {module}.{alias.name} is not allowed"
                    )

    def check_call(self, node: ast.Call) -> None:
        """Validate function calls against whitelist or local definitions."""
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
            if self._is_dunder(name):
                self.errors.append(f"Use of dunder name '{name}' is not allowed")
                return
            if name in self.allowed_builtins:
                return
            if name in self.user_names:
                return
            self.errors.append(f"Function call to '{name}' is not allowed")
            return
        if isinstance(func, ast.Attribute):
            # Find the base object of the attribute chain (a.b.c.d -> a)
            base = func.value
            while isinstance(base, ast.Attribute):
                base = base.value
            # Unknown bases (Call results, Subscripts, Constants, locals)
            # are tolerated -- we restrict only known modules.
            if isinstance(base, ast.Name):
                base_id = base.id
                if base_id == 'cq':
                    if func.attr not in self.allowed_cq_operations:
                        self.errors.append(
                            f"CadQuery operation '{func.attr}' is not allowed"
                        )
                elif base_id == 'math':
                    if func.attr not in self.allowed_imports['math']['functions']:
                        self.errors.append(
                            f"Math operation '{func.attr}' is not allowed"
                        )
                elif base_id == 'np':
                    if func.attr not in self.allowed_imports['numpy']['functions']:
                        self.errors.append(
                            f"Numpy operation '{func.attr}' is not allowed"
                        )
            # else: chained call result, locally-bound variable, lambda, etc.
            #   -> allowed (sandbox builtins prevent leaking interpreter state)

    def visit_and_validate(self, node: ast.AST) -> None:
        """Recursively visit and validate AST nodes."""
        # Outright-banned node types
        if isinstance(node, self._banned_nodes):
            self.errors.append(
                f"Usage of {node.__class__.__name__} is not allowed"
            )

        # Imports
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            self.check_import(node)

        # Calls
        elif isinstance(node, ast.Call):
            self.check_call(node)

        # Attribute access -- forbid only dunder access (escape vector
        # via __class__ / __subclasses__ / __builtins__ / __dict__ / ...)
        elif isinstance(node, ast.Attribute):
            if self._is_dunder(node.attr):
                self.errors.append(
                    f"Access to dunder attribute '{node.attr}' is not allowed"
                )

        # Bare Name references -- ban dunder names (e.g. __builtins__,
        # __import__, __name__) to close the most obvious escape paths.
        elif isinstance(node, ast.Name):
            if self._is_dunder(node.id):
                self.errors.append(
                    f"Use of dunder name '{node.id}' is not allowed"
                )

        # `while <truthy-constant>` must contain a `break`
        if isinstance(node, ast.While):
            if self._while_test_is_truthy_constant(node.test) and not self._has_break(node.body):
                self.errors.append(
                    "Infinite 'while True' without 'break' is not allowed"
                )

        # Recurse
        for child in ast.iter_child_nodes(node):
            self.visit_and_validate(child)

    def validate(self, code: str) -> Tuple[Optional[str], Optional[str]]:
        """
        @brief Validate CadQuery code.
        @returns (cleaned_code, None) on success ; (None, message) on failure.
        """
        self.errors = []
        self.user_names = set()
        # require an explicit `result = …` assignment somewhere
        if not re.search(r'(^|\n)\s*result\s*=', code):
            return None, "Code must assign to 'result' variable"
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return None, f"Invalid Python syntax: {str(e)}"
        self._collect_user_names(tree)
        self.visit_and_validate(tree)
        if self.errors:
            return None, "Validation failed: " + "; ".join(self.errors)
        return code, None
