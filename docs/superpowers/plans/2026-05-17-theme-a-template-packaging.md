# Theme A — Template/Packaging Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace regex-based S3Key matching with YAML-parse-driven, stem-keyed, directory-aware Lambda packaging that eliminates false orphan errors, supports multi-file handlers, removes the `lambda/` subdir hardcode, and makes stem collisions impossible.

**Architecture:** A new shared `template_parser.py` parses CFN intrinsics-safe YAML to extract `{stem → raw_s3key}` pairs. A new `find_source_for_stem()` helper walks `deployment/**/` for a matching directory (preferred) or flat `.py` file. Both `_check_lambda_orphan` in `tools.py` and `_build_packaging_plan` / `_upload_initial_lambda_zips` in the runner use these helpers, replacing all regex-on-template-body patterns.

**Tech Stack:** Python 3.11, `pyyaml`, `zipfile`, `pytest`, `pytest-mock`

---

### Task 1: Create `harness/shared/template_parser.py`

**Files:**
- Create: `harness/shared/template_parser.py`
- Create: `tests/test_template_parser.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_template_parser.py`:

```python
import textwrap
import pytest
from harness.shared.template_parser import extract_s3key_stems


def _write_template(tmp_path, body: str) -> str:
    p = tmp_path / "faulted.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


def test_plain_s3key(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Bucket: bucket
                S3Key: handler.zip
    """)
    assert extract_s3key_stems(path) == {"handler": "handler.zip"}


def test_path_prefixed_s3key(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: lambdas/handler.zip
    """)
    assert extract_s3key_stems(path) == {"handler": "lambdas/handler.zip"}


def test_quoted_s3key(tmp_path):
    # YAML double-quotes are stripped by safe_load naturally
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: "lambdas/handler.zip"
    """)
    assert extract_s3key_stems(path) == {"handler": "lambdas/handler.zip"}


def test_sub_intrinsic_s3key(tmp_path):
    # !Sub scalar — no-op constructor returns the raw string value
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: !Sub "${Bucket}/handler.zip"
    """)
    result = extract_s3key_stems(path)
    assert "handler" in result


def test_multiple_lambdas(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          FnA:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: alpha.zip
          FnB:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: beta.zip
    """)
    assert extract_s3key_stems(path) == {"alpha": "alpha.zip", "beta": "beta.zip"}


def test_non_zip_s3key_ignored(tmp_path):
    path = _write_template(tmp_path, """
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: handler.jar
    """)
    assert extract_s3key_stems(path) == {}


def test_missing_file_returns_empty():
    assert extract_s3key_stems("/does/not/exist.yaml") == {}


def test_malformed_yaml_returns_empty(tmp_path):
    path = tmp_path / "faulted.yaml"
    path.write_text(": this is not valid yaml: [\n")
    assert extract_s3key_stems(str(path)) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/shubhan/ACEDebugging-benchmark/.claude/worktrees/eval-runner-audit
pytest tests/test_template_parser.py -v
```

Expected: `ModuleNotFoundError: No module named 'harness.shared.template_parser'`

- [ ] **Step 3: Implement `harness/shared/template_parser.py`**

Create `harness/shared/template_parser.py`:

```python
import os
import yaml


_CFN_TAGS = [
    "!Sub", "!Ref", "!GetAtt", "!Join", "!Select", "!If",
    "!FindInMap", "!Base64", "!Cidr", "!Split", "!ImportValue",
    "!Transform", "!And", "!Or", "!Not", "!Equals",
]


def _cfn_safe_loader() -> type:
    """Return a yaml.SafeLoader subclass with no-op CFN intrinsic constructors."""
    class _Loader(yaml.SafeLoader):
        pass

    def _noop(loader, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node, deep=True)
        return loader.construct_mapping(node, deep=True)

    for tag in _CFN_TAGS:
        _Loader.add_constructor(tag, _noop)

    return _Loader


def extract_s3key_stems(template_path: str) -> dict[str, str]:
    """Parse a CloudFormation template and return {stem: raw_s3key} for all
    Lambda S3Key entries whose value ends in '.zip'.

    Handles plain scalars, path-prefixed keys ('lambdas/foo.zip'), YAML-quoted
    values, and CFN intrinsic tags (!Sub, !Ref, etc.) via no-op constructors.
    Returns {} on any parse or I/O error.
    """
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            doc = yaml.load(f, Loader=_cfn_safe_loader())
    except Exception:
        return {}

    if not isinstance(doc, dict):
        return {}

    stems: dict[str, str] = {}
    for resource in doc.get("Resources", {}).values():
        if not isinstance(resource, dict):
            continue
        props = resource.get("Properties", {})
        if not isinstance(props, dict):
            continue
        code = props.get("Code", {})
        raw_key = (code.get("S3Key") if isinstance(code, dict) else None) or props.get("S3Key")
        if not isinstance(raw_key, str):
            continue
        raw_key = raw_key.strip("\"'")
        if not raw_key.endswith(".zip"):
            continue
        stem = os.path.splitext(os.path.basename(raw_key))[0]
        if stem:
            stems[stem] = raw_key

    return stems
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_template_parser.py -v
```

Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add harness/shared/template_parser.py tests/test_template_parser.py
git commit -m "feat(shared): add template_parser with YAML-safe S3Key stem extraction"
```

---

### Task 2: Extend `LambdaUpload` with `source_path` and `is_dir`

**Files:**
- Modify: `harness/shared/types.py`

- [ ] **Step 1: Add fields to `LambdaUpload`**

In `harness/shared/types.py`, update the `LambdaUpload` dataclass (currently lines 20–27):

```python
@dataclass
class LambdaUpload:
    """One Lambda package queued for a submission."""
    rel_path: str            # e.g. "lambda/handler.py" or "lambda/handler"
    stem: str                # e.g. "handler"
    s3_key_original: str     # e.g. "handler.zip"
    s3_key_new: str          # e.g. "lambdas/<run>/<sha>/handler.zip"
    sha256: str
    arcname: str             # e.g. "index.py" (derived from Handler; "" for dir zips)
    source_path: str = ""    # absolute path to the source file or directory
    is_dir: bool = False     # True when source_path is a directory
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
pytest tests/test_runner.py tests/test_agent_loop.py -v
```

Expected: all existing tests PASS (new fields have defaults, no callers break)

- [ ] **Step 3: Commit**

```bash
git add harness/shared/types.py
git commit -m "feat(types): add source_path and is_dir fields to LambdaUpload"
```

---

### Task 3: Update `_check_lambda_orphan` in `harness/agent/tools.py`

**Files:**
- Modify: `harness/agent/tools.py`
- Modify: `tests/test_agent_loop.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_agent_loop.py` (after `test_write_file_blocked_outside_deployment`):

```python
def test_write_file_orphan_check_uses_yaml_parse(tmp_path):
    """write_file accepts a path whose stem appears as a YAML-parsed S3Key."""
    (tmp_path / "faulted.yaml").write_text(
        "Resources:\n"
        "  Fn:\n"
        "    Type: AWS::Lambda::Function\n"
        "    Properties:\n"
        "      Code:\n"
        '        S3Key: "lambdas/handler.zip"\n'  # quoted — old regex would miss this
    )
    (tmp_path / "deployment" / "lambda").mkdir(parents=True)
    result = dispatch_file_tool(
        "write_file",
        {"path": "deployment/lambda/handler.py", "content": "# fix\n"},
        str(tmp_path),
    )
    assert result.startswith("Written"), f"Unexpected: {result}"


def test_write_file_orphan_check_sub_intrinsic(tmp_path):
    """write_file accepts a write whose stem appears inside a !Sub S3Key."""
    (tmp_path / "faulted.yaml").write_text(
        "Resources:\n"
        "  Fn:\n"
        "    Type: AWS::Lambda::Function\n"
        "    Properties:\n"
        "      Code:\n"
        "        S3Key: !Sub '${Bucket}/processor.zip'\n"
    )
    (tmp_path / "deployment" / "lambda").mkdir(parents=True)
    result = dispatch_file_tool(
        "write_file",
        {"path": "deployment/lambda/processor.py", "content": "# fix\n"},
        str(tmp_path),
    )
    assert result.startswith("Written"), f"Unexpected: {result}"


def test_write_file_orphan_check_dir_write_accepted(tmp_path):
    """write_file accepts a write to deployment/lambda/<stem>/file.py when stem in template."""
    (tmp_path / "faulted.yaml").write_text(
        "Resources:\n"
        "  Fn:\n"
        "    Type: AWS::Lambda::Function\n"
        "    Properties:\n"
        "      Code:\n"
        "        S3Key: worker.zip\n"
    )
    (tmp_path / "deployment" / "lambda" / "worker").mkdir(parents=True)
    result = dispatch_file_tool(
        "write_file",
        {"path": "deployment/lambda/worker/utils.py", "content": "# helper\n"},
        str(tmp_path),
    )
    assert result.startswith("Written"), f"Unexpected: {result}"


def test_write_file_orphan_check_unknown_stem_rejected(tmp_path):
    """write_file rejects a deployment/lambda/*.py whose stem is absent from template."""
    (tmp_path / "faulted.yaml").write_text(
        "Resources:\n"
        "  Fn:\n"
        "    Type: AWS::Lambda::Function\n"
        "    Properties:\n"
        "      Code:\n"
        "        S3Key: handler.zip\n"
    )
    (tmp_path / "deployment" / "lambda").mkdir(parents=True)
    result = dispatch_file_tool(
        "write_file",
        {"path": "deployment/lambda/ghost.py", "content": "# oops\n"},
        str(tmp_path),
    )
    assert result.startswith("Error:"), f"Expected error, got: {result}"
    assert "ghost" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_agent_loop.py::test_write_file_orphan_check_uses_yaml_parse \
       tests/test_agent_loop.py::test_write_file_orphan_check_sub_intrinsic \
       tests/test_agent_loop.py::test_write_file_orphan_check_dir_write_accepted \
       tests/test_agent_loop.py::test_write_file_orphan_check_unknown_stem_rejected -v
```

Expected: 3 FAIL (quoted, Sub, dir writes incorrectly rejected by old regex), 1 may pass

- [ ] **Step 3: Replace `_check_lambda_orphan` in `harness/agent/tools.py`**

Replace the entire `_check_lambda_orphan` function (lines 108–139):

```python
def _check_lambda_orphan(rel_path: str, scenario_root: pathlib.Path) -> str | None:
    """Return an error message if rel_path writes to deployment/ with no matching
    S3Key stem in faulted.yaml; return None if the write is permitted.

    Accepts writes where any path component (directory name or file stem) matches
    a known stem from the YAML-parsed template, enabling both flat-file and
    directory-based Lambda package layouts.
    """
    from harness.shared.template_parser import extract_s3key_stems

    norm = rel_path.replace("\\", "/")
    if not norm.startswith("deployment/") or not norm.endswith(".py"):
        return None
    template_path = scenario_root / "faulted.yaml"
    if not template_path.exists():
        return None
    stems = extract_s3key_stems(str(template_path))
    if not stems:
        return None

    # Accept the write if any component of the path (dir name or file stem)
    # matches a known package stem.
    parts = norm[len("deployment/"):].split("/")
    for part in parts:
        candidate = os.path.splitext(part)[0] if part.endswith(".py") else part
        if candidate in stems:
            return None

    return (
        f"Error: no matching S3Key found for write to {rel_path}. "
        f"Available stems from faulted.yaml: {sorted(stems.keys())}. "
        f"Either rename your file/directory to match one of these stems, "
        f"or edit faulted.yaml to add an S3Key for your target stem."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_agent_loop.py -v
```

Expected: all tests PASS including the 4 new ones

- [ ] **Step 5: Commit**

```bash
git add harness/agent/tools.py tests/test_agent_loop.py
git commit -m "fix(tools): rewrite _check_lambda_orphan using YAML-parsed S3Key stems"
```

---

### Task 4: Add `find_source_for_stem` + `_zip_dir` + update `_build_packaging_plan` + `handle_submission`

**Files:**
- Modify: `harness/runner/deployment_handler.py`
- Modify: `tests/test_runner.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_runner.py` (after the existing `TestHandleSubmission` class):

```python
# ---------------------------------------------------------------------------
# template_parser integration — find_source_for_stem
# ---------------------------------------------------------------------------

from harness.runner.deployment_handler import find_source_for_stem, _zip_dir


def test_find_source_for_stem_prefers_directory(tmp_path):
    """find_source_for_stem returns the directory when both dir and .py exist."""
    d = tmp_path / "lambda" / "handler"
    d.mkdir(parents=True)
    (d / "handler.py").write_text("# handler")
    (tmp_path / "lambda" / "handler.py").write_text("# flat")
    path, is_dir = find_source_for_stem(str(tmp_path), "handler")
    assert is_dir is True
    assert path == str(d)


def test_find_source_for_stem_flat_fallback(tmp_path):
    """find_source_for_stem falls back to flat .py when no directory matches."""
    (tmp_path / "lambda").mkdir()
    flat = tmp_path / "lambda" / "handler.py"
    flat.write_text("# flat handler")
    path, is_dir = find_source_for_stem(str(tmp_path), "handler")
    assert is_dir is False
    assert path == str(flat)


def test_find_source_for_stem_returns_none_when_missing(tmp_path):
    path, is_dir = find_source_for_stem(str(tmp_path), "nonexistent")
    assert path is None
    assert is_dir is False


def test_find_source_for_stem_non_lambda_subdir(tmp_path):
    """find_source_for_stem finds sources outside the lambda/ subdir."""
    d = tmp_path / "glue" / "etl"
    d.mkdir(parents=True)
    (d / "etl.py").write_text("# glue job")
    path, is_dir = find_source_for_stem(str(tmp_path), "etl")
    assert is_dir is True
    assert path == str(d)


def test_zip_dir_includes_all_files(tmp_path):
    """_zip_dir zips all files in a directory preserving relative paths."""
    import zipfile as zf
    d = tmp_path / "handler"
    d.mkdir()
    (d / "handler.py").write_text("# main")
    (d / "utils.py").write_text("# helper")
    sub = d / "models"
    sub.mkdir()
    (sub / "schema.py").write_text("# schema")
    zip_bytes = _zip_dir(str(d))
    with zf.ZipFile(__import__("io").BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
    assert "handler.py" in names
    assert "utils.py" in names
    assert "models/schema.py" in names


def test_build_packaging_plan_uses_dir_source(tmp_path):
    """_build_packaging_plan creates a dir-based LambdaUpload when source is a directory."""
    import textwrap
    from harness.runner.deployment_handler import _build_packaging_plan

    template = tmp_path / "faulted.yaml"
    template.write_text(textwrap.dedent("""
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Handler: handler.lambda_handler
              Code:
                S3Key: handler.zip
    """))
    d = tmp_path / "lambda" / "handler"
    d.mkdir(parents=True)
    (d / "handler.py").write_text("# main")
    diff = {
        "files_modified": [os.path.join("lambda", "handler", "handler.py")],
        "files_added": [],
        "per_file_line_changes": {},
    }
    plan = _build_packaging_plan(diff, str(template), str(tmp_path), "run-test")
    assert len(plan.uploads) == 1
    assert plan.uploads[0].is_dir is True
    assert plan.uploads[0].stem == "handler"
    assert plan.uploads[0].source_path == str(d)


def test_build_packaging_plan_stem_collision_raises(tmp_path):
    """_build_packaging_plan raises ValueError when two stems resolve to the same source."""
    import textwrap
    from harness.runner.deployment_handler import _build_packaging_plan

    template = tmp_path / "faulted.yaml"
    template.write_text(textwrap.dedent("""
        Resources:
          FnA:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: handler.zip
          FnB:
            Type: AWS::Lambda::Function
            Properties:
              Code:
                S3Key: handler.zip
    """))
    d = tmp_path / "lambda" / "handler"
    d.mkdir(parents=True)
    (d / "handler.py").write_text("# main")
    diff = {
        "files_modified": [
            os.path.join("lambda", "handler", "handler.py"),
        ],
        "files_added": [],
        "per_file_line_changes": {},
    }
    # Two identical S3Keys produce the same stem; extract_s3key_stems deduplicates by
    # stem key, so collision only triggers when two different stems map to the same path.
    # Test the guard directly: inject two stems pointing at the same dir.
    # (In practice this happens if the template has handler.zip and Handler.zip.)
    # We test the guard by patching extract_s3key_stems to return a collision.
    import unittest.mock as mock
    with mock.patch(
        "harness.runner.deployment_handler.extract_s3key_stems",
        return_value={"handler": "handler.zip", "Handler": "Handler.zip"},
    ):
        diff2 = {
            "files_modified": [
                os.path.join("lambda", "handler", "handler.py"),
                os.path.join("lambda", "Handler", "Handler.py"),
            ],
            "files_added": [],
            "per_file_line_changes": {},
        }
        # Both stems map to the same source path → ValueError
        with pytest.raises(ValueError, match="Stem collision"):
            _build_packaging_plan(diff2, str(template), str(tmp_path), "run-col")


def test_build_packaging_plan_glue_subdir(tmp_path):
    """_build_packaging_plan packages a source outside deployment/lambda/."""
    import textwrap
    from harness.runner.deployment_handler import _build_packaging_plan

    template = tmp_path / "faulted.yaml"
    template.write_text(textwrap.dedent("""
        Resources:
          GlueJob:
            Type: AWS::Glue::Job
            Properties:
              Code:
                S3Key: etl.zip
    """))
    d = tmp_path / "glue" / "etl"
    d.mkdir(parents=True)
    (d / "etl.py").write_text("# glue")
    diff = {
        "files_modified": [os.path.join("glue", "etl", "etl.py")],
        "files_added": [],
        "per_file_line_changes": {},
    }
    plan = _build_packaging_plan(diff, str(template), str(tmp_path), "run-glue")
    assert len(plan.uploads) == 1
    assert plan.uploads[0].stem == "etl"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_runner.py::test_find_source_for_stem_prefers_directory \
       tests/test_runner.py::test_zip_dir_includes_all_files \
       tests/test_runner.py::test_build_packaging_plan_uses_dir_source \
       tests/test_runner.py::test_build_packaging_plan_glue_subdir -v
```

Expected: `ImportError` or `AssertionError` — functions don't exist yet

- [ ] **Step 3: Add `find_source_for_stem` and `_zip_dir` to `deployment_handler.py`**

Add the following after the existing `_zip_file` function (after line ~60):

```python
def _zip_dir(dir_path: str) -> bytes:
    """Zip all files under dir_path, preserving relative paths within the dir."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirnames, filenames in os.walk(dir_path):
            for filename in sorted(filenames):
                abs_file = os.path.join(dirpath, filename)
                arcname = os.path.relpath(abs_file, dir_path)
                zf.write(abs_file, arcname=arcname)
    return buf.getvalue()


def find_source_for_stem(deployment_dir: str, stem: str) -> tuple[str | None, bool]:
    """Return (abs_path, is_dir) for the Lambda source matching `stem`.

    Searches deployment_dir recursively. Prefers a subdirectory named `stem`
    (directory-based packaging) over a flat `stem.py` file (legacy layout).
    Returns (None, False) if no match is found.
    """
    deployment_dir = os.path.abspath(deployment_dir)
    # First pass: directory named stem
    for dirpath, dirnames, _filenames in os.walk(deployment_dir):
        for d in dirnames:
            if d == stem:
                return os.path.join(dirpath, d), True
    # Second pass: flat <stem>.py file
    for dirpath, _dirnames, filenames in os.walk(deployment_dir):
        for f in filenames:
            if f == stem + ".py":
                return os.path.join(dirpath, f), False
    return None, False
```

Also add the import at the top of `deployment_handler.py`:

```python
from harness.shared.template_parser import extract_s3key_stems
```

- [ ] **Step 4: Replace `_build_packaging_plan`**

Replace the entire `_build_packaging_plan` function:

```python
def _path_to_package_stem(rel_path: str, known_stems: set[str]) -> str | None:
    """Return the package stem for a changed file path, or None if not in known_stems.

    Handles both flat layout ('lambda/handler.py' → 'handler') and directory
    layout ('lambda/handler/utils.py' → 'handler').
    """
    import pathlib as _pathlib
    norm = rel_path.replace("\\", "/")
    parts = _pathlib.PurePosixPath(norm).parts
    for part in parts:
        candidate = _pathlib.PurePosixPath(part).stem if part.endswith(".py") else part
        if candidate in known_stems:
            return candidate
    return None


def _build_packaging_plan(diff: dict, template_path: str, deployment_dir: str, run_id: str) -> PackagingPlan:
    """Compute what to upload from a deployment diff.

    Uses YAML-parsed S3Key stems so quoted values and CFN intrinsics are handled
    correctly. Locates source packages (directory or flat .py) by stem under the
    full deployment_dir tree, not just deployment/lambda/.
    """
    stems = extract_s3key_stems(template_path)
    known_stems = set(stems.keys())

    affected_stems: set[str] = set()
    for rel_path in diff["files_modified"] + diff["files_added"]:
        stem = _path_to_package_stem(rel_path, known_stems)
        if stem:
            affected_stems.add(stem)

    plan = PackagingPlan()
    seen_source_paths: set[str] = set()

    with open(template_path, "r", encoding="utf-8") as _f:
        template_body_for_handler = _f.read()

    for stem in sorted(affected_stems):
        s3key_original = stems[stem]
        source_path, is_dir = find_source_for_stem(deployment_dir, stem)
        if source_path is None:
            plan.orphans.append(stem)
            continue

        abs_source = os.path.abspath(source_path)
        if abs_source in seen_source_paths:
            raise ValueError(
                f"Stem collision: '{stem}' resolves to source '{source_path}' "
                "which is already claimed by another stem."
            )
        seen_source_paths.add(abs_source)

        if is_dir:
            zip_bytes = _zip_dir(source_path)
            arcname = ""
        else:
            handler = find_handler_for_s3key(template_body_for_handler, s3key_original)
            arcname = handler_to_arcname(handler)
            zip_bytes = _zip_file(source_path, arcname=arcname)

        sha = hashlib.sha256(zip_bytes).hexdigest()[:12]
        rel_display = os.path.relpath(source_path, deployment_dir)
        plan.uploads.append(LambdaUpload(
            rel_path=rel_display.replace(os.sep, "/"),
            stem=stem,
            s3_key_original=s3key_original,
            s3_key_new=f"lambdas/{run_id}/{sha}/{stem}.zip",
            sha256=sha,
            arcname=arcname,
            source_path=source_path,
            is_dir=is_dir,
        ))

    if diff.get("per_file_line_changes", {}).get("faulted.yaml"):
        plan.template_changed = True

    return plan
```

- [ ] **Step 5: Update the upload step inside `handle_submission`**

In `handle_submission`, replace the `_zip_file` call in the upload loop (~line 139):

Old code:
```python
zip_bytes = _zip_file(
    os.path.join(deployment_dir, "lambda", os.path.basename(upload.rel_path)),
    arcname=upload.arcname,
)
```

New code:
```python
if upload.is_dir:
    zip_bytes = _zip_dir(upload.source_path)
else:
    zip_bytes = _zip_file(upload.source_path, arcname=upload.arcname)
```

Also update the call to `_build_packaging_plan` inside `handle_submission`. Change:

```python
plan = _build_packaging_plan(diff, template_body, deployment_dir, run_id)
```

to:

```python
plan = _build_packaging_plan(diff, template_path, deployment_dir, run_id)
```

(`template_path` is already defined earlier in `handle_submission`)

- [ ] **Step 6: Run all new and existing tests**

```bash
pytest tests/test_runner.py -v
```

Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add harness/runner/deployment_handler.py tests/test_runner.py
git commit -m "feat(runner): template-driven dir-aware Lambda packaging (breakpoints 4.2–4.5)"
```

---

### Task 5: Update `_upload_initial_lambda_zips` in `scenario_runner.py`

**Files:**
- Modify: `harness/runner/scenario_runner.py`
- Modify: `tests/test_runner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_runner.py`:

```python
def test_upload_initial_lambda_zips_uses_directory_source(tmp_path, mocker):
    """ScenarioRunner.start() uploads a dir-based Lambda zip at initial deploy."""
    import textwrap

    scenario_dir = tmp_path / "scenario"
    scenario_dir.mkdir()
    (scenario_dir / "scenario.md").write_text("symptom")
    (scenario_dir / "fault_manifest.json").write_text('{"architecture": "arch_01_test"}')
    template = scenario_dir / "faulted.yaml"
    template.write_text(textwrap.dedent("""
        AWSTemplateFormatVersion: '2010-09-09'
        Resources:
          Fn:
            Type: AWS::Lambda::Function
            Properties:
              Handler: handler.lambda_handler
              Code:
                S3Bucket: ace-bench-artifacts
                S3Key: handler.zip
    """))
    deployment = scenario_dir / "deployment"
    handler_dir = deployment / "lambda" / "handler"
    handler_dir.mkdir(parents=True)
    (handler_dir / "handler.py").write_text("def lambda_handler(e, c): return {}")
    (handler_dir / "utils.py").write_text("# helper")

    mock_s3 = MagicMock()
    mock_cf = MagicMock()
    mocker.patch("harness.runner.scenario_runner.s3_client", mock_s3)
    mocker.patch("harness.runner.scenario_runner.cf_client", mock_cf)
    mocker.patch("harness.runner.scenario_runner._ensure_artifact_bucket")
    mocker.patch("harness.runner.scenario_runner.init_run")

    runner = ScenarioRunner(str(scenario_dir), "run-init-test")
    runner._upload_initial_lambda_zips()

    assert mock_s3.put_object.called
    call_kwargs = mock_s3.put_object.call_args.kwargs
    assert call_kwargs["Key"] == "handler.zip"

    # Verify the zip contains both files from the directory
    import io, zipfile
    zip_bytes = call_kwargs["Body"]
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    assert "handler.py" in names
    assert "utils.py" in names
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_runner.py::test_upload_initial_lambda_zips_uses_directory_source -v
```

Expected: FAIL — uploaded zip only contains `handler.py`, not `utils.py`

- [ ] **Step 3: Replace `_upload_initial_lambda_zips` in `scenario_runner.py`**

Replace the entire method:

```python
def _upload_initial_lambda_zips(self) -> None:
    from harness.runner.deployment_handler import (
        _zip_dir, _zip_file, find_source_for_stem, find_handler_for_s3key, handler_to_arcname,
    )
    from harness.shared.template_parser import extract_s3key_stems

    template_path = os.path.join(self.scenario_dir, "faulted.yaml")
    stems = extract_s3key_stems(template_path)
    if not stems:
        return

    with open(template_path, "r", encoding="utf-8") as f:
        template_body = f.read()

    _ensure_artifact_bucket()
    for stem, s3_key in stems.items():
        source_path, is_dir = find_source_for_stem(self.deployment_dir, stem)
        if source_path is None:
            continue
        if is_dir:
            zip_bytes = _zip_dir(source_path)
        else:
            handler = find_handler_for_s3key(template_body, s3_key)
            arcname = handler_to_arcname(handler)
            zip_bytes = _zip_file(source_path, arcname=arcname)
        s3_client.put_object(Bucket=_ARTIFACT_BUCKET, Key=s3_key, Body=zip_bytes)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_runner.py -v
```

Expected: all tests PASS including the new one

- [ ] **Step 5: Commit**

```bash
git add harness/runner/scenario_runner.py tests/test_runner.py
git commit -m "fix(runner): _upload_initial_lambda_zips uses YAML-parsed stems and dir-aware packaging"
```

---

### Task 6: Full test suite smoke check

- [ ] **Step 1: Run the complete test suite**

```bash
pytest tests/test_template_parser.py tests/test_runner.py tests/test_agent_loop.py tests/test_shared.py -v
```

Expected: all tests PASS

- [ ] **Step 2: Commit (if any fixups were needed)**

```bash
git add -p
git commit -m "fix(packaging): post-review fixups for Theme A"
```

---

## Self-Review

**Spec coverage:**
- 4.2 (quoted / intrinsic S3Key) → Task 1 + Task 3 + Task 4
- 4.3 (multi-file directory packaging) → Task 4 `_zip_dir` + Task 5
- 4.4 (hardcoded `lambda/` subdir) → Task 4 `find_source_for_stem` walks all of `deployment/`
- 4.5 (stem collision) → Task 4 `seen_source_paths` guard with `ValueError`

**Placeholder scan:** No TBDs, no "handle edge cases" prose, all steps contain full code.

**Type consistency:**
- `find_source_for_stem` returns `tuple[str | None, bool]` — used consistently in Tasks 4 and 5
- `LambdaUpload.source_path: str = ""` and `is_dir: bool = False` — set explicitly in every `LambdaUpload(...)` construction in Task 4
- `extract_s3key_stems(template_path: str) -> dict[str, str]` — called with string path in all tasks
- `_zip_dir(dir_path: str) -> bytes` and `_zip_file(file_path: str, arcname: str) -> bytes` — signatures consistent throughout
