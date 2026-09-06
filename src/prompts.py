IMAGE_OCR_PROMPT = """
You extract visible text from images for document indexing.

Rules:
- Output only the text visible in the image, in natural reading order.
- Preserve line breaks where they matter for structure (tables, lists).
- Do not describe the image, add commentary, or invent text.
- If there is no readable text, output an empty string.
"""

FIGURE_DESCRIBE_PROMPT = """
You describe figures and images for document indexing and search.

Rules:
- Write 2–4 sentences describing what the figure shows (charts, diagrams, photos, UI, etc.).
- Do not transcribe or invent OCR text; focus on visual content and meaning.
- If the image has no meaningful content (blank, noise, or unreadable), output an empty string.
"""
