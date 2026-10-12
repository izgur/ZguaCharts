"""Generate a single-study module from its live AST and required helper closure."""
from __future__ import annotations
import ast
import copy
from pathlib import Path
from vendor.astor import to_source
from indicators import INDICATOR_DEFAULTS, INDICATOR_LABELS, INDICATOR_PARAMETERS, parameter_schema
from indicator_catalog import CATALOG
from community_indicators import CATALOG as COMMUNITY_CATALOG

ROOT = Path(__file__).resolve().parents[1]


def known(node, key):
    if isinstance(node, ast.Name) and node.id == "key": return key
    if isinstance(node, ast.Name) and node.id == "requested_set": return {key}
    if isinstance(node, ast.Name) and node.id == "COMMUNITY_CATALOG": return set(COMMUNITY_CATALOG)
    if isinstance(node, (ast.Str, ast.Num, ast.NameConstant)): return ast.literal_eval(node)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)): return [known(value, key) for value in node.elts]
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = known(node.left, key), known(node.comparators[0], key)
        op = node.ops[0]
        if isinstance(op, ast.Eq): return left == right
        if isinstance(op, ast.NotEq): return left != right
        if isinstance(op, ast.In): return left in right
        if isinstance(op, ast.NotIn): return left not in right
    raise ValueError("Dynamic expression")


class SelectStudy(ast.NodeTransformer):
    def __init__(self, key): self.key = key

    def visit_If(self, node):
        try: choice = known(node.test, self.key)
        except (ValueError, TypeError): return self.generic_visit(node)
        body = node.body if choice else node.orelse
        result = []
        for statement in body:
            transformed = self.visit(statement)
            if isinstance(transformed, list): result.extend(transformed)
            elif transformed is not None: result.append(transformed)
        return result

    def visit_IfExp(self, node):
        try: choice = known(node.test, self.key)
        except (ValueError, TypeError): return self.generic_visit(node)
        return self.visit(node.body if choice else node.orelse)


def single_source(key):
    if key not in INDICATOR_LABELS: raise KeyError(key)
    filename = "community_indicators.py" if key in COMMUNITY_CATALOG else "indicator_catalog.py" if key in CATALOG else "indicators.py"
    native = ast.parse((ROOT / "indicators.py").read_text(encoding="utf-8"))
    selected = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    helpers = {node.name: node for node in native.body if isinstance(node, ast.FunctionDef)}
    helpers.update({node.name: node for node in selected.body if isinstance(node, ast.FunctionDef)})
    main_name = "calculate" if key in CATALOG else "build_indicator_payload"
    main = copy.deepcopy(helpers[main_name])
    if key not in CATALOG:
        # Catalogue dispatch is unrelated to a selected base study.
        main.body = [node for node in main.body if not isinstance(node, ast.For)]
    main = SelectStudy(key).visit(main)
    helpers[main_name] = main
    required, pending = {}, [main_name]
    if key in CATALOG: pending += ["candles_to_frame", "timed", "line_series", "histogram_series"]
    while pending:
        name = pending.pop()
        if name in required: continue
        required[name] = copy.deepcopy(helpers[name])
        for node in ast.walk(required[name]):
            if isinstance(node, ast.Name) and node.id in helpers and node.id not in required:
                pending.append(node.id)
    # The schema is a literal for this indicator only, not a registry of others.
    if "parameter_schema" in required:
        required["parameter_schema"] = ast.parse("def parameter_schema(key):\n    return " + repr(parameter_schema(key))).body[0]
    placement = CATALOG[key]["placement"] if key in CATALOG else "overlay" if key in ("ema", "sma", "vwap", "bbands", "supertrend") else "pane"
    metadata = "\n".join([
        "NAME = " + repr(INDICATOR_LABELS[key]), "DEFAULTS = " + repr(INDICATOR_DEFAULTS[key]),
        "PLACEMENT = " + repr(placement), "KEY = " + repr(key),
        "INDICATOR_LABELS = {KEY: NAME}", "INDICATOR_DEFAULTS = {KEY: DEFAULTS}",
        "INDICATOR_PARAMETERS = {KEY: " + repr(INDICATOR_PARAMETERS[key]) + "}",
        "CATALOG = {KEY: {'label': NAME}}", "ta = None",
        'pd.Series.index_frame = property(lambda self: self.attrs.get("source_frame"))',
    ])
    functions = "\n\n".join(to_source(node) for node in required.values())
    if key not in CATALOG:
        wrapper = 'def indicator(candles, params=None):\n    return build_indicator_payload(candles, [KEY], settings={KEY: dict(DEFAULTS, **(params or {}))})\n'
    else:
        wrapper = '''def indicator(candles, params=None):
    df = candles_to_frame(candles)
    if df.empty:
        return {"overlays": [], "panes": []}
    output = []
    for item in calculate(df, KEY, dict(DEFAULTS, **(params or {}))):
        values = timed(item["values"], df)
        series = histogram_series(item["name"], values) if item["type"] == "histogram" else line_series(item["name"], values, item["color"])
        series.update(indicatorId=KEY, guide=item.get("guide", False))
        for field in ("offset", "cloud", "pointOnly", "segments", "events", "markerGroup"):
            if field in item: series[field] = item[field]
        if item.get("colors") is not None:
            for point, color in zip(series["data"], item["colors"]): point["color"] = str(color)
        if "markers" in item: series["markers"] = item["markers"]
        output.append(series)
    return {"overlays": output if PLACEMENT == "overlay" else [], "panes": [] if PLACEMENT == "overlay" else [{"id": KEY, "title": NAME, "series": output}]}
'''
    notice = "# Single indicator and its required supporting functions.\n"
    if key in COMMUNITY_CATALOG:
        entry = COMMUNITY_CATALOG[key]
        notice += "# MPL-2.0: https://mozilla.org/MPL/2.0/\n# Adapted from " + entry["author"] + "\n# " + entry["sourceUrl"] + "\n"
    return notice + "from __future__ import annotations\nimport math\nimport numpy as np\nimport pandas as pd\n\n" + metadata + "\n\n" + functions + "\n\n" + wrapper
