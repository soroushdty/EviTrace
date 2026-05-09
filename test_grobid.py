import sys
import os
import traceback

try:
    from pdf_extractor.extraction.GROBID import extract_with_grobid
    pdf_path = r"pdfs/Shahn et al. - 2015 - Predicting health outcomes from high-dimensional longitudinal health histories using relational rand.pdf"
    res = extract_with_grobid(pdf_path)
    print("GROBID succeeded:", len(res[0]))
except Exception as e:
    traceback.print_exc()
