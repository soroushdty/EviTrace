# Design Document: schema-validator-split

## Overview

This feature refactors EviTrace's validation architecture from an ad-hoc,
scattered approach into a clean, schema-driven system with clear ownership
boundaries. The core changes are:

1. Two purpose-specific JSON schema files replace the previous single schema:
   `configs/structure_schema.json` (structural contracts) and
   `configs/agent_schema.json` (agent-facing content including the system
   prompt).
2. A generic `Validator` base class with injectable serializer and a
   `ValidationResult` frozen dataclass provide the reusable validation engine.
3. `StructureSchemaValidator` in `quality_control/` becomes the sole reader of
   `structure_schema.json`; `AgentSchemaValidator` in `agents/` (already
   exists) becomes the sole reader of `agent_schema.json`.
4. `validate_qc_context_input` migrates from `pipeline/validator.py` to
   `quality_control/`, where it delegates to `StructureSchemaValidator`.
5. The system prompt moves from a hardcoded Python constant in
   `agents/openai/prompts.py` to `agent_schema.json`, making it user-editable
   without touching any `.py` file.
6. PDF file validation lands in `pdf_extractor/` as a bespoke standalone
   function, outside the generic `Validator` hierarchy.
7. Dependency direction is enforced: agents → (no QC/pipeline), quality_control
   → (no agents/pipeline), pipeline → quality_control + agents.

The migration is hard: no backwards-compatibility shims. `SYSTEM_PROMPT` and
`validate_qc_context_input` are removed from their old locations entirely.

---

## Architecture

### Current State

```
pipeline/validator.py
  ├── validate_qc_context_input()   ← mixed concern: structural guard
  ├── validate_chunk_output()       ← LLM JSON parsing + ad-hoc field checks
  ├── clean_json_string()
  ├── _parse_response_json()
  └── reconstruct_fields()

agents/openai/prompts.py
  └── SYSTEM_PROMPT = "..."         ← hardcoded Python string constant

agents/validator.py                 ← AgentSchemaValidator (already exists)
configs/agent_schema.json           ← already contains system_prompt
configs/structure_schema.json       ← already contains structural $defs
```

### Target State

```
quality_control/
  ├── validator.py                  ← NEW: Validator base class + ValidationResult
  ├── structure_validator.py        ← NEW: StructureSchemaValidator
  ├── validate_context.py           ← NEW: validate_qc_context_input (migrated)
  └── __init__.py                   ← updated: exports new names

agents/
  ├── validator.py                  ← EXISTING: AgentSchemaValidator (unchanged)
  ├── __init__.py                   ← EXISTING: exports agent_schema_validator singleton
  └── openai/
      └── prompts.py                ← MODIFIED: SYSTEM_PROMPT constant removed;
                                       get_system_prompt() callable exposed instead

pdf_extractor/
  ├── pdf_validator.py              ← NEW: validate_pdf() standalone function
  └── __init__.py                   ← updated: exports PDFValidationError

pipeline/
  └── validator.py                  ← MODIFIED: validate_qc_context_input removed;
                                       validate_chunk_output delegates to
                                       StructureSchemaValidator

configs/
  ├── structure_schema.json         ← EXISTING: structural contracts only
  └── agent_schema.json             ← EXISTING: agent-facing content only
```

### Dependency Graph (post-migration)

```
main.py
  └── pipeline/orchestrator.py
        ├── pipeline/pdf_processor.py
        │     ├── quality_control/          (validate_qc_context_input)
        │     ├── agents/openai/api_client.py
        │     │     └── agents/openai/prompts.py
        │     │           └── agents/        (agent_schema_validator singleton)
        │     └── pipeline/validator.py
        │           └── quality_control/    (StructureSchemaValidator)
        └── quality_control/
              └── (no agents, no pipeline, no pdf_extractor)

pdf_extractor/
  └── pdf_validator.py              (no quality_control, no agents, no pipeline)
```

Forbidden cross-package imports (enforced by static analysis test):
- `pdf_extractor` → `quality_control`: **forbidden**
- `quality_control` → `agents`, `pipeline`, `pdf_extractor`: **forbidden**
- `agents` → `quality_control`, `pipeline`, `pdf_extractor`: **forbidden**
- `pipeline` → (imported by): **forbidden** (pipeline is a leaf consumer)

---

## Components and Interfaces

### 1. `ValidationResult` (frozen dataclass)

**Location:** `quality_control/validator.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    errors: list[str]
    validated_object: dict
```

Returned by every `Validator.validate()` call regardless of pass/fail. The
`validated_object` is always the serialized dict produced by the injected
serializer — never the original object.

---

### 2. `Validator` (generic base class)

**Location:** `quality_control/validator.py`

```python
from typing import Any, Callable

class Validator:
    def __init__(
        self,
        serializer: Callable[[Any], dict],
        schema: dict,
    ) -> None: ...

    def validate(self, obj: Any) -> ValidationResult: ...
```

**Construction:**
- Accepts `serializer: Callable[[Any], dict]` and `schema: dict`.
- Raises `TypeError` immediately if `schema` is not a `dict` or is `None`.
- Stores both arguments; does no I/O.

**`validate(obj)`:**
- Calls `serializer(obj)` to produce a dict. If the serializer raises, the
  exception propagates unchanged — no `ValidationResult` is returned.
- Validates the dict against `self._schema` using `jsonschema.validate()`.
- Returns `ValidationResult(is_valid=True, errors=[], validated_object=d)` on
  success.
- Returns `ValidationResult(is_valid=False, errors=[...], validated_object=d)`
  on failure, where each error string identifies the violated constraint by
  field name and rule.
- Contains no references to `Candidate`, `QCBundle`, PDF files, or OpenAI
  constructs.

**Validation engine:** `jsonschema` (already a project dependency via existing
schema validation patterns). Use `jsonschema.Draft7Validator` for consistent
error collection across all constraints.

---

### 3. `StructureSchemaValidator`

**Location:** `quality_control/structure_validator.py`

```python
class StructureSchemaValidator:
    def __init__(self, schema_path: Path | str | None = None) -> None: ...

    def validate_candidate(
        self, candidate: Any, serializer: Callable[[Any], dict]
    ) -> ValidationResult: ...

    def validate_qc_bundle(
        self, bundle: Any, serializer: Callable[[Any], dict]
    ) -> ValidationResult: ...

    def validate_pdf_processor_output(
        self, fields: Any, serializer: Callable[[Any], dict]
    ) -> ValidationResult: ...

    def validate_extraction_map(
        self, extraction_map: Any, serializer: Callable[[Any], dict]
    ) -> ValidationResult: ...

    def validate_chunk_output(
        self, chunk_output: Any, serializer: Callable[[Any], dict]
    ) -> ValidationResult: ...
```

**Construction:**
- Resolves `configs/structure_schema.json` relative to the project root
  (derived from `Path(__file__).resolve().parent.parent / "configs" /
  "structure_schema.json"`) when `schema_path` is `None`.
- Loads and parses the JSON file exactly once; holds the parsed dict in memory.
- Raises `StructureSchemaLoadError` (defined in `quality_control/`) if the
  file is missing or contains invalid JSON.

**Each validation method:**
- Reads `self._schema["validator_targets"][method_name]` to get the `$defs`
  reference string (e.g. `"#/$defs/Candidate"`).
- Resolves the referenced `$defs` entry from `self._schema["$defs"]`.
- Constructs a `Validator(serializer, sub_schema)` and calls
  `validator.validate(obj)`.
- Returns the `ValidationResult` directly.
- Propagates serializer exceptions unchanged.

**`StructureSchemaLoadError`:**

```python
class StructureSchemaLoadError(Exception):
    """Raised when structure_schema.json is missing or contains invalid JSON."""
```

Defined in `quality_control/structure_validator.py` and re-exported from
`quality_control/__init__.py`.

---

### 4. `AgentSchemaValidator` (existing — no changes to class)

**Location:** `agents/validator.py` (already exists and is complete)

The class is already implemented and correct. No changes to the class itself.
The only change is in `agents/openai/prompts.py` (see §6 below).

**`SchemaValidationError`:** Already defined in `agents/validator.py` and
re-exported from `agents/__init__.py`.

---

### 5. `validate_qc_context_input` (migrated)

**Location:** `quality_control/validate_context.py`

```python
from quality_control.structure_validator import StructureSchemaValidator
from quality_control.models import QCBundle

_structure_validator = StructureSchemaValidator()

def validate_qc_context_input(ctx: object) -> None: ...
```

**Behaviour (unchanged from current `pipeline/validator.py`):**
- Checks `ctx` is a `QCBundle` instance.
- Checks `ctx.unified` is not `None`.
- Checks `ctx.unified.document_id` is a non-empty `str`.
- Checks `ctx.unified.content` is a `dict`.
- Checks `ctx.unified.content['exact_text']` is a non-empty `str`.
- Delegates the structural check to
  `_structure_validator.validate_qc_bundle(ctx, serializer)`.
- Raises `ValidationError` (imported from `pipeline.validator`) with a
  non-empty `errors` list if any check fails.

**Import in `pipeline/pdf_processor.py`:**
```python
# Before (to be removed):
from .validator import reconstruct_fields, validate_qc_context_input

# After:
from quality_control.validate_context import validate_qc_context_input
from .validator import reconstruct_fields
```

---

### 6. `agents/openai/prompts.py` (modified)

**Removed:** The module-level constant `SYSTEM_PROMPT: str = ...`

**Added:** A module-level callable `get_system_prompt()` that delegates to the
singleton:

```python
from agents import agent_schema_validator

def get_system_prompt() -> str:
    """Return the system prompt from agent_schema.json via the singleton."""
    return agent_schema_validator.get_system_prompt()
```

**`api_client.py` change:** Replace the import of `SYSTEM_PROMPT` with a call
to `get_system_prompt()` at the point of use:

```python
# Before:
from .prompts import SYSTEM_PROMPT, build_cache_warmup_message, build_user_message
# ...
{"role": "system", "content": SYSTEM_PROMPT},

# After:
from .prompts import get_system_prompt, build_cache_warmup_message, build_user_message
# ...
{"role": "system", "content": get_system_prompt()},
```

This preserves prompt cache stability because `get_system_prompt()` always
returns the same cached string from the singleton for the lifetime of the
process.

---

### 7. `validate_pdf` (bespoke PDF validator)

**Location:** `pdf_extractor/pdf_validator.py`

```python
from pathlib import Path

def validate_pdf(path: str | Path) -> None: ...
```

**Not** a subclass of `Validator`. Standalone function.

**Check order (short-circuit — first failure terminates):**
1. **Magic bytes:** Read first 5 bytes; if not `b"%PDF-"`, raise
   `PDFValidationError` with a message identifying the magic bytes failure.
   Do not perform further checks.
2. **File size:** If `os.path.getsize(path) == 0`, raise `PDFValidationError`
   with a message identifying the empty file failure. Do not perform further
   checks.
3. **Password protection:** Call `fitz.open(path)`; if `doc.needs_pass` is
   `True`, raise `PDFValidationError` with a message identifying the password
   protection failure. Do not perform the fitz readability check.
4. **Fitz readability:** If `fitz.open()` raises any exception (including
   `fitz.FileDataError`), raise `PDFValidationError` with a message
   identifying the fitz readability failure.

**`PDFValidationError`:**

```python
class PDFValidationError(Exception):
    """Raised when a PDF file fails validation."""
```

Defined in `pdf_extractor/pdf_validator.py` and re-exported from
`pdf_extractor/__init__.py` so it is importable as
`from pdf_extractor import PDFValidationError`.

---

### 8. `pipeline/validator.py` (modified)

**Removed:** `validate_qc_context_input` function (hard removal).

**Modified:** `validate_chunk_output` delegates structural field validation to
`StructureSchemaValidator`:

```python
from quality_control.structure_validator import StructureSchemaValidator

_structure_validator = StructureSchemaValidator()

def validate_chunk_output(
    raw: str,
    expected_indices: list[int],
    *,
    valid_location_ids: set[str] | None = None,
) -> list[dict]:
    # ... existing JSON parsing logic unchanged ...
    # After parsing, delegate structural validation:
    result = _structure_validator.validate_chunk_output(data, lambda x: x)
    if not result.is_valid:
        raise ValidationError(
            "Structural validation failed:\n" + "\n".join(result.errors)
        )
    return data
```

**Retained (unchanged):** `clean_json_string`, `_parse_response_json`,
`reconstruct_fields`, `ValidationError`.

---

## Data Models

### `ValidationResult`

```python
@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool          # True iff all schema constraints satisfied
    errors: list[str]       # empty when is_valid=True; one string per violation
    validated_object: dict  # the serialized dict (never the original object)
```

Frozen to prevent accidental mutation after validation. The `validated_object`
is always the output of the serializer, not the original input — this is the
round-trip invariant.

### Schema File Ownership

| File | Owner | Content |
|---|---|---|
| `configs/structure_schema.json` | `StructureSchemaValidator` | `$defs` for Candidate, QCBundle, UnifiedRecord, ExtractionFieldDict, ExtractionMapEntry, ChunkOutput, StudyJson, PdfProcessorOutput; `validator_targets` mapping |
| `configs/agent_schema.json` | `AgentSchemaValidator` | `system_prompt`, `policies`, `extraction_rules` |

### `validator_targets` in `structure_schema.json`

The five method names map to `$defs` references:

```json
{
  "validator_targets": {
    "validate_candidate":           "#/$defs/Candidate",
    "validate_qc_bundle":           "#/$defs/QCBundle",
    "validate_pdf_processor_output":"#/$defs/PdfProcessorOutput",
    "validate_extraction_map":      "#/$defs/ExtractionMap",
    "validate_chunk_output":        "#/$defs/ChunkOutput"
  }
}
```

Note: `validate_study_json` is present in the current file but is not one of
the five required method names. The five required keys are exactly those listed
above.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all
valid executions of a system — essentially, a formal statement about what the
system should do. Properties serve as the bridge between human-readable
specifications and machine-verifiable correctness guarantees.*

### Property 1: Validator rejects non-dict schemas

*For any* value that is not a `dict` (including `None`, integers, strings,
lists), constructing `Validator(serializer, schema)` with that value as
`schema` SHALL raise `TypeError`.

**Validates: Requirements 2.2**

---

### Property 2: Validator round-trip — validated_object equals serializer output

*For any* object `obj` and any serializer `s` that does not raise, calling
`Validator(s, schema).validate(obj)` SHALL return a `ValidationResult` whose
`validated_object` is value-equal (`==`) to `s(obj)`.

**Validates: Requirements 2.3, 2.10**

---

### Property 3: Validator returns ValidationResult on every non-raising call

*For any* object `obj` and any serializer `s` that does not raise, calling
`Validator(s, schema).validate(obj)` SHALL return an instance of
`ValidationResult` (never `None`, never a raw dict, never an exception).

**Validates: Requirements 2.4**

---

### Property 4: Valid objects produce is_valid=True with empty errors

*For any* object whose serialized form satisfies all constraints in the schema,
`Validator.validate(obj)` SHALL return a `ValidationResult` with `is_valid`
equal to `True` and `errors` equal to `[]`.

**Validates: Requirements 2.6**

---

### Property 5: Invalid objects produce is_valid=False with non-empty errors

*For any* object whose serialized form violates one or more schema constraints,
`Validator.validate(obj)` SHALL return a `ValidationResult` with `is_valid`
equal to `False` and `len(errors) >= 1`, where each error string is non-empty.

**Validates: Requirements 2.7**

---

### Property 6: Serializer exceptions propagate unchanged

*For any* exception type `E` raised by the serializer callable, calling
`Validator.validate(obj)` SHALL propagate an exception of type `E` unchanged
and SHALL NOT return a `ValidationResult`.

**Validates: Requirements 2.9, 3.13**

---

### Property 7: StructureSchemaValidator idempotence

*For any* valid object passed to any of the five `StructureSchemaValidator`
validation methods, calling the same method twice on the same valid object
SHALL return `ValidationResult` instances that are equal in all three fields
(`is_valid`, `errors`, `validated_object`), regardless of call order.

**Validates: Requirements 3.12**

---

### Property 8: StructureSchemaValidator valid objects produce is_valid=True

*For any* valid `Candidate`, `QCBundle`, `PdfProcessorOutput`, `ExtractionMap`,
or `ChunkOutput` object, the corresponding `StructureSchemaValidator` method
SHALL return a `ValidationResult` with `is_valid` equal to `True` and `errors`
equal to `[]`.

**Validates: Requirements 3.4, 3.5, 3.6, 3.7, 3.8, 3.9**

---

### Property 9: StructureSchemaValidator invalid objects produce is_valid=False

*For any* object with a missing required field or a type violation, the
corresponding `StructureSchemaValidator` method SHALL return a
`ValidationResult` with `is_valid` equal to `False` and `len(errors) >= 1`.

**Validates: Requirements 3.10**

---

### Property 10: AgentSchemaValidator invalid schemas raise SchemaValidationError

*For any* schema dict that is missing one or more of the three required
top-level keys (`system_prompt`, `policies`, `extraction_rules`), or that
contains a non-string, empty, or whitespace-only value under `system_prompt`,
or that contains a non-`dict` value under `policies` or `extraction_rules`,
constructing `AgentSchemaValidator(schema_path)` SHALL raise
`SchemaValidationError`.

**Validates: Requirements 4.4, 5.5, 6.5**

---

### Property 11: AgentSchemaValidator prompt cache stability

*For any* valid `agent_schema.json`, calling `get_system_prompt()` any number
of times on the same `AgentSchemaValidator` instance SHALL always return a
value that is byte-identical to the string loaded at construction time.

**Validates: Requirements 4.8, 6.1, 6.2, 6.3**

---

### Property 12: PDF validator short-circuit ordering

*For any* file that fails the magic bytes check, `validate_pdf()` SHALL raise
`PDFValidationError` without performing the size, password, or fitz checks
(verified by asserting that the mocked size/fitz functions are never called).

*For any* file that passes the magic bytes check but fails the size check,
`validate_pdf()` SHALL raise `PDFValidationError` without performing the
password or fitz checks.

**Validates: Requirements 7.3, 7.8**

---

### Property Reflection

After reviewing all properties above:

- Properties 2 and 3 are distinct: Property 2 tests the *value* of
  `validated_object`; Property 3 tests the *type* of the return value. Both
  are needed.
- Properties 4 and 5 are complementary (valid vs. invalid inputs) and cannot
  be merged.
- Properties 8 and 9 mirror 4 and 5 but at the `StructureSchemaValidator`
  level. They are not redundant with 4/5 because they test the full delegation
  chain including `validator_targets` resolution.
- Property 7 (idempotence) is not implied by Properties 8/9 — it specifically
  tests that repeated calls on the same instance produce identical results.
- Properties 10 and 11 cover `AgentSchemaValidator` and are not redundant with
  any `Validator` property.
- Property 12 is specific to the PDF validator's short-circuit ordering and is
  not covered by any other property.

No redundancies identified. All 12 properties provide unique validation value.

---

## Error Handling

### `StructureSchemaLoadError`

Raised by `StructureSchemaValidator.__init__()` when:
- `configs/structure_schema.json` is not found at the resolved path.
- The file contains invalid JSON.

This is a fatal startup error. The process should not continue if the
structural schema cannot be loaded.

### `SchemaValidationError`

Raised by `AgentSchemaValidator.__init__()` when:
- `configs/agent_schema.json` is not found or unreadable.
- The file contains invalid JSON.
- Any of the three required top-level keys is missing.
- `system_prompt` is not a string, or is empty/whitespace-only.
- `policies` or `extraction_rules` is not a `dict`.

Already implemented. No changes needed.

### `PDFValidationError`

Raised by `validate_pdf()` at the first failing check in the fixed order:
magic bytes → size → password → fitz readability. Never raised for any other
reason; all other exceptions from `fitz.open()` are wrapped in
`PDFValidationError`.

### `ValidationError` (existing, in `pipeline/validator.py`)

Raised by `validate_chunk_output()` when `StructureSchemaValidator` returns
`is_valid=False`, and by `validate_qc_context_input()` when any structural
check fails. The `errors` list from `ValidationResult` is forwarded as the
exception message.

### Serializer exceptions

The `Validator` base class propagates serializer exceptions unchanged. Callers
are responsible for handling serializer-specific exceptions (e.g.
`AttributeError` if the object is missing an expected field).

---

## Testing Strategy

### Unit Tests

Unit tests cover specific examples, edge cases, and error conditions. They live
in `tests/quality_control/`, `tests/agents/`, `tests/pdf_extractor/`, and
`tests/pipeline/` following the existing layout convention.

**`tests/quality_control/test_validator_base.py`**
- `ValidationResult` is a frozen dataclass with exactly three fields.
- `Validator` raises `TypeError` for non-dict schemas (None, int, str, list).
- `Validator.validate()` returns `ValidationResult` for a trivially valid
  schema.
- `Validator.validate()` propagates serializer exceptions unchanged.
- `Validator.validate()` returns `is_valid=False` for a schema violation.

**`tests/quality_control/test_structure_validator.py`**
- `StructureSchemaValidator` raises `StructureSchemaLoadError` for a missing
  file.
- `StructureSchemaValidator` raises `StructureSchemaLoadError` for invalid JSON.
- Each of the five methods returns `is_valid=True` for a minimal valid object.
- Each of the five methods returns `is_valid=False` for an object missing a
  required field.
- `validate_qc_context_input` raises `ValidationError` for a `QCBundle` with
  `unified=None`.

**`tests/agents/test_agent_schema_validator.py`** (existing tests, extend as
needed)
- `AgentSchemaValidator` raises `SchemaValidationError` for missing file.
- `AgentSchemaValidator` raises `SchemaValidationError` for empty
  `system_prompt`.
- `get_system_prompt()` returns the expected string.

**`tests/pdf_extractor/test_pdf_validator.py`**
- `validate_pdf()` raises `PDFValidationError` for wrong magic bytes.
- `validate_pdf()` raises `PDFValidationError` for zero-size file.
- `validate_pdf()` raises `PDFValidationError` for password-protected PDF
  (mocked fitz).
- `validate_pdf()` raises `PDFValidationError` when `fitz.open()` raises.
- `PDFValidationError` is importable as `from pdf_extractor import
  PDFValidationError`.

**`tests/pipeline/test_validator_refactored.py`**
- `validate_qc_context_input` is NOT importable from `pipeline.validator`.
- `validate_chunk_output` raises `ValidationError` when
  `StructureSchemaValidator` returns `is_valid=False`.
- `SYSTEM_PROMPT` is NOT importable from `agents.openai.prompts`.

**`tests/test_dependency_directions.py`** (static import analysis)
- Recursively inspects AST of all `.py` files in each package.
- Asserts no forbidden cross-package imports exist (per Requirement 9).

### Property-Based Tests

Property-based tests use Hypothesis (`@given`, `@settings(max_examples=100)`).
They live alongside unit tests in their respective subdirectory.

**`tests/quality_control/test_validator_properties.py`**

```python
from hypothesis import given, settings
from hypothesis import strategies as st
```

- **Property 1** (`test_validator_rejects_non_dict_schema`): `@given(st.one_of(st.none(), st.integers(), st.text(), st.lists(st.integers())))` — assert `TypeError` is raised.
- **Property 2** (`test_validator_round_trip_validated_object`): `@given(st.dictionaries(st.text(), st.integers()))` — assert `result.validated_object == serializer(obj)`.
- **Property 3** (`test_validator_always_returns_validation_result`): `@given(st.dictionaries(st.text(), st.integers()))` — assert `isinstance(result, ValidationResult)`.
- **Property 4** (`test_validator_valid_input_is_valid_true`): Generate dicts that satisfy a simple schema; assert `is_valid=True` and `errors=[]`.
- **Property 5** (`test_validator_invalid_input_is_valid_false`): Generate dicts missing a required key; assert `is_valid=False` and `len(errors) >= 1`.
- **Property 6** (`test_validator_propagates_serializer_exception`): `@given(st.sampled_from([ValueError, TypeError, RuntimeError]))` — assert the exception propagates.
- **Property 7** (`test_structure_validator_idempotence`): Generate valid `Candidate`-shaped dicts; call `validate_candidate` twice; assert results are equal.
- **Property 8** (`test_structure_validator_valid_objects`): Generate valid `Candidate`-shaped dicts; assert `is_valid=True`.
- **Property 9** (`test_structure_validator_invalid_objects`): Generate dicts missing required fields; assert `is_valid=False`.
- **Property 10** (`test_agent_schema_validator_invalid_schemas`): `@given(st.fixed_dictionaries({...}))` with missing/invalid keys; assert `SchemaValidationError`.
- **Property 11** (`test_agent_schema_validator_prompt_stability`): `@given(st.integers(min_value=1, max_value=50))` for call count; assert all calls return the same string.
- **Property 12** (`test_pdf_validator_short_circuit_magic_bytes`): Generate random bytes that are not `%PDF-`; assert `PDFValidationError` is raised and size/fitz mocks are never called.

Each property test is tagged with a comment:
```python
# Feature: schema-validator-split, Property N: <property_text>
```

### Integration Tests

No dedicated integration tests for this feature. The existing end-to-end
pipeline tests (exercised via `pipeline/orchestrator.py`) provide integration
coverage. The static import analysis test in `tests/test_dependency_directions.py`
provides structural integration coverage.

### Slow Test Marking

No property tests in this feature require slow marking — all use in-memory
objects and mocked fitz. No `pytestmark = pytest.mark.slow` is needed.
