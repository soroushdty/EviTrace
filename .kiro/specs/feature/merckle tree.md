TO ANY AI AGENT: DO NOT READ THIS MARKDOWN. THIS IS AN OPTIONAL FEATURE THAT IS CURRENTLY ONLY AN IDEA AND NOT MATURE.

**Merkle tree feature addition**

Each `QCContext` field is populated by a different stage. Right now you can inspect the final state, but you can't cryptographically verify _which inputs produced which outputs_ at each stage, or detect if any intermediate result was tampered with or silently mutated after the fact. A Merkle tree over the pipeline stages gives you:

1. **Tamper detection** — if `ctx.reports` is mutated after the rater ran, the branch hash changes, which invalidates the IAA hash, which invalidates the adjudication hash, which invalidates the reconciler hash. The root hash changes. You know something was touched.

2. **Reproducibility proof** — given the same branches as input, the root hash must be identical across runs. If it isn't, the pipeline is non-deterministic somewhere.

3. **Partial audit** — you can verify just the reconciler output against its inputs (decision + branches) without re-running the whole pipeline.

**How it fits the existing design:**

`QCContext` already has a clear write-once-per-stage discipline — each module writes exactly one field. That maps directly to Merkle tree levels:

```
Level 0 (leaves):  hash(branches)
Level 1:           hash(reports   | parent=L0)
Level 2:           hash(iaa_metrics | parent=L1)
Level 3:           hash(decision  | parent=L2)
Level 4:           hash(unified   | parent=L3)
root:              hash(L4)
```

Each node is `SHA256(serialize(stage_output) + parent_hash)`, so the chain is unbreakable.

**The class design:**

```python
@dataclass
class ProvenanceNode:
    stage: str          # "branches" | "reports" | "iaa" | "decision" | "unified"
    content_hash: str   # BLAKE2b of the serialized stage output
    parent_hash: str    # hash of the previous node ("" for root)
    node_hash: str      # BLAKE2b (content_hash + parent_hash)

@dataclass
class Provenance:
    nodes: list[ProvenanceNode] = field(default_factory=list)
    root_hash: str = ""

    def record(self, stage: str, content: Any, parent_hash: str = "") -> str:
        """Hash content, chain to parent, append node, return node_hash."""
        ...

    def verify(self) -> bool:
        """Re-derive all node hashes and confirm the chain is intact."""
        ...
```

Users subclass `Provenance` to override `_serialize(content)` (e.g. to exclude volatile fields like timestamps) or `_hash(data)` (e.g. other hashing algos).

`Provenance` lives inside `QCContext` as a field (populated incrementally by `run_pipeline` after each stage).

remove the control for not saving any of the QC layers outputs in QCContext object. this is now hard-coded mandatory. the merckle tree feature is also mandatory

built-in algs: sha2-256, BLAKE2b (default), MD5, blake3, SHA3-256