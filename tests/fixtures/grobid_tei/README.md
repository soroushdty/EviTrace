# Real GROBID TEI fixtures

Genuine `processFulltextDocument` output from GROBID **0.8.2** (`lfoppiano/grobid:0.8.2-crf`,
build `a91ee48`), captured 2026-09-21 for the `risk-remediation` spec (Requirements 7 and 11).
They exist because every hand-built TEI fixture in this repository mis-models GROBID: hand-built
fixtures nest `<figure>`/`<table>` inside a `<div>` and use `page;x0,y0,x1,y1` coordinates,
whereas real GROBID emits figures and tables as `<body>` siblings *after* the last `<div>`,
wraps tables as `<figure type="table"><table/></figure>`, and emits coordinates as
`page,x,y,w,h` boxes separated by `;`.

| File | Source (open access) | Figures | Tables |
|---|---|---|---|
| `biorxiv_2020.03.24.004655.tei.xml` | bioRxiv 10.1101/2020.03.24.004655 | 12 (5 caption-less) | 0 |
| `arxiv_2003.10218.tei.xml` | arXiv 2003.10218 | 10 | 3 |
| `plosone_journal.pone.0230405.tei.xml` | PLOS ONE 10.1371/journal.pone.0230405 | 9 | 1 |

Request parameters: the pipeline's `extract_with_grobid` form data (`consolidateHeader=0`,
`generateIDs=1`, etc.) **except** that `teiCoordinates` was sent as repeated form fields
(`-F teiCoordinates=p -F teiCoordinates=figure -F teiCoordinates=formula -F teiCoordinates=head
-F teiCoordinates=biblStruct`). The pipeline itself sends one comma-joined value, which GROBID
ignores, so production TEI currently has no `coords` — see Requirement 11. Structure is otherwise
identical to production output.

Do not edit these files by hand; regenerate against the same GROBID version if they must change.
