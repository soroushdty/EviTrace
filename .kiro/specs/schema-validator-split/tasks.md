# Implementation Plan: schema-validator-split

## Overview

Refactor EviTrace's validation architecture into a clean, schema-driven system
with clear ownership boundaries. The work proceeds in five logical phases:
(1) create the generic `Validator` base and `ValidationResult` dataclass,
(2) create `StructureSchemaValidator` and migrate `validate_qc_context_input`,
(3) create the PDF validator in `pdf_extractor/`,
(4) modify `agents/openai/prompts.py` and `api_client.py` to remove
`SYSTEM_PROMPT`, and (5) refactor `pipeline/validator.py` to delegate to
`StructureSchemaValidator` and hard-remove `validate_qc_context_input`.
A static AST dependency-direction test closes the work.

---

## Tasks

- [x] 1. Create `ValidationResult` frozen dataclass and `Validator` base class
  - Create `quality_control/validator.py` with the `ValidationResult` frozen
    dataclass (`is_valid: bool`, `errors: list[str]`, `validated_object: dict`)
    and the `Validator` class that accepts `serializer: Callable[[Any], dict]`
    and `schema: dict` at construction time.
  - `Validator.__init__` must raise `TypeError` immediately when `schema` is
    not a `dict` or is `None`.
  - `Validator.validate(obj)` calls `serializer(obj)`, validates the resulting
    dict with `jsonschema.Draft7Validator`, and returns a `ValidationResult`.
    Serializer exceptions and schema-engine exceptions propagate unchanged.
  - The class must contain no references to `Candidate`, `QCBundle`, PDF files,
    or OpenAI constructs.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_

  - [x] 1.1 Implement `ValidationResult` and `Validator` in `quality_control/validator.py`
    - Write the frozen dataclass and the `Validator` class exactly as specified
      in the design (§1 and §2).
    - _Requirements: 2.1–2.10_

  - [x] 1.2 Write unit tests for `Validator` base class
    - File: `tests/quality_control/test_validator_base.py`
    - Cover: `ValidationResult` is frozen with exactly three fields; `TypeError`
      for non-dict schemas (`None`, `int`, `str`, `list`); `validate()` returns
      `ValidationResult` for a trivially valid schema; serializer exceptions
      propagate unchanged; `is_valid=False` for a schema violation.
    - _Requirements: 2.2, 2.4, 2.7, 2.9_

  - [x] 1.3 Write property-based tests for `Validator` (Properties 1–6)
    - File: `tests/quality_control/test_validator_properties.py`
    - **Property 1:** `test_validator_rejects_non_dict_schema` — `@given(st.one_of(st.none(), st.integers(), st.text(), st.lists(st.integers())))` — assert `TypeError`.
    - **Property 2:** `test_validator_round_trip_validated_object` — assert `result.validated_object == serializer(obj)`.
    - **Property 3:** `test_validator_always_returns_validation_result` — assert `isinstance(result, ValidationResult)`.
    - **Property 4:** `test_validator_valid_input_is_valid_true` — valid dicts → `is_valid=True`, `errors=[]`.
    - **Property 5:** `test_validator_invalid_input_is_valid_false` — dicts missing required key → `is_valid=False`, `len(errors) >= 1`.
    - **Property 6:** `test_validator_propagates_serializer_exception` — assert exception propagates unchanged.
    - Use `@settings(max_examples=100)` on each test.
    - Tag each test with `# Feature: schema-validator-split, Property N: <text>`
    - _Requirements: 2.2, 2.3, 2.4, 2.6, 2.7, 2.9, 2.10_

- [x] 2. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Create `StructureSchemaValidator` and `StructureSchemaLoadError`
  - Create `quality_control/structure_validator.py` with `StructureSchemaLoadError`
    and `StructureSchemaValidator`.
  - Constructor resolves `configs/structure_schema.json` relative to the project
    root via `Path(__file__).resolve().parent.parent / "configs" / "structure_schema.json"`
    when `schema_path` is `None`; loads and parses JSON exactly once; raises
    `StructureSchemaLoadError` on missing file or invalid JSON.
  - Each of the five methods (`validate_candidate`, `validate_qc_bundle`,
    `validate_pdf_processor_output`, `validate_extraction_map`,
    `validate_chunk_output`) reads `self._schema["validator_targets"][method_name]`
    to resolve the `$defs` sub-schema, constructs a `Validator(serializer, sub_schema)`,
    calls `validator.validate(obj)`, and returns the `ValidationResult` directly.
    Serializer exceptions propagate unchanged.
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13_

  - [x] 3.1 Implement `StructureSchemaLoadError` and `StructureSchemaValidator`
    - Write `quality_control/structure_validator.py` as specified in the design (§3).
    - _Requirements: 3.1–3.13_

  - [x] 3.2 Write unit tests for `StructureSchemaValidator`
    - File: `tests/quality_control/test_structure_validator.py`
    - Cover: `StructureSchemaLoadError` raised for missing file; raised for
      invalid JSON; each of the five methods returns `is_valid=True` for a
      minimal valid object; each returns `is_valid=False` for an object missing
      a required field.
    - _Requirements: 3.9, 3.10, 3.11_

  - [x] 3.3 Write property-based tests for `StructureSchemaValidator` (Properties 7–9)
    - File: `tests/quality_control/test_validator_properties.py` (extend existing file)
    - **Property 7:** `test_structure_validator_idempotence` — call same method twice on same valid object; assert results equal in all three fields.
    - **Property 8:** `test_structure_validator_valid_objects` — generate valid `Candidate`-shaped dicts; assert `is_valid=True`.
    - **Property 9:** `test_structure_validator_invalid_objects` — generate dicts missing required fields; assert `is_valid=False`.
    - Use `@settings(max_examples=100)` on each test.
    - Tag each test with `# Feature: schema-validator-split, Property N: <text>`
    - _Requirements: 3.9, 3.10, 3.12_

- [x] 4. Migrate `validate_qc_context_input` to `quality_control/`
  - Create `quality_control/validate_context.py` with a module-level
    `_structure_validator = StructureSchemaValidator()` singleton and the
    `validate_qc_context_input(ctx)` function migrated from
    `pipeline/validator.py`.
  - The function must perform the five checks (isinstance QCBundle, unified not
    None, document_id non-empty str, content is dict, exact_text non-empty str)
    and delegate the structural check to
    `_structure_validator.validate_qc_bundle(ctx, serializer)`.
  - Raises `ValidationError` (imported from `pipeline.validator`) with a
    non-empty `errors` list if any check fails.
  - Update `pipeline/pdf_processor.py` to import `validate_qc_context_input`
    from `quality_control.validate_context` instead of `pipeline.validator`.
  - _Requirements: 8.4, 8.5_

  - [x] 4.1 Create `quality_control/validate_context.py` with migrated function
    - Write the module as specified in the design (§5).
    - _Requirements: 8.4_

  - [x] 4.2 Update import in `pipeline/pdf_processor.py`
    - Change `from .validator import reconstruct_fields, validate_qc_context_input`
      to `from quality_control.validate_context import validate_qc_context_input`
      and `from .validator import reconstruct_fields` (separate import).
    - _Requirements: 8.5_

- [x] 5. Update `quality_control/__init__.py` exports
  - Export `StructureSchemaLoadError`, `StructureSchemaValidator`,
    `ValidationResult`, `Validator`, and `validate_qc_context_input` from
    `quality_control/__init__.py`.
  - _Requirements: 3.1, 3.11_

  - [x] 5.1 Update `quality_control/__init__.py`
    - Add the new names to the package's public exports.
    - _Requirements: 3.1, 3.11_

- [x] 6. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Create `validate_pdf` standalone function in `pdf_extractor/`
  - Create `pdf_extractor/pdf_validator.py` with `PDFValidationError` and the
    `validate_pdf(path: str | Path) -> None` standalone function.
  - `validate_pdf` is NOT a subclass of `Validator`.
  - Check order (short-circuit): magic bytes (`b"%PDF-"`) → file size (zero
    bytes) → password protection (`doc.needs_pass`) → fitz readability
    (`fitz.open()` raises). Each failure raises `PDFValidationError` with a
    descriptive message; subsequent checks are not performed.
  - All `fitz.open()` exceptions (including `fitz.FileDataError`) are wrapped
    in `PDFValidationError`.
  - Update `pdf_extractor/__init__.py` to export `PDFValidationError`.
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

  - [x] 7.1 Implement `PDFValidationError` and `validate_pdf` in `pdf_extractor/pdf_validator.py`
    - Write the module as specified in the design (§7).
    - _Requirements: 7.1–7.8_

  - [x] 7.2 Update `pdf_extractor/__init__.py` to export `PDFValidationError`
    - Add `from pdf_extractor.pdf_validator import PDFValidationError` to the
      package's `__init__.py`.
    - _Requirements: 7.7_

  - [-] 7.3 Write unit tests for `validate_pdf`
    - File: `tests/pdf_extractor/test_pdf_validator.py`
    - Cover: raises `PDFValidationError` for wrong magic bytes; for zero-size
      file; for password-protected PDF (mocked fitz with `doc.needs_pass=True`);
      when `fitz.open()` raises; `PDFValidationError` importable as
      `from pdf_extractor import PDFValidationError`.
    - Mock `fitz` with `MagicMock`; never call real fitz.
    - _Requirements: 7.3, 7.4, 7.5, 7.6, 7.7_

  - [-] 7.4 Write property-based test for PDF validator short-circuit (Property 12)
    - File: `tests/pdf_extractor/test_pdf_validator.py` (extend)
    - **Property 12:** `test_pdf_validator_short_circuit_magic_bytes` — `@given(st.binary(min_size=5).filter(lambda b: b[:5] != b"%PDF-"))` — assert `PDFValidationError` raised and size/fitz mocks never called.
    - Use `@settings(max_examples=100)`.
    - Tag with `# Feature: schema-validator-split, Property 12: <text>`
    - _Requirements: 7.3, 7.8_

- [x] 8. Remove `SYSTEM_PROMPT` constant from `agents/openai/prompts.py`
  - Remove the module-level `SYSTEM_PROMPT` constant from
    `agents/openai/prompts.py`.
  - Add a module-level `get_system_prompt() -> str` callable that delegates to
    `agent_schema_validator.get_system_prompt()` via the singleton imported from
    `agents`.
  - Update `agents/openai/api_client.py` to import `get_system_prompt` instead
    of `SYSTEM_PROMPT`, and call `get_system_prompt()` at the point of use
    (e.g. `{"role": "system", "content": get_system_prompt()}`).
  - _Requirements: 5.3, 5.4, 10.1, 10.3, 10.5, 10.6_

  - [x] 8.1 Modify `agents/openai/prompts.py` — remove `SYSTEM_PROMPT`, add `get_system_prompt()`
    - Write the change as specified in the design (§6).
    - _Requirements: 5.3, 5.4, 10.1, 10.3_

  - [x] 8.2 Modify `agents/openai/api_client.py` — replace `SYSTEM_PROMPT` import with `get_system_prompt` call
    - Write the change as specified in the design (§6).
    - _Requirements: 10.5, 10.6_

- [ ] 9. Refactor `pipeline/validator.py` — delegate to `StructureSchemaValidator`, hard-remove `validate_qc_context_input`
  - Add a module-level `_structure_validator = StructureSchemaValidator()`
    singleton (imported from `quality_control.structure_validator`).
  - Modify `validate_chunk_output` to delegate structural field validation to
    `_structure_validator.validate_chunk_output(data, lambda x: x)` after
    existing JSON parsing; raise `ValidationError` with the `errors` list when
    `result.is_valid` is `False`.
  - Hard-remove `validate_qc_context_input` from `pipeline/validator.py`
    (no shim, no re-export, no `__all__` entry).
  - Retain `clean_json_string`, `_parse_response_json`, `validate_chunk_output`,
    `reconstruct_fields`, and `ValidationError` unchanged.
  - _Requirements: 8.1, 8.2, 8.3, 8.6, 10.2, 10.4_

  - [x] 9.1 Modify `pipeline/validator.py` — add `StructureSchemaValidator` delegation and remove `validate_qc_context_input`
    - Write the change as specified in the design (§8).
    - _Requirements: 8.1, 8.2, 8.3, 8.6, 10.2, 10.4_

  - [-] 9.2 Write unit tests for refactored `pipeline/validator.py`
    - File: `tests/pipeline/test_validator_refactored.py`
    - Cover: `validate_qc_context_input` is NOT importable from `pipeline.validator`
      (assert `ImportError` or `AttributeError`); `validate_chunk_output` raises
      `ValidationError` when `StructureSchemaValidator` returns `is_valid=False`;
      `SYSTEM_PROMPT` is NOT importable from `agents.openai.prompts`.
    - _Requirements: 8.6, 10.1, 10.2_

- [x] 10. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Write static AST dependency-direction test
  - Create `tests/test_dependency_directions.py` that recursively inspects the
    AST of all `.py` files in each package (including sub-packages and
    `__init__.py` files) and asserts no forbidden cross-package import statements
    exist.
  - Forbidden pairs to assert (per Requirement 9):
    - `pdf_extractor` → `quality_control`: forbidden
    - `quality_control` → `agents`: forbidden
    - `quality_control` → `pipeline`: forbidden
    - `quality_control` → `pdf_extractor`: forbidden
    - `agents` → `quality_control`: forbidden
    - `agents` → `pipeline`: forbidden
    - `agents` → `pdf_extractor`: forbidden
    - `pipeline` imported by `quality_control` or `agents`: forbidden
  - Use Python's `ast` module; no external dependencies.
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 11.1 Implement `tests/test_dependency_directions.py`
    - Write the static import analysis test as specified in the design (Testing
      Strategy §`tests/test_dependency_directions.py`).
    - _Requirements: 9.1–9.6_

- [x] 12. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP.
- Each task references specific requirements for traceability.
- Checkpoints ensure incremental validation after each logical phase.
- Property tests validate universal correctness properties (Properties 1–12 from the design).
- Unit tests validate specific examples and edge cases.
- The migration is hard: no backwards-compatibility shims. `SYSTEM_PROMPT` and
  `validate_qc_context_input` must be fully removed from their old locations.
- Mock `fitz` (PyMuPDF) with `MagicMock` in all unit tests; never call real fitz.
- Run tests from the repo root: `python -m pytest -q`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "4.1"] },
    { "id": 4, "tasks": ["4.2", "5.1"] },
    { "id": 5, "tasks": ["7.1", "8.1", "9.1"] },
    { "id": 6, "tasks": ["7.2", "8.2"] },
    { "id": 7, "tasks": ["7.3", "7.4", "9.2"] },
    { "id": 8, "tasks": ["11.1"] }
  ]
}
```
