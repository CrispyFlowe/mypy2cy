#!/usr/bin/env python3
# mypy: disable-error-code="no-untyped-call"
# mypy: disable-error-code=arg-type


import re
import sys
from pathlib import Path
import argparse
from typing import (
    Type, TypeVar, Callable, Union
)

TYPE_MAP = {
    "int": "int",
    "float": "float",
    "str": "str",
    "bool": "bint",
    "bytes": "bytes",
    "list": "list",
    "tuple": "tuple",
    "dict": "dict",
    "set": "set",
    "None": "void",
    "Any": "object",
}

import ast

_T = TypeVar("_T")

gen_allow = False

def type_params(*args: str) -> Callable[[Type[_T]], Type[_T]]:
    def wrapper(cls: Type[_T]) -> Type[_T]:
        setattr(cls, "__type_params__", list(args))
        return cls
    return wrapper


def extract_convert_type_mappings(path: str) -> dict[str, tuple[str, list[str]]]:
    """Extract @convert_type and @type_params mappings from file."""
    mappings: dict[str, tuple[str, list[str]]] = {}
    with open(path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            template = None
            params = []
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call):
                    if getattr(deco.func, 'id', None) == "convert_type":
                        if len(deco.args) == 1 and isinstance(deco.args[0], ast.Constant):
                            template = deco.args[0].value
                    elif getattr(deco.func, 'id', None) == "type_params":
                        for arg in deco.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                params.append(arg.value)
            if template:
                mappings[node.name] = (template, params)
    return mappings

CUSTOM_TYPE_MAP: dict[str, tuple[str, list[str]]] = {}

def map_type(pytype: str) -> str:

    base = pytype.strip().replace(" ", "")
    if base.isdigit():
        return base
    
    def unwrap_literal(typ: str) -> str:
        while typ.startswith("Literal[") and typ.endswith("]"):
            typ = typ[8:-1]
        return typ

    def try_custom_template(name: str, args: list[str]) -> str | None:
        """Apply template substitution from @convert_type and @type_params."""
        if name not in CUSTOM_TYPE_MAP:
            return None
        template, param_keys = CUSTOM_TYPE_MAP[name]
        if len(param_keys) != len(args):
            print(f"[!] Parameter count mismatch for '{name}'", file=sys.stderr)
            return None
        substitutions = {}
        for key, val in zip(param_keys, args):
            unwrapped = unwrap_literal(val.strip())
            substitutions[key] = map_type(unwrapped)
        for key, val in substitutions.items():
            template = template.replace("{" + key + "}", val)
        return template

    match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\[(.*)\]$', base)
    if match:
        typename, args_str = match.groups()
        args = [a.strip() for a in args_str.split(',')]
        substituted = try_custom_template(typename, args)
        if substituted:
            return substituted

        if typename == "tuple":
            return f"({', '.join(map_type(a) for a in args)})"
        elif False and typename == "list":
            # deprecated since cython memoryviews are not directly
            # equivalent to python lists
            return f"{map_type(unwrap_literal(args[0]))}[:]"
        elif (typename in ('tuple', 'set', 'list', 'dict')) or gen_allow:
            return f"{typename}[{map_type(unwrap_literal(args[0]))}]"

    if base in CUSTOM_TYPE_MAP:
        template, param_keys = CUSTOM_TYPE_MAP[base]
        if param_keys:
            print(f"[!] Expected parameters for '{base}', but none provided", file=sys.stderr)
        return template

    base = unwrap_literal(base)
    outer = re.match(r'^([a-zA-Z_][a-zA-Z0-9_\\.]*)$', base)
    key = outer.group(1) if outer else None
    result = TYPE_MAP.get(key) or key or "object" # type:ignore

    if result == "object":
        print(f"[!] Unrecognized type '{pytype}', defaulting to object", file=sys.stderr)
    return result

# Other functions and main() remain unchanged


def convert_variable_declaration(line: str) -> str:
    pattern = re.compile(
        r'^(\s*)'
        r'(?P<var>[a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*'
        r'(?P<type>[a-zA-Z_][a-zA-Z0-9_\[\], \.]*)'
        r'(?P<assign>\s*=\s*[^#\n]*)?'
        r'(?P<comment>\s*#.*)?$'
    )
    match = pattern.match(line)
    if not match:
        return line

    indent = match.group(1)
    var = match.group("var")
    typ = map_type(match.group("type"))
    assign = match.group("assign") or ""
    comment = match.group("comment") or ""

    return f"{indent}cdef {typ} {var} {assign}{comment}"

def convert_function_definition(merged_line: str) -> str:
    pattern = re.compile(
        r'^(\s*)def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'
        r'\((.*?)\)\s*(->\s*([a-zA-Z_][a-zA-Z0-9_\[\], \.]*))?\s*:$'
    )
    match = pattern.match(merged_line)
    if not match:
        return merged_line

    indent, fname, params, _, ret_type = match.groups()
    ret_cytype = map_type(ret_type) if ret_type else "void"

    param_list = []
    for param in params.split(","):
        param = param.strip()
        if not param:
            continue
        if ":" in param:
            pname, ptype = map(str.strip, param.split(":", 1))
            if pname == "self":
                param_list.append("self")
            else:
                param_list.append(f"{map_type(ptype)} {pname}")
        else:
            param_list.append(param)

    return f"{indent}cpdef {ret_cytype} {fname}({', '.join(param_list)}):"

def file_level_enabled(lines) -> bool:
    for line in lines[:3]:
        if "use-cython" in line:
            return True
    return False

def convert_typing_to_cython(source: str, force_global: bool = False) -> str:
    lines = source.splitlines()
    result = []
    i = 0
    in_block = False
    file_wide = force_global or file_level_enabled(lines)
    next_line_compiled = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped == "@compile":
            next_line_compiled = True
            result.append(line)
            i += 1
            continue

        if stripped.startswith("#") and "cython-begin" in stripped:
            in_block = True
            result.append(line)
            i += 1
            continue
        if stripped.startswith("#") and "cython-end" in stripped:
            in_block = False
            result.append(line)
            i += 1
            continue

        compile_now = file_wide or in_block or next_line_compiled
        next_line_compiled = False

        if stripped.startswith("@"):
            result.append(line)
            i += 1
            continue

        if stripped.startswith("def ") and compile_now:
            func_block = [line]
            while not line.strip().endswith(":") and i + 1 < len(lines):
                i += 1
                line = lines[i]
                func_block.append(line)
            merged = ' '.join(func_block)
            result.append(convert_function_definition(merged))
            i += 1
            continue

        if compile_now and re.match(r'^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*:', line):
            result.append(convert_variable_declaration(line))
        else:
            result.append(line)
        i += 1

    return "\n".join(result)

def process_single_file(input_path: Path, output_path: Path, dry_run: bool = False, global_mode: bool = False):
    with open(input_path, 'r', encoding='utf-8') as f:
        source = f.read()

    converted = convert_typing_to_cython(source, force_global=global_mode)

    if dry_run:
        print(f"--- Dry run for: {input_path} ---")
        print(converted)
        return

    if not output_path:
        output_path = input_path.with_suffix(".pyx")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(converted)

    print(f"[.] Converted file written to: {output_path}")

def process_path(input_path: Path, output: Union[Path, None] = None, dry_run: bool = False, global_mode: bool = False):
    if input_path.is_file():
        process_single_file(input_path, output, dry_run=dry_run, global_mode=global_mode) # type:ignore
    elif input_path.is_dir():
        for pyfile in input_path.glob("*.py"):
            out_file = pyfile.with_suffix(".pyx") if not output else output / pyfile.with_suffix(".pyx").name
            process_single_file(pyfile, out_file, dry_run=dry_run, global_mode=global_mode)
    else:
        print(f"[x] Path not found: {input_path}", file=sys.stderr)
        exit(1)

def main():
    parser = argparse.ArgumentParser(description="Convert Python typing to Cython cdef/cpdef declarations.")
    parser.add_argument("input", help="Input file or directory")
    parser.add_argument("-o", "--output", help="Output file or directory")
    parser.add_argument("-d", "--dry-run", action="store_true", help="Only print output, do not write to file")
    parser.add_argument("-g", "--global", dest="global_mode", action="store_true",
                        help="Force full-file conversion regardless of annotations")
    parser.add_argument("-t", "--types", nargs="*", default=[], help="Optional .py files defining @convert_type classes")

    args = parser.parse_args()

    # Load custom type mappings from annotation files
    if args.types:
        for type_file in args.types:
            CUSTOM_TYPE_MAP.update(extract_convert_type_mappings(type_file))
    else:
        try:
            with open(args.input, 'r', encoding='utf-8') as f:
                src = f.read()
            if 'from typingcy' in src or 'import typingcy' in src:
                typingcy_file = Path(args.input).parent / 'typingcy.py'
                if typingcy_file.exists():
                    CUSTOM_TYPE_MAP.update(extract_convert_type_mappings(str(typingcy_file)))
        except Exception as e:
            print(f"[!] Failed to auto-load typingcy.py: {e}", file=sys.stderr)


    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve() if args.output else None

    return transcribe(input_path, output_path, args.dry_run, args.global_mode)  # type:ignore

def convert_type(name: str) -> Callable[[Type[_T]], Type[_T]]:
    def wrapper(cls: Type[_T]) -> Type[_T]:
        return cls
    return wrapper

def transcribe(input: str, 
               output: str, 
               dry_run: bool = False, 
               global_mode: bool = False
) -> bool:
    try:
        process_path(Path(input), output=Path(output), dry_run=dry_run, global_mode=global_mode) # type:ignore
        return True
    except Exception as e:
        print(f"[x] {e}", file=sys.stderr)
        exit(1)

if __name__ == "__main__":
    main()
