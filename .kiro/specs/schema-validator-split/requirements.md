# Requirements Document

## Introduction

EviTrace currently scatters validation logic across multiple modules: structural
validation of pipeline objects lives partly in `pipeline/validator.py` (mixed
with LLM JSON parsing), and the agent system prompt is hardcoded as a Python
string literal in `agents/openai/prompts.py`. This makes the system prompt
inaccessible to users without editing Python source, and makes it impossible to
reason about which module "owns" a given schema.

This feature introduces a clean schema-driven validation architecture by:

1. Splitting the monolithic schema into two purpose-specific JSON files with
   clear ownership boundaries.
2. Introducing two dedicated validator classes — `StructureSchemaValidator` and
   `AgentSchemaValidator` — each the sole reader of its schema file.
3. Providing a generic, injectable `Validator` base class that knows nothing
   about PDFs, candidates, or agents.
4. Making the system prompt user-editable via `agent_schema.json` without
   touching any `.py` module.
5. Migrating `validate_qc_context_input` out of `pipeline/validator.py` and
   into `quality_control/`, where it delegates to `StructureSchemaValidator`.

---

## Glossary

- **StructureSchemaValidator**: The validator class in `quality_control/` that
  is the sole reader of `structure_schema.json`. Validates any object against a
  structural schema using an injectable serializer.
- **AgentSchemaValidator**: The validator class in `agents/` (not
  `agents/openai/`) that is the sole reader of `agent_schema.json`. Validates
  the schema on startup and exposes typed accessors to the rest of the system.
- **Validator**: The generic base class that accepts a serializer callable and a
  schema dict, and returns a `ValidationResult`. It has no knowledge of PDFs,
  candidates, chunks, or agents.
- **ValidationResult**: A frozen dataclass returned by every `Validator.validate()`
  call, carrying `is_valid` (bool), `errors` (list of strings), and
  `validated_object` (the serialized dict).
- **structure_schema.json**: The JSON file containing machine-enforceable
  structural contracts: required keys, types, allowed values, and cardinality
  rules. Read exclusively by `StructureSchemaValidator`. Located at
  `configs/structure_schema.json` relative to the project root.
- **agent_schema.json**: The JSON file containing agent-facing content: system
  prompt text, output policies, extraction rules, and key legends. Read
  exclusively by `AgentSchemaValidator`. Located at `configs/agent_schema.json`
  relative to the project root.
- **PDF_Validator**: The bespoke function in `pdf_extractor/` that validates PDF
  files by checking magic bytes, non-zero size, password protection, and
  readability by fitz. Not part of the generic `Validator` hierarchy.
- **System_Prompt**: The text returned by `AgentSchemaValidator.get_system_prompt()`,
  loaded from `agent_schema.json` and byte-identical across all calls within
  a single process lifetime.
- **Candidate**: A dataclass from `quality_control/models.py` representing one
  contributor's output entering the QC pipeline.
- **QCBundle**: A dataclass from `quality_control/models.py` representing the
  full shared mutable state passed through all QC modules.
- **StructureSchemaLoadError**: The exception raised by `StructureSchemaValidator`
  when `structure_schema.json` is missing or contains invalid JSON. Defined in
  `quality_control/`.
- **SchemaValidationError**: The exception raised by `AgentSchemaValidator`
  when `agent_schema.json` is missing, malformed, or structurally invalid.
  Defined in `agents/` and importable as `from agents import SchemaValidationError`.

---

## Requirements

### Requirement 1: Schema File Split

**User Story:** As a developer, I want the single schema to be split into two
purpose-specific JSON files, so that structural contracts and agent-facing
content have clear, separate ownership.

#### Acceptance Criteria

1. THE System SHALL provide a `structure_schema.json` file at
   `configs/structure_schema.json` containing only machine-enforceable
   structural contracts: required keys, field types, allowed values, and
   cardinality rules for pipeline dataclasses and their fields (`Candidate`,
   `QCBundle`, `UnifiedRecord`, `ExtractionFieldDict`, `ExtractionMapEntry`,
   `ChunkOutput`, `StudyJson`, and `PdfProcessorOutput`).
2. THE System SHALL provide an `agent_schema.json` file at
   `configs/agent_schema.json` containing only agent-facing content: system
   prompt text, output policies, extraction rules, and key legends.
3. THE System SHALL ensure that `structure_schema.json` contains no agent-facing
   content — specifically: no system prompt text, no output policies, no
   extraction rules, no compact key legends (e.g. `i`, `v`, `loc`, `c`
   definitions), no pipeline status strings (e.g. `"complete"`,
   `"failed_qc_pipeline"`), no confidence tier definitions (e.g. `h`, `m`,
   `l`, `nr`), and no evidence rules.
4. THE System SHALL ensure that `agent_schema.json` contains no structural
   contracts — specifically: no required key lists, no field type constraints,
   no allowed value enumerations, and no cardinality rules for pipeline
   dataclasses.
5. THE `structure_schema.json` file SHALL contain a `validator_targets` object
   whose keys are exactly the five `StructureSchemaValidator` method names
   (`validate_candidate`, `validate_qc_bundle`, `validate_pdf_processor_output`,
   `validate_extraction_map`, `validate_chunk_output`) and whose values are
   strings in the form `"#/$defs/<TypeName>"` identifying the `$defs` entry
   each method validates against, so that the schema is self-describing with
   respect to its consumers.

---

### Requirement 2: Generic Validator Base Class

**User Story:** As a developer, I want a generic `Validator` class that knows
nothing about PDFs, candidates, or agents, so that I can validate any object
against any schema by injecting a serializer.

#### Acceptance Criteria

1. THE Validator SHALL accept a serializer callable of type
   `Callable[[Any], dict]` and a schema dict at construction time.
2. IF the schema argument passed at construction time is not a `dict` or is
   `None`, THEN THE Validator SHALL raise `TypeError` before any call to
   `validate()` can be made.
3. WHEN `Validator.validate(obj)` is called, THE Validator SHALL apply the
   serializer to `obj` to produce a dict, then validate that dict against the
   schema.
4. THE Validator SHALL return a `ValidationResult` frozen dataclass on every
   call to `validate()`, regardless of whether validation passes or fails.
5. THE ValidationResult SHALL carry exactly three fields: `is_valid` (bool),
   `errors` (list of strings), and `validated_object` (the serialized dict).
6. WHEN the serialized dict satisfies all schema constraints, THE Validator
   SHALL set `ValidationResult.is_valid` to `True` and `ValidationResult.errors`
   to an empty list and `ValidationResult.validated_object` to the serialized dict.
7. WHEN the serialized dict violates one or more schema constraints, THE
   Validator SHALL set `ValidationResult.is_valid` to `False` and
   `ValidationResult.errors` to a non-empty list of non-empty strings each
   identifying the violated constraint by field name and rule (e.g.
   `'field "c": value "x" not in allowed set {"h", "m", "l", "nr"}'`), one
   string per violation, and `ValidationResult.validated_object` to the
   serialized dict.
8. THE Validator SHALL contain no references to PDF files, `Candidate` objects,
   `QCBundle` objects, or OpenAI API constructs.
9. WHEN the serializer callable raises an exception, OR WHEN the schema
   validation engine raises an exception during `validate()`, THE Validator
   SHALL propagate that exception unchanged and SHALL NOT return a
   `ValidationResult`.
10. FOR ALL objects `obj` where the serializer does not raise, THE Validator
    SHALL return a `ValidationResult` whose `validated_object` is value-equal
    (`==`) to the dict produced by applying the serializer to `obj` (round-trip
    invariant: the validated object is always the serialized form, never the
    original).

---

### Requirement 3: StructureSchemaValidator

**User Story:** As a developer, I want a `StructureSchemaValidator` in
`quality_control/` that is the sole reader of `structure_schema.json`, so that
all structural validation is centralised and the schema file has a single owner.

#### Acceptance Criteria

1. THE StructureSchemaValidator SHALL reside in the `quality_control/` package.
2. THE StructureSchemaValidator SHALL be the only module in the codebase that
   opens or reads `structure_schema.json`.
3. WHEN constructed, THE StructureSchemaValidator SHALL load
   `configs/structure_schema.json` (resolved relative to the project root,
   derived from the module file's own location) exactly once and hold the parsed
   schema in memory for the lifetime of the instance.
4. WHEN `validate_candidate(candidate, serializer)` is called, THE
   StructureSchemaValidator SHALL apply the serializer to the `Candidate` object
   and delegate to the generic `Validator` using the `$defs` entry identified by
   `validator_targets["validate_candidate"]`, returning a `ValidationResult`.
5. WHEN `validate_qc_bundle(bundle, serializer)` is called, THE
   StructureSchemaValidator SHALL apply the serializer to the `QCBundle` object
   and delegate to the generic `Validator` using the `$defs` entry identified by
   `validator_targets["validate_qc_bundle"]`, returning a `ValidationResult`.
6. WHEN `validate_pdf_processor_output(fields, serializer)` is called, THE
   StructureSchemaValidator SHALL apply the serializer to the list of extraction
   field dicts and delegate to the generic `Validator` using the `$defs` entry
   identified by `validator_targets["validate_pdf_processor_output"]`, returning
   a `ValidationResult`.
7. WHEN `validate_extraction_map(extraction_map, serializer)` is called, THE
   StructureSchemaValidator SHALL apply the serializer to the extraction map
   object and delegate to the generic `Validator` using the `$defs` entry
   identified by `validator_targets["validate_extraction_map"]`, returning a
   `ValidationResult`.
8. WHEN `validate_chunk_output(chunk_output, serializer)` is called, THE
   StructureSchemaValidator SHALL apply the serializer to the chunk output object
   and delegate to the generic `Validator` using the `$defs` entry identified by
   `validator_targets["validate_chunk_output"]`, returning a `ValidationResult`.
9. WHEN any of the five validation methods is called with an object whose
   serialized form satisfies all structural constraints in
   `structure_schema.json`, THE StructureSchemaValidator SHALL return a
   `ValidationResult` with `is_valid` equal to `True`, `errors` equal to an
   empty list, and `validated_object` equal to the serialized dict.
10. WHEN any of the five validation methods is called with an object whose
    serialized form violates one or more structural constraints, THE
    StructureSchemaValidator SHALL return a `ValidationResult` with `is_valid`
    equal to `False` and `errors` containing one non-empty string per violated
    constraint, each identifying the constraint by field name and rule.
11. IF `configs/structure_schema.json` is missing from the project root or
    contains invalid JSON at construction time, THEN THE StructureSchemaValidator
    SHALL raise a `StructureSchemaLoadError` (defined in `quality_control/`)
    before any validation method can be called.
12. FOR ALL valid objects passed to any of the five validation methods, THE
    StructureSchemaValidator SHALL return a `ValidationResult` with `is_valid`
    equal to `True` regardless of the order in which the methods are called
    (idempotence: calling the same method twice on the same valid object
    produces the same result).
13. WHEN any of the five validation methods is called and the serializer raises
    an exception, THE StructureSchemaValidator SHALL propagate that exception
    unchanged and SHALL NOT return a `ValidationResult`.

---

### Requirement 4: AgentSchemaValidator

**User Story:** As a developer, I want an `AgentSchemaValidator` in `agents/`
that is the sole reader of `agent_schema.json`, so that all agent-facing content
is centralised and the schema file has a single owner.

#### Acceptance Criteria

1. THE AgentSchemaValidator SHALL reside in the `agents/` package (not in
   `agents/openai/`).
2. THE AgentSchemaValidator SHALL be the only module in the codebase that opens
   or reads `agent_schema.json`.
3. WHEN constructed, THE AgentSchemaValidator SHALL load `agent_schema.json`
   exactly once and hold the parsed content in memory for the lifetime of the
   instance.
4. IF `agent_schema.json` is missing or unreadable (raises `OSError` or
   `FileNotFoundError`), is malformed JSON, is missing any of the three required
   top-level keys (`system_prompt`, `policies`, `extraction_rules`), contains a
   non-string value under `system_prompt`, contains a string under
   `system_prompt` whose `.strip()` is empty (whitespace-only or empty string),
   or contains a non-`dict` value under `policies` or `extraction_rules`, THEN
   THE AgentSchemaValidator SHALL raise `agents.SchemaValidationError` during
   construction before any accessor is called.
5. THE AgentSchemaValidator SHALL expose a `get_system_prompt()` method that
   returns the cached value stored under the `system_prompt` key as a `str`,
   with no modification or assembly.
6. THE AgentSchemaValidator SHALL expose a `get_policies()` method that returns
   the cached output policies as a `dict`.
7. THE AgentSchemaValidator SHALL expose a `get_extraction_rules()` method that
   returns the cached extraction rules as a `dict`.
8. FOR ALL calls to `get_system_prompt()`, `get_policies()`, and
   `get_extraction_rules()` on the same `AgentSchemaValidator` instance, THE
   AgentSchemaValidator SHALL return byte-identical values (prompt cache
   stability invariant: the in-memory cached values are never re-read from disk
   after construction).
9. THE `agents.SchemaValidationError` exception class SHALL be defined in the
   `agents/` package `__init__.py` or a dedicated `agents/exceptions.py` module
   and SHALL be importable as `from agents import SchemaValidationError`.
10. WHEN constructed with no `schema_path` argument, THE AgentSchemaValidator
    SHALL resolve `agent_schema.json` at `configs/agent_schema.json` relative to
    the project root, derived from the module file's own location, so that the
    path remains correct regardless of the working directory from which the
    process is started.

---

### Requirement 5: User-Editable System Prompt

**User Story:** As a user, I want to modify the system prompt by editing
`agent_schema.json` only, so that I can customise extraction behaviour without
touching any Python module.

#### Acceptance Criteria

1. THE System SHALL store the full system prompt text in `agent_schema.json`
   under the top-level key `system_prompt`.
2. WHEN `AgentSchemaValidator.get_system_prompt()` is called after
   `agent_schema.json` has been edited and the process restarted, THE
   AgentSchemaValidator SHALL return the updated value of the `system_prompt`
   key.
3. THE System SHALL NOT define a module-level name `SYSTEM_PROMPT` in
   `agents/openai/prompts.py` after the migration. Any module-level constant
   named `SYSTEM_PROMPT` in that file is prohibited regardless of how it is
   assigned (direct assignment, import alias, or computed expression).
4. THE `agents/openai/prompts.py` module SHALL obtain the system prompt
   exclusively by calling `AgentSchemaValidator.get_system_prompt()` via the
   module-level singleton `agent_schema_validator` exported from the `agents`
   package, and SHALL expose the result through a module-level callable or
   accessor (not a constant) so that `api_client.py` can retrieve it without
   importing a `SYSTEM_PROMPT` constant.
5. IF the value of the `system_prompt` key in `agent_schema.json` is an empty
   string, a whitespace-only string, or a non-string type, THEN THE
   AgentSchemaValidator SHALL raise `agents.SchemaValidationError` during
   construction (this criterion applies to the user-edit guard: the same
   validation that fires on initial load also fires when the file is edited and
   the process is restarted).

---

### Requirement 6: Prompt Cache Stability

**User Story:** As a developer, I want the assembled system prompt to be
byte-identical across all warmup and extraction calls within a single run, so
that OpenAI prompt caching works correctly.

#### Acceptance Criteria

1. THE AgentSchemaValidator SHALL load `agent_schema.json` once at process
   startup and cache the `system_prompt` string in memory; subsequent calls to
   `get_system_prompt()` SHALL return the cached value without re-reading the
   file.
2. WHILE the process is running, FOR ALL calls to `get_system_prompt()` on the
   same `AgentSchemaValidator` instance, THE AgentSchemaValidator SHALL return
   a value that is byte-equal to the string loaded at construction time.
3. THE system prompt string returned by `get_system_prompt()` SHALL be
   byte-identical between the cache warmup call in `warm_pdf_cache` and all
   subsequent `extract_chunk` calls within the same process run.
4. THE shared user-message prefix built by `agents/openai/prompts.py` (the
   portion of the user message that is constant across all chunks for the same
   PDF) SHALL NOT include any of the following variable values: PDF file name,
   chunk index or number, run ID, timestamp, or process ID.
5. IF `agent_schema.json` is missing or unreadable when the process starts,
   THEN THE AgentSchemaValidator SHALL raise `agents.SchemaValidationError`
   during construction, preventing any warmup or extraction call from proceeding
   with an undefined system prompt.

---

### Requirement 7: PDF File Validation

**User Story:** As a developer, I want PDF file validation (magic bytes,
non-zero size, password protection, fitz readability) to live in
`pdf_extractor/` as a bespoke function, so that it is not conflated with the
generic schema-based Validator hierarchy.

#### Acceptance Criteria

1. THE PDF_Validator SHALL reside in the `pdf_extractor/` package as a
   standalone function accepting a `str` or `pathlib.Path` argument, not as a
   subclass of `Validator`.
2. WHEN a file with valid PDF magic bytes, non-zero size, no password protection,
   and fitz readability is provided, THE PDF_Validator SHALL return without
   raising an exception.
3. WHEN the first 5 bytes of the file do not equal `%PDF-` (0x25 0x50 0x44
   0x46 0x2D), THE PDF_Validator SHALL raise `pdf_extractor.PDFValidationError`
   with a message identifying the magic bytes failure and SHALL NOT perform the
   size, password, or fitz checks.
4. WHEN the file size is zero bytes (checked after the magic bytes check
   passes), THE PDF_Validator SHALL raise `pdf_extractor.PDFValidationError`
   with a message identifying the empty file failure and SHALL NOT perform the
   password or fitz checks.
5. WHEN `fitz.open()` returns a document where `doc.needs_pass` is `True`
   (checked after the size check passes), THE PDF_Validator SHALL raise
   `pdf_extractor.PDFValidationError` with a message identifying the password
   protection failure and SHALL NOT perform the fitz readability check.
6. WHEN `fitz.open()` raises any exception — including `fitz.FileDataError` —
   (checked after the size check passes and the password check does not apply),
   THE PDF_Validator SHALL raise `pdf_extractor.PDFValidationError` with a
   message identifying the fitz readability failure.
7. THE `pdf_extractor.PDFValidationError` exception class SHALL be defined in
   the `pdf_extractor/` package and SHALL be importable as
   `from pdf_extractor import PDFValidationError`.
8. FOR ALL files that fail validation, THE PDF_Validator SHALL raise
   `pdf_extractor.PDFValidationError` at exactly the first failing check and
   SHALL NOT raise any other exception type for validation failures (short-circuit
   ordering invariant: checks fire in the fixed order magic bytes → size →
   password → fitz readability, and the first failure terminates the sequence).

---

### Requirement 8: Pipeline Validator Refactoring

**User Story:** As a developer, I want `pipeline/validator.py` to contain only
LLM JSON parsing logic, so that structural validation is cleanly separated from
LLM output handling.

#### Acceptance Criteria

1. THE `pipeline/validator.py` module SHALL retain `clean_json_string`,
   `_parse_response_json`, `validate_chunk_output`, and `reconstruct_fields`
   after the migration.
2. WHEN `validate_chunk_output` is called with a structurally valid chunk
   output, THE `pipeline/validator.py` module SHALL delegate structural field
   validation to `StructureSchemaValidator.validate_chunk_output` and SHALL
   return the validated list of dicts.
3. IF `StructureSchemaValidator.validate_chunk_output` returns a
   `ValidationResult` with `is_valid` equal to `False`, THEN THE
   `pipeline/validator.py` module SHALL raise `ValidationError` with the
   `errors` list from the `ValidationResult`.
4. THE `validate_qc_context_input` function SHALL be defined in
   `quality_control/` and SHALL delegate to
   `StructureSchemaValidator.validate_qc_bundle` to check: (a) `ctx` is a
   `QCBundle` instance, (b) `ctx.unified` is not `None`, (c)
   `ctx.unified.document_id` is a non-empty `str`, (d) `ctx.unified.content`
   is a `dict`, (e) `ctx.unified.content['exact_text']` is a non-empty `str`.
   IF any of these checks fails, THEN `validate_qc_context_input` SHALL raise
   `ValidationError` with a non-empty `errors` list identifying which check
   failed.
5. ALL import statements in `pipeline/pdf_processor.py` that reference
   `validate_qc_context_input` SHALL import it from `quality_control/`, not
   from `pipeline/validator.py`.
6. THE `pipeline/validator.py` module SHALL NOT define a function or variable
   named `validate_qc_context_input` after the migration (this is the hard
   removal criterion; see also Requirement 10).

---

### Requirement 9: Dependency Direction Enforcement

**User Story:** As a developer, I want the dependency graph between modules to
be clean and free of inversions, so that the architecture remains maintainable
and testable.

#### Acceptance Criteria

1. THE `pdf_extractor` package SHALL NOT be imported by any module within
   `quality_control`.
2. THE `pipeline` package SHALL import from `quality_control` (for
   `StructureSchemaValidator` and `validate_qc_context_input`) and from `agents`
   (for `AgentSchemaValidator`) but SHALL NOT be imported by either.
3. THE `agents/openai` package SHALL import `AgentSchemaValidator` from `agents`
   and SHALL NOT contain any `import` statement — whether at module level, inside
   a function body, via `importlib`, or via `__import__()` — that imports from
   `quality_control`.
4. THE `quality_control` package SHALL NOT import from `agents`, `pipeline`, or
   `pdf_extractor`.
5. THE `agents` package SHALL NOT import from `quality_control`, `pipeline`, or
   `pdf_extractor`.
6. THE dependency direction rules in criteria 1–5 SHALL be verified by a static
   import analysis test in `tests/` that recursively inspects the AST of all
   `.py` files in each package (including sub-packages and `__init__.py` files)
   and asserts no forbidden cross-package import statements exist.

---

### Requirement 10: Hard Migration — No Backwards-Compatibility Shims

**User Story:** As a developer, I want the migration to be clean with no
backwards-compatibility shims, so that the old ad-hoc validation patterns
cannot be accidentally reused.

#### Acceptance Criteria

1. THE System SHALL NOT define a module-level name `SYSTEM_PROMPT` in
   `agents/openai/prompts.py` after the migration.
2. THE System SHALL NOT define a function or variable named
   `validate_qc_context_input` in `pipeline/validator.py` after the migration.
3. THE System SHALL NOT make `SYSTEM_PROMPT` importable from
   `agents.openai.prompts` via any mechanism — including direct assignment,
   `__all__` listing, `from … import` re-export, `importlib.import_module`, or
   PEP 562 `__getattr__` — after the migration.
4. THE System SHALL NOT make `validate_qc_context_input` importable from
   `pipeline.validator` via any mechanism — including direct assignment,
   `__all__` listing, `from … import` re-export, `importlib.import_module`, or
   PEP 562 `__getattr__` — after the migration.
5. THE `agents/openai/api_client.py` module SHALL NOT import a module-level
   constant named `SYSTEM_PROMPT` from any module after the migration.
6. IF `SYSTEM_PROMPT` is removed from `agents/openai/prompts.py` as part of the
   migration, THEN `api_client.py` SHALL obtain the system prompt by calling a
   callable or accessor exposed by `agents/openai/prompts.py` (which internally
   delegates to `agent_schema_validator.get_system_prompt()`), not by importing
   a module-level constant named `SYSTEM_PROMPT` from any module.
