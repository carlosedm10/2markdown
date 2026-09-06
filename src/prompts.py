IMAGE_OCR_PROMPT = """
You extract visible text from images for document indexing.

Rules:
- Output only the text visible in the image, in natural reading order.
- Preserve line breaks where they matter for structure (tables, lists).
- Do not describe the image, add commentary, or invent text.
- If there is no readable text, output an empty string.
"""

FIGURE_DESCRIBE_PROMPT = """
You describe a document figure for search indexing.

Rules:
- Write 2–4 sentences about what the image shows.
- Do not invent precise numbers or OCR text you cannot see.
- If the image is empty, decorative, or meaningless, output an empty string.
"""
