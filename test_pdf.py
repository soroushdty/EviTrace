import sys
import os
import fitz

pdf_path = r"pdfs/Shahn et al. - 2015 - Predicting health outcomes from high-dimensional longitudinal health histories using relational rand.pdf"

if not os.path.exists(pdf_path):
    print(f"File not found: {pdf_path}")
    sys.exit(1)

doc = fitz.open(pdf_path)
text_blocks = 0
image_blocks = 0

for page in doc:
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") == 0:
            text_blocks += 1
        elif block.get("type") == 1:
            image_blocks += 1

print(f"Text blocks: {text_blocks}")
print(f"Image blocks: {image_blocks}")
