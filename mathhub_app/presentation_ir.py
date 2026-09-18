from __future__ import annotations

import re
from typing import Any

from presentation_notation import app_label, type_label

IR_VERSION = "surface-ir-v3"

_DOMAIN_UNICODE = {
    "Nat": "ℕ",
    "Int": "ℤ",
    "Rat": "ℚ",
    "Real": "ℝ",
    "Complex": "ℂ",
}
_DOMAIN_LATEX = {
    "Nat": r"\mathbb{N}",
    "ℕ": r"\mathbb{N}",
    "Int": r"\mathbb{Z}",
    "ℤ": r"\mathbb{Z}",
    "Rat": r"\mathbb{Q}",
    "ℚ": r"\mathbb{Q}",
    "Real": r"\mathbb{R}",
    "ℝ": r"\mathbb{R}",
    "Complex": r"\mathbb{C}",
    "ℂ": r"\mathbb{C}",
}
_GREEK_LATEX = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ε": r"\varepsilon",
    "ζ": r"\zeta",
    "η": r"\eta",
    "θ": r"\theta",
    "κ": r"\kappa",
    "λ": r"\lambda",
    "μ": r"\mu",
    "ν": r"\nu",
    "ξ": r"\xi",
    "π": r"\pi",
    "ρ": r"\rho",
    "σ": r"\sigma",
    "τ": r"\tau",
    "φ": r"\varphi",
    "χ": r"\chi",
    "ψ": r"\psi",
    "ω": r"\omega",
}
_OP_LATEX = {
    "↔": r"\Longleftrightarrow",
    "→": r"\Longrightarrow",
    "∨": r"\lor",
    "∧": r"\land",
    "=": "=",
    "≠": r"\ne",
    "≤": r"\le",
    "≥": r"\ge",
    "<": "<",
    ">": ">",
    "∈": r"\in",
    "∉": r"\notin",
    "⊆": r"\subseteq",
}
_INFIX_TOKENS = {
    "+", "-", "*", "/", "^", "%", "=", "≠", "<", ">", "≤", "≥", "∈", "∉", "⊆",
    "∧", "∨", "↔", "→", "×", "∘", "::", ":=",
}


def _space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def normalize_surface(text: str) -> str:
    value = _space(text)
    value = value.replace("<->", "↔").replace("->", "→")
    value = value.replace("<=", "≤").replace(">=", "≥").replace("!=", "≠")
    for source, target in _DOMAIN_UNICODE.items():
        # Nat as a type is notation; Nat.Prime is a namespace-qualified head.
        value = re.sub(rf"\b{re.escape(source)}\b(?!\.)", target, value)
    value = re.sub(r"\s*,\s*", ", ", value)
    value = re.sub(r"\s*:\s*", " : ", value)
    value = re.sub(r"\s*→\s*", " → ", value)
    value = re.sub(r"\s*×\s*", " × ", value)
    return _space(value)


def _balanced_outer(text: str) -> bool:
    if len(text) < 2 or text[0] != "(" or text[-1] != ")":
        return False
    depth = 0
    pairs = {"(": ")", "{": "}", "[": "]"}
    closers = set(pairs.values())
    for i, char in enumerate(text):
        if char in pairs:
            depth += 1
        elif char in closers:
            depth -= 1
            if depth == 0 and i != len(text) - 1:
                return False
    return depth == 0


def _split_top_level(text: str, operator: str) -> tuple[str, str] | None:
    depth = 0
    pairs = {"(": ")", "{": "}", "[": "]"}
    closers = set(pairs.values())
    i = 0
    while i <= len(text) - len(operator):
        char = text[i]
        if char in pairs:
            depth += 1
            i += 1
            continue
        if char in closers:
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and text.startswith(operator, i):
            return text[:i].strip(), text[i + len(operator) :].strip()
        i += 1
    return None


def _split_quantifier(text: str) -> tuple[str, str] | None:
    depth = 0
    for i, char in enumerate(text):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            return text[:i].strip(), text[i + 1 :].strip()
    return None


def _split_type(text: str) -> tuple[str, str] | None:
    depth = 0
    for i, char in enumerate(text):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        elif char == ":" and depth == 0:
            return text[:i].strip(), text[i + 1 :].strip()
    return None


def _top_level_terms(text: str) -> list[str]:
    terms: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        if char.isspace() and depth == 0:
            if current:
                terms.append("".join(current).strip())
                current = []
        else:
            current.append(char)
    if current:
        terms.append("".join(current).strip())
    return [term for term in terms if term]


def _application_parts(text: str) -> tuple[str, list[str]] | None:
    terms = _top_level_terms(text)
    if len(terms) < 2:
        return None
    head, args = terms[0], terms[1:]
    if not re.fullmatch(r"[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_'.α-ωΑ-Ω]*", head):
        return None
    if any(term in _INFIX_TOKENS for term in args):
        return None
    if any(term[:1] in {"+", "-", "*", "/", "^", "=", "<", ">", "×"} for term in args):
        return None
    return head, args


def parse_type_expr(text: str) -> dict[str, Any]:
    """Parse a conservative display-only type surface.

    This is not a Lean AST. It is a deterministic presentation tree over a
    pretty/source type surface, with enough structure to typeset common binders.
    """
    value = normalize_surface(text)
    if not value:
        return {"node": "type_atom", "text": ""}
    if _balanced_outer(value):
        return {"node": "type_group", "body": parse_type_expr(value[1:-1])}
    # Function arrows are right-associative because we split the first top-level arrow.
    split = _split_top_level(value, "→")
    if split and split[0] and split[1]:
        return {
            "node": "type_arrow",
            "domain": parse_type_expr(split[0]),
            "codomain": parse_type_expr(split[1]),
        }
    split = _split_top_level(value, "×")
    if split and split[0] and split[1]:
        return {
            "node": "type_product",
            "left": parse_type_expr(split[0]),
            "right": parse_type_expr(split[1]),
        }
    application = _application_parts(value)
    if application:
        head, args = application
        return {
            "node": "type_app",
            "head": head,
            "args": [parse_type_expr(arg) for arg in args],
        }
    return {"node": "type_atom", "text": value}


def split_claim_declaration(declaration: str) -> tuple[str, str, str]:
    match = re.search(r"\b(?:theorem|lemma)\s+([^\s:(]+)", declaration)
    if not match:
        raise ValueError("presentation IR requires a theorem or lemma declaration")
    name = match.group(1)
    rest = declaration[match.end() :].strip()
    depth = 0
    for i, char in enumerate(rest):
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth = max(0, depth - 1)
        elif char == ":" and depth == 0:
            proposition = rest[i + 1 :].strip()
            if proposition.endswith(":="):
                proposition = proposition[:-2].rstrip()
            return name, rest[:i].strip(), proposition
    raise ValueError("could not locate the proposition in the formal declaration")


def _binder_groups(segment: str) -> list[str]:
    groups: list[str] = []
    i = 0
    pairs = {"(": ")", "{": "}", "[": "]"}
    while i < len(segment):
        if segment[i].isspace():
            i += 1
            continue
        opener = segment[i]
        if opener in pairs:
            closer = pairs[opener]
            depth = 0
            for j in range(i, len(segment)):
                if segment[j] == opener:
                    depth += 1
                elif segment[j] == closer:
                    depth -= 1
                    if depth == 0:
                        groups.append(segment[i : j + 1])
                        i = j + 1
                        break
            else:
                groups.append(segment[i:])
                break
            continue
        j = i
        while j < len(segment) and not segment[j].isspace():
            j += 1
        groups.append(segment[i:j])
        i = j
    return [group for group in groups if group.strip()]


def parse_binder(raw: str, *, style: str | None = None) -> dict[str, Any]:
    value = normalize_surface(raw)
    inferred = style or "explicit"
    inner = value
    if value.startswith("(") and value.endswith(")"):
        inferred, inner = "explicit", value[1:-1].strip()
    elif value.startswith("{") and value.endswith("}"):
        inferred, inner = "implicit", value[1:-1].strip()
    elif value.startswith("[") and value.endswith("]"):
        inferred, inner = "instance", value[1:-1].strip()
    split = _split_type(inner)
    if split:
        names_part, type_part = split
        names = [name for name in names_part.split() if name]
        normalized_type = normalize_surface(type_part)
        return {
            "node": "binder",
            "style": inferred,
            "names": names,
            "type": normalized_type,
            "type_ir": parse_type_expr(normalized_type),
            "raw": value,
        }
    constraint = normalize_surface(inner)
    return {
        "node": "binder",
        "style": inferred,
        "names": [],
        "type": "",
        "raw": value,
        "constraint": constraint,
        "constraint_ir": parse_type_expr(constraint) if constraint else None,
    }


def parse_expr(text: str) -> dict[str, Any]:
    value = normalize_surface(text)
    if not value:
        return {"node": "atom", "text": ""}
    if _balanced_outer(value):
        return {"node": "group", "body": parse_expr(value[1:-1])}
    if value[0:1] in {"∀", "∃"}:
        split = _split_quantifier(value[1:].strip())
        if split:
            binder, body = split
            return {
                "node": "quantifier",
                "quantifier": "forall" if value.startswith("∀") else "exists",
                "binder": parse_binder(binder, style="quantified"),
                "body": parse_expr(body),
            }
    for op in ("↔", "→", "∨", "∧", "≠", "≤", "≥", "∉", "⊆", "∈", "=", "<", ">"):
        split = _split_top_level(value, op)
        if split and split[0] and split[1]:
            return {
                "node": "binary",
                "operator": op,
                "left": parse_expr(split[0]),
                "right": parse_expr(split[1]),
            }
    application = _application_parts(value)
    if application:
        head, args = application
        return {
            "node": "app",
            "head": head,
            "args": [parse_expr(arg) for arg in args],
        }
    return {"node": "atom", "text": value}


def parse_declaration_ir(declaration: str) -> dict[str, Any]:
    name, binder_surface, proposition = split_claim_declaration(declaration)
    return {
        "version": IR_VERSION,
        "node": "claim",
        "lean_name": name,
        "binders": [parse_binder(group) for group in _binder_groups(binder_surface)],
        "body": parse_expr(proposition),
    }


def render_type_unicode(node: dict[str, Any]) -> str:
    kind = node.get("node")
    if kind == "type_arrow":
        return f"{render_type_unicode(node['domain'])} → {render_type_unicode(node['codomain'])}"
    if kind == "type_product":
        return f"{render_type_unicode(node['left'])} × {render_type_unicode(node['right'])}"
    if kind == "type_app":
        args = ", ".join(render_type_unicode(arg) for arg in node.get("args") or [])
        return f"{type_label(str(node.get('head') or ''))}({args})"
    if kind == "type_group":
        return f"({render_type_unicode(node['body'])})"
    return normalize_surface(str(node.get("text") or ""))


def _unicode_binder(binder: dict[str, Any]) -> str:
    raw = normalize_surface(str(binder.get("raw") or ""))
    names = " ".join(binder.get("names") or [])
    type_ir = binder.get("type_ir")
    type_text = render_type_unicode(type_ir) if isinstance(type_ir, dict) else normalize_surface(str(binder.get("type") or ""))
    style = binder.get("style")
    if names and type_text:
        core = f"{names} : {type_text}"
        if style == "implicit":
            return "{" + core + "}"
        if style == "instance":
            return "[" + core + "]"
        if style == "quantified":
            return core
        return "(" + core + ")"
    if style == "instance" and binder.get("constraint"):
        constraint_ir = binder.get("constraint_ir")
        rendered = render_type_unicode(constraint_ir) if isinstance(constraint_ir, dict) else normalize_surface(str(binder["constraint"]))
        return "[" + rendered + "]"
    return raw


def render_unicode(node: dict[str, Any]) -> str:
    kind = node.get("node")
    if kind == "claim":
        body = render_unicode(node["body"])
        binders = [_unicode_binder(binder) for binder in node.get("binders") or []]
        return normalize_surface(f"∀ {' '.join(binders)}, {body}" if binders else body)
    if kind == "quantifier":
        q = "∀" if node.get("quantifier") == "forall" else "∃"
        return normalize_surface(f"{q} {_unicode_binder(node['binder'])}, {render_unicode(node['body'])}")
    if kind == "binary":
        return normalize_surface(f"{render_unicode(node['left'])} {node.get('operator')} {render_unicode(node['right'])}")
    if kind == "app":
        args = ", ".join(render_unicode(arg) for arg in node.get("args") or [])
        return f"{app_label(str(node.get('head') or ''))}({args})"
    if kind == "group":
        return f"({render_unicode(node['body'])})"
    return normalize_surface(str(node.get("text") or ""))


def _latex_identifier(text: str) -> str:
    if text in _DOMAIN_LATEX:
        return _DOMAIN_LATEX[text]
    if text in _GREEK_LATEX:
        return _GREEK_LATEX[text]
    if text in {"Type", "Prop", "Sort"}:
        return r"\mathrm{" + text + "}"
    if "." in text and re.fullmatch(r"[A-Za-z][A-Za-z0-9_'.]*(?:\.[A-Za-z][A-Za-z0-9_'.]*)+", text):
        return r"\operatorname{" + text.replace("_", r"\_") + "}"
    return text.replace("_", r"\_")


def _latex_atom(text: str) -> str:
    value = normalize_surface(text)
    for source, target in _DOMAIN_LATEX.items():
        if source in _DOMAIN_UNICODE:
            value = re.sub(
                rf"\b{re.escape(source)}\b(?!\.)",
                lambda _m, replacement=target: replacement,
                value,
            )
        else:
            value = value.replace(source, target)
    for source, target in _GREEK_LATEX.items():
        value = value.replace(source, target)
    replacements = {
        "↔": r"\Longleftrightarrow",
        "→": r"\longrightarrow",
        "∧": r"\land",
        "∨": r"\lor",
        "¬": r"\neg\,",
        "≤": r"\le",
        "≥": r"\ge",
        "≠": r"\ne",
        "∈": r"\in",
        "∉": r"\notin",
        "⊆": r"\subseteq",
        "∪": r"\cup",
        "∩": r"\cap",
        "∅": r"\varnothing",
        "∘": r"\circ",
        "×": r"\times",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    value = re.sub(r"\bType\b", r"\\mathrm{Type}", value)
    value = re.sub(r"\bProp\b", r"\\mathrm{Prop}", value)
    value = re.sub(
        r"\b([A-Za-z][A-Za-z0-9_']*(?:\.[A-Za-z][A-Za-z0-9_']*)+)\b",
        lambda match: r"\operatorname{" + match.group(1).replace("_", r"\_") + "}",
        value,
    )
    value = re.sub(r"\^\s*([0-9]+)\b", lambda match: "^{" + match.group(1) + "}", value)
    return re.sub(r"\s+", " ", value).strip()


def _latex_type_head(head: str) -> str:
    label = type_label(head).replace("_", r"\_")
    if re.fullmatch(r"[A-Za-z]+", label):
        return r"\mathrm{" + label + "}"
    return _latex_identifier(label)


def render_type_latex(node: dict[str, Any]) -> str:
    kind = node.get("node")
    if kind == "type_arrow":
        domain = render_type_latex(node["domain"])
        codomain = render_type_latex(node["codomain"])
        return domain + r"\,\to\," + codomain
    if kind == "type_product":
        return render_type_latex(node["left"]) + r"\,\times\," + render_type_latex(node["right"])
    if kind == "type_app":
        args = r",\,".join(render_type_latex(arg) for arg in node.get("args") or [])
        return _latex_type_head(str(node.get("head") or "")) + r"\!\left(" + args + r"\right)"
    if kind == "type_group":
        return r"\left(" + render_type_latex(node["body"]) + r"\right)"
    return _latex_identifier(normalize_surface(str(node.get("text") or "")))


def _simple_domain_type(node: Any) -> str | None:
    if not isinstance(node, dict) or node.get("node") != "type_atom":
        return None
    text = normalize_surface(str(node.get("text") or ""))
    return _DOMAIN_LATEX.get(text)


def _latex_binder(binder: dict[str, Any]) -> str:
    names = binder.get("names") or []
    style = binder.get("style")
    type_ir = binder.get("type_ir")
    if names and isinstance(type_ir, dict):
        rendered_names = ",".join(_latex_identifier(name) for name in names)
        domain = _simple_domain_type(type_ir)
        if domain:
            core = rendered_names + r" \in " + domain
        else:
            core = rendered_names + r" : " + render_type_latex(type_ir)
        if style == "implicit":
            return r"\{" + core + r"\}"
        if style == "instance":
            return r"[" + core + r"]"
        return core
    constraint_ir = binder.get("constraint_ir")
    if style == "instance" and isinstance(constraint_ir, dict):
        return r"[" + render_type_latex(constraint_ir) + r"]"
    return _latex_atom(str(binder.get("raw") or ""))


def _latex_app_head(head: str) -> str:
    label = app_label(head).replace("_", r"\_")
    if re.fullmatch(r"[A-Za-z]+", label):
        return r"\operatorname{" + label + "}"
    return _latex_identifier(label)


def render_latex(node: dict[str, Any]) -> str:
    kind = node.get("node")
    if kind == "claim":
        body = render_latex(node["body"])
        binders = node.get("binders") or []
        if not binders:
            return body
        return r"\forall\," + r"\;".join(_latex_binder(binder) for binder in binders) + r",\quad " + body
    if kind == "quantifier":
        q = r"\forall" if node.get("quantifier") == "forall" else r"\exists"
        return q + r"\," + _latex_binder(node["binder"]) + r",\quad " + render_latex(node["body"])
    if kind == "binary":
        op = _OP_LATEX.get(str(node.get("operator")), str(node.get("operator") or ""))
        return render_latex(node["left"]) + r"\;" + op + r"\;" + render_latex(node["right"])
    if kind == "app":
        args = r",\,".join(render_latex(arg) for arg in node.get("args") or [])
        return _latex_app_head(str(node.get("head") or "")) + r"\!\left(" + args + r"\right)"
    if kind == "group":
        return r"\left(" + render_latex(node["body"]) + r"\right)"
    return _latex_atom(str(node.get("text") or ""))


def canonical_from_declaration(declaration: str) -> dict[str, Any]:
    ir = parse_declaration_ir(declaration)
    return {
        "lean_name": ir["lean_name"],
        "text": render_unicode(ir),
        "latex": render_latex(ir),
        "ir": ir,
        "renderer_version": IR_VERSION,
    }
