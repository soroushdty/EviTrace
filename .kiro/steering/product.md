---
description: Product purpose, value proposition, and core capabilities
---

# EviTrace — Product Steering

## Purpose

EviTrace is an automated literature review pipeline for biomedical research PDFs. It ingests scientific papers and produces auditable, structured JSON records with extracted metadata and confidence-graded evidence citations tied to source text.

## Target Users

Clinical researchers and evidence synthesis teams conducting scoping reviews, systematic reviews, or clinical evidence mapping — particularly those performing manual literature curation for clinical decision-making or research synthesis.

## Problem Being Solved

Manual extraction of study attributes from scientific papers is error-prone, time-consuming, and difficult to audit. EviTrace automates this using multi-stage PDF extraction, quality-control adjudication, and LLM-based structured extraction with source-anchored confidence scoring.

## Core Capabilities

**Evidence Grounding** — Every extracted field includes a citation of its source text, a confidence tier (high/medium/low/not-reported), and a flag indicating whether it required synthesis across sources.

**Multi-Backend PDF Extraction** — Cascading extraction tiers (PyMuPDF, pdfplumber, PaddleOCR) with GROBID as a parallel semantic branch for cross-branch quality comparison and adjudication.

**Chunked LLM Extraction** — Domain-scoped parallel chunks (fields 1..N-1) with a final synthesis chunk receiving prior results as context. Structured JSON-Schema outputs with local field-index validation.

**Prompt Caching** — Per-PDF cache warmup preloads the system prompt and shared paper text, reducing cost and latency across runs.

**Idempotent Checkpointing** — `manifest.json` tracks per-PDF processing status; partial runs resume safely after interruptions.

**QC Reporting** — Cross-paper CSV output flags low-confidence or missing fields for human review.

## Key Design Principle

Each pipeline stage (extraction, QC, LLM agent) is independently swappable and can be used standalone outside the end-to-end workflow.
