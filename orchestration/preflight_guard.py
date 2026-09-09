"""
orchestration/preflight_guard.py — Pre-Flight Python AST & Syntax Verification Gate
Intercepts LLM generated diffs BEFORE Testing/Review to prevent broken syntax from propagating.
"""

from __future__ import annotations

import ast


def validate_python_code(source_code: str) -> tuple[bool, str | None]:
    """
    Validate pure Python source code using Python's native AST parser.
    Returns: (is_valid, error_message)
    """
    try:
        ast.parse(source_code)
        return True, None
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}"
    except Exception as e:
        return False, f"AST Parse Error: {str(e)}"


def extract_added_code_from_diff(code_diff: str) -> str:
    """
    Extract only newly added/modified lines from a unified diff hunk.
    """
    added_lines = []
    for line in code_diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])
    return "\n".join(added_lines)


def preflight_diff_check(code_diff: str, original_file_content: str = "") -> dict:
    """
    Perform a complete pre-flight structural & AST syntax verification on a code diff.
    Returns: {
        "passed": bool,
        "syntax_clean": bool,
        "diff_structured": bool,
        "error": str | None,
    }
    """
    if not code_diff or not code_diff.strip():
        return {
            "passed": False,
            "syntax_clean": False,
            "diff_structured": False,
            "error": "Empty code diff provided",
        }

    # Verify standard unified diff markers
    has_headers = ("---" in code_diff and "+++" in code_diff) or "@@" in code_diff
    if not has_headers and not code_diff.startswith("diff --git"):
        return {
            "passed": False,
            "syntax_clean": False,
            "diff_structured": False,
            "error": "Malformed diff: missing standard unified diff hunk markers (---, +++, @@)",
        }

    added_code = extract_added_code_from_diff(code_diff)

    # Determine if the target file is a Python file
    is_python = True  # Default to True just to be safe
    for line in code_diff.splitlines():
        if line.startswith("+++"):
            is_python = line.strip().endswith(".py")
            break

    # If the added code has full statements, test for blatant syntax errors
    # (e.g. unclosed parentheses, missing colons)
    if added_code.strip() and is_python:
        try:
            ast.parse(added_code)
            syntax_clean = True
            syntax_err = None
        except SyntaxError as e:
            syntax_clean = False
            syntax_err = f"SyntaxError in generated code patch at line {e.lineno}: {e.msg}"
        except Exception:
            syntax_clean = True
            syntax_err = None
    else:
        syntax_clean = True
        syntax_err = None

    return {
        "passed": syntax_clean,
        "syntax_clean": syntax_clean,
        "diff_structured": True,
        "error": syntax_err,
    }
