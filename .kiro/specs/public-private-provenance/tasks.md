# Implementation Plan

- [ ] 1. Foundation: package skeleton, configuration, vocabularies, and canonicalization

- [ ] 1.1 Register the disclosure configuration block and run-scoped output path
  - Add the `disclosure` defaults mapping and register `"disclosure"` as a known top-level configuration key so an unknown-key check does not reject it
  - Add a loader that returns the resolved disclosure settings, covering the enable flag, the public-view subdirectory and level-disposition table, the vault settings, the integrity algorithm and manifest flag, and the transaction settings
  - Add the run-scoped public output directory constant alongside the existing run-scoped path helpers
  - Add the `disclosure:` block to the shipped configuration file with the enable flag defaulted off and no vault path
  - Observable: loading the shipped configuration returns the documented defaults, and a configuration containing the `disclosure` key no longer raises an unknown-key error
  - _Requirements: 1.7, 5.7, 6.6, 6.7_
  - _Boundary: Configuration_

- [ ] 1.2 Create the disclosure package skeleton, error hierarchy, and dependency-direction rules
  - Create the package with its public surface module and the typed error hierarchy rooted at a disclosure error, with a fail-closed base class carrying a named basis for every exclusion condition
  - Add the twelve forbidden dependency pairs plus one named test per pair, and an allowlist test asserting the package imports only utilities, provenance, provenance export, and one privacy constant
  - Observable: the dependency-direction suite passes with the new pairs present, and importing the package succeeds with pipeline, agents, and quality control absent from the module table
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_
  - _Boundary: DisclosureErrors_

- [ ] 1.3 Define every disclosure vocabulary, shared record, and schema constant
  - Declare the closed vocabularies for node disposition, disposition basis, disagreement kind, ledger subject kind, hash algorithm identifier, and verification outcome
  - Declare the frozen records for disposition, safe reference, commitment, omission marker, public node, reduction counts, public view, linkage entry and record, ledger entry and ledger, proof artifact, integrity manifest, disclosure transaction, verification report, and proof semantics
  - Declare the three schema identity and version constants, the three scheme identity constants, and the most-restrictive-disposition constant
  - Import the anti-overclaiming statement from the privacy subsystem rather than authoring a second one, and reject namespaced extension keys that do not match the declared pattern
  - Observable: a vocabulary test enumerates every literal union member, and a test asserts the disclaimer is imported and not duplicated as a literal anywhere in the package
  - _Requirements: 1.4, 1.5, 2.3, 3.1, 4.4, 8.1, 9.2, 9.3, 9.5, 10.2, 10.3_
  - _Boundary: DisclosureModels_

- [ ] 1.4 Implement canonical byte encoding and the hash algorithm registry
  - Provide a deterministic byte encoding with sorted keys, stable text encoding, and a declared set of volatile fields excluded from hashing, together with a canonicalization identifier recorded on every artifact that hashes
  - Provide a registry containing only collision-resistant algorithms, with a default, and no path by which a weak algorithm can be selected through configuration
  - Observable: digests are stable across processes for equal inputs, two values differing only in volatile fields digest identically, and a registry test asserts the exact algorithm set
  - _Requirements: 6.6, 6.7, 7.2_
  - _Boundary: Canonicalization_

- [ ] 2. Consultation and private storage

- [ ] 2.1 (P) Implement carrier consultation and disposition derivation
  - Derive a per-node disposition by reading only the carrier's state, decision, supplier, and supply time, and never the sensitivity label
  - Resolve a decision to a sharing level using the mapping supplied by the audit-export configuration, and define only the level-to-disposition table on top of it
  - Apply the most restrictive disposition with a recorded basis when no decision exists, when the decision is absent from the supplied mapping, and when consultation raises
  - Detect the three enumerated disagreement conditions, assign the requires-review disposition, record the disagreement kind, and raise the run-level review flag
  - Observable: a table-driven test covers every state-machine row, a source-level test asserts the module never accesses the carrier's label field, and a test asserts no transition leads from a fail-closed basis to an including disposition
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 11.1, 11.2_
  - _Boundary: ConsultationService_

- [ ] 2.2 (P) Implement the vault protocol, file-backed store, and access log
  - Define the vault store protocol, the authorization check protocol, and the entry-kind vocabulary, and implement a file-backed store writing entries under an operator-configured directory
  - Refuse at construction any vault path resolving inside the repository root or under the run output directory, reporting the offending path and offering no fallback location
  - Append an access record identifying the entry, the operator, the time, and the outcome before returning from every resolution attempt, including denials, and return nothing on denial
  - Generate salts inside the store and never expose them outside it; serialize writes and appends behind a single lock
  - Ingest the surrogate handoff artifact produced by the transformation subsystem into pseudonym-mapping entries, reading it by path and importing nothing from that package, validating the producer literal and the schema major version before writing any entry, and recording the run-scoped stability declaration the artifact carries
  - Treat a missing, unreadable, wrongly-attributed, or major-version-mismatched handoff artifact as a fail-closed condition yielding zero entries and a named reason, never as a run that produced no surrogates and never as a partial ingest
  - Observable: a store constructed under a temporary directory logs granted and denied accesses, a path under the repository or the output directory is refused with a named error, no test writes a vault inside the repository, a fixture handoff artifact matching the producer's pinned schema yields one entry per scope, each of the absent, wrong-producer, and bumped-major cases yields zero entries with a named reason, and a boundary test confirms the disclosure package imports nothing from the transformation package
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.8, 11.3_
  - _Boundary: VaultService_

- [ ] 2.3 (P) Implement safe reference construction and resolution
  - Derive an opaque reference from the per-run salt and the node identifier under the configured algorithm, writing the reverse mapping to the vault before returning the reference
  - Make the reference deterministic within a run and different across runs with different salts, and carry the reference scheme identity and version on every reference
  - Resolve a reference back to its node identifier only through the vault's authorized path
  - Observable: a property test asserts a reference contains no four-character-or-longer substring of the node identifier and that equal inputs yield equal references; resolution succeeds for an authorized operator and raises for an unauthorized one
  - _Requirements: 3.2, 3.5, 3.6, 5.1, 9.2_
  - _Boundary: SafeReferenceBuilder_
  - _Depends: 2.2_

- [ ] 2.4 (P) Implement salted-hash commitments with vaulted openings
  - Produce a commitment over the canonical bytes of the content prefixed by a per-commitment salt, writing the salt and the opening to the vault before the commitment value is returned
  - Carry the commitment scheme identity, version, algorithm, and canonicalization on every commitment, together with a proof-semantics block naming what the commitment establishes and what it does not
  - Provide opening and verification entry points, and raise a fail-closed error when the opening cannot be stored so that no commitment is emitted without recoverable opening material
  - Observable: a round-trip test opens a commitment successfully, a single changed content byte fails verification, and a simulated vault write failure produces no commitment object at all
  - _Requirements: 3.3, 3.7, 5.2, 9.2, 10.1, 10.2, 10.5_
  - _Boundary: CommitmentBuilder_
  - _Depends: 2.2_

- [ ] 3. Public and private view generation

- [ ] 3.1 Implement the public view projector with explicit omission markers and reduction counts
  - Project every graph node into exactly one public node whose identifier is a safe reference rather than a provenance identifier, preserving edge kinds between public identifiers so claim, evidence, and source relationships survive
  - Replace every excluded element with an omission marker carrying the node kind, the basis, and a restricted-evidence-exists flag, never by deleting the element
  - Populate content only for the include-in-full disposition, fall back from an unavailable commitment to an omission marker with a recorded reason, and compute reduction counts across the five dispositions
  - Carry the run-level sharing marking supplied by the audit-export subsystem verbatim, set the restricted-evidence-exists indicator on the view, leave the graph unmodified, and reduce a failing element without stopping the others
  - Observable: node count parity with the graph holds, counts sum to the node total, the serialized view contains no substring of the source text for any node not included in full, and a whole-view assembly failure results in no artifact being produced
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 3.1, 3.4, 3.7, 11.2, 11.6, 11.7_
  - _Boundary: PublicViewProjector_
  - _Depends: 2.1, 2.3, 2.4_

- [ ] 3.2 Implement the private view and the auditable public-to-private linkage
  - Build a private view retaining every available source anchor so an authorized operator can resolve an evidence reference back to page, span, and structural path, storing anchors that must not sit in the run output tree in the vault
  - Emit one linkage entry per public element naming the public identifier, the private node identifier, the disposition applied, and the authorizing decision, and preserve the private-to-public relationship where a transformation was recorded upstream
  - Record any public element with no resolvable private counterpart as unlinked with a reason, and write the linkage record to the vault rather than to the run output tree
  - Observable: every public element appears in the linkage entries or the unlinked list and never in both, and the serialized linkage record contains no evidence text and no anchor field
  - _Requirements: 1.3, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_
  - _Boundary: PrivateViewBuilder_

- [ ] 4. Integrity ledger, manifest, transaction, and verification

- [ ] 4.1 Implement the ordered chained integrity ledger and its verification
  - Order entries from the provenance subsystem's existing ordered event view, then the covered artifact files, then the two views, maintaining no second ordering of run events
  - Chain each entry's marker to its predecessor's so that modifying any entry invalidates every later one, and expose the final marker as a chain head named as a chain head rather than as a tree root
  - Reference existing upstream artifact and content digests rather than recomputing them, and compute a digest only for content that has none
  - Report an incomplete ledger with a reason when any covered subject cannot be digested, and report such a run as unverifiable rather than verified
  - Observable: verification of an unmodified ledger succeeds, mutating one entry's content digest yields a report naming that exact sequence and subject, and a source-level test asserts the module computes no document, configuration, schema, or artifact-file fingerprint of its own
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.8, 11.4_
  - _Boundary: IntegrityLedgerService_
  - _Depends: 3.2_

- [ ] 4.2 (P) Assemble the integrity manifest with proof semantics and non-performable checks
  - Assemble the manifest from the ledger, the covered-artifact digest map, the algorithm, the canonicalization identifier, and the schema identity and version needed to reproduce verification outside the runtime
  - Declare the proof-artifact slot as an explicit empty list rather than an absent field, and list the checks that cannot be performed from the export alone
  - Attach a proof-semantics block with a non-empty statement of what the manifest does not establish, and embed the anti-overclaiming statement imported from the privacy subsystem
  - Observable: a manifest built over a fabricated run names its algorithm, canonicalization, schema identity and version, reports an empty proof slot explicitly, and carries a non-empty does-not-establish list
  - _Requirements: 7.1, 7.2, 9.1, 9.2, 9.3, 9.6, 10.1, 10.2, 10.3, 10.6_
  - _Boundary: IntegrityLedgerService, ProofSemanticsModule_

- [ ] 4.3 (P) Implement the disclosure transaction and the symmetric integrity marking
  - Record a transaction containing the private root marker, the public root marker, the authorizing decision identifiers, the policy profile identities, and the creation time, and append the finalized transaction to the ledger as its final entry
  - Define the signer protocol and ship a symmetric marking implementation that receives an already-resolved marking callable and a non-secret key-version identifier, importing nothing from the secrets subsystem
  - Record explicitly whether the resulting marking can be verified without the key, setting it false for the shipped symmetric scheme, and raise rather than emitting an apparently signed transaction when signing is enabled but unavailable
  - Observable: an unsigned transaction reports no signature and no external verifiability, a marked transaction reports its key version and false external verifiability, signing failure raises, and a source-level test asserts no module in the package imports the secrets module or reveals a secret handle
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 9.2_
  - _Boundary: TransactionService_

- [ ] 4.4 Implement runtime-independent verification of an exported artifact set
  - Verify from a manifest path and a base directory alone, recomputing each covered artifact's digest under the manifest's declared algorithm and canonicalization and rechaining the ledger, with no graph, vault, or runtime state required
  - Report success with the covered set enumerated, failure with the artifact and the first divergent position identified, and an absent covered artifact as uncovered rather than verified
  - Report declared non-performable checks as not performable rather than silently skipping them, and refuse to interpret a manifest declaring an unrecognized scheme or an unsupported schema major version
  - Observable: verification of an untouched artifact set reports success and enumerates coverage, a one-byte modification reports failure at the expected position, a deleted artifact reports as uncovered, and an unrecognized scheme raises a named error without partial interpretation
  - _Requirements: 7.3, 7.4, 7.5, 7.6, 9.4_
  - _Boundary: VerificationService_
  - _Depends: 4.2, 4.3_

- [ ] 5. Persistence

- [ ] 5.1 Implement canonical, atomic, version-guarded artifact serialization
  - Serialize and deserialize the public view, the integrity manifest, and the disclosure transaction using the canonical encoding, writing through a temporary file and an atomic replace into the run-scoped public directory
  - Embed schema identity, schema version, scheme identities, and the proof-semantics block in every artifact, preserve unknown namespaced extension keys across a round trip, and report a differing schema major version rather than interpreting it
  - Exclude the vault, the linkage record, and private anchors from anything written under the run output tree
  - Observable: a round trip returns an equal record including namespaced extensions, a repeated write of an equal artifact produces a byte-identical file, and a differing schema major version raises a named error
  - _Requirements: 7.2, 9.2, 9.3, 9.4, 9.5_
  - _Boundary: Serialization_
  - _Depends: 4.4_

- [ ] 6. Integration

- [ ] 6.1 Wire the vault store, authorization check, and transaction signer from the pipeline
  - Build the vault store from configuration by class path, applying the location refusal rule at construction and reporting the condition when no vault path is configured
  - Resolve the signing key through the privacy subsystem's managed secret path, close over the marking operation, and hand the transaction builder a marking callable and a key-version identifier so no secret handle enters the disclosure package
  - Ingest the run's surrogate handoff artifact into the constructed vault before any view is built, passing its path explicitly and recording the ingest outcome in the run-level disclosure state
  - Observable: a configured vault and signer are constructed and passed explicitly, a missing vault path leaves disclosure disabled with a recorded reason, an ingested handoff artifact is reflected in the run-level state and an absent one is recorded with its reason without failing the run, and a boundary test confirms this is the only module on this path that touches a secret handle
  - _Requirements: 5.6, 5.7, 8.3, 8.6_
  - _Boundary: DisclosureWiring_

- [ ] 6.2 Sequence disclosure generation into the run and report the run-level disclosure state
  - Run disclosure after the audit-export artifacts have been written, so the ledger can cover their digests and the public view can carry their sharing marking, altering none of them
  - Construct the vault, consultation inputs, and signer once per run and pass them explicitly, then consult, build both views, build the ledger and manifest, record the transaction, and write the artifacts
  - Produce no public artifact and record that no disclosure view was generated, with the reason, when disclosure is disabled or no vault is configured, and never abort extraction on a disclosure failure
  - Observable: a governed run writes the three artifacts under the run-scoped public directory and records the reduction counts and chain head, while a disabled run writes nothing there and records the reason
  - _Requirements: 1.1, 1.7, 8.7, 11.6, 11.7_
  - _Boundary: PipelineIntegration_
  - _Depends: 5.1, 6.1_

- [ ] 7. Validation

- [ ] 7.1 (P) Add boundary and inertness regression tests
  - Assert no module in the package reads, compares, or branches on the carrier's sensitivity label, mirroring the equivalent guards in the two upstream subsystems
  - Assert no module in the package imports the secrets module, holds a secret handle, or reveals one, and that no module computes a document, configuration, schema, or artifact-file fingerprint of its own
  - Assert the package imports cleanly with pipeline, agents, and quality control absent from the module table and performs no network access
  - Observable: the boundary suite passes and fails when a deliberately introduced label access, secret import, or fingerprint computation is added
  - _Requirements: 2.5, 2.6, 6.5, 8.5_
  - _Boundary: Validation_

- [ ] 7.2 (P) Add the anti-overclaiming scan over the strings this spec ships
  - Scan the package source, the disclosure configuration block, and the three written artifacts using the prohibited-term list owned by the privacy subsystem, extended with the terms this design does not support
  - Assert the anti-overclaiming statement is imported rather than duplicated and is embedded in every written artifact, and that every proof-semantics record carries a non-empty statement of what it does not establish
  - Observable: the scan passes over the shipped strings and fails when a claim of compliance, certification, anonymization, tamper-proofing, tree-based inclusion proofs, or zero-knowledge proving is introduced
  - _Requirements: 10.3, 10.4, 10.5, 10.6_
  - _Boundary: AntiOverclaimingCheck_

- [ ] 7.3 Add end-to-end, fail-closed, and property coverage
  - Exercise a full run over a fabricated graph with a temporary vault: three artifacts written, verification succeeding with coverage enumerated, a one-byte tamper failing at the expected position, and a deleted artifact reported as uncovered
  - Exercise the fail-closed paths: a graph with no privacy carriers yields a view of nothing but omission markers with the restricted-evidence indicator set and no content; a refused vault location disables disclosure; an unauthorized resolution returns nothing and still logs
  - Add property coverage for reference opacity and determinism, canonical encoding stability under key reordering and volatile-field changes, and the guarantee that no disposition is more permissive than the most restrictive default unless a decision resolved to a level
  - Observable: the end-to-end and fail-closed suites pass, and the property suite finds no counterexample to reference opacity, digest stability, or the disposition permissiveness bound
  - _Requirements: 1.1, 2.4, 3.4, 3.6, 6.3, 7.3, 7.4, 7.5, 11.3, 11.5, 11.6_
  - _Boundary: Validation_
  - _Depends: 6.2_
