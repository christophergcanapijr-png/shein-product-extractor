import re

file_path = 'backend/services/gemini_service.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Insert SMALL_PLANT_INSTRUCTIONS after PLANT_INSTRUCTIONS
small_plant_inst = '''
SMALL_PLANT_INSTRUCTIONS = \"\"\"
Listing type: SMALL_PLANT

This is a Vinted ad for a small indoor plant, artificial bonsai, or decorative
potted plant. Describe this exact plant, not a dress, bag, or previous item.

Write each title in this exact comma structure, naming only details that
are visible on THIS plant:
1. specific plant type plus one visible detail (artificial bonsai tree, indoor plant, etc);
2. comma, then the colour or visible features (pink flowers, dark trunk, grey pot);
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. comma, then "size S" or "taille S".

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- You MUST include "size S" (English) or "taille S" (French) in the title, not size 36.
- Do not include material in the title.
- Never put a size other than size S, one size, or measurements in the title.

Copy this English pattern exactly:
"Artificial bonsai tree, pink flowers dark trunk grey pot, style elegant, size S"
"Indoor decorative plant, green leaves ceramic pot, style boho bohemian, size S"

Copy this French pattern exactly:
"Bonsaï artificiel, fleurs roses tronc sombre pot gris, style elegant, taille S"
"Plante d'intérieur, feuilles vertes pot en céramique, style boho bohemian, taille S"

Do not write "boho style". Write "style" first, then the style tokens.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Line 3: that language's title exactly again (the title is repeated twice).
- Blank line.
- English: the exact line: price are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- You must not add measurements to the listing (no XX placeholders).

Hashtags:
- 20 to 25 item-specific tags about this plant, colour, pot, and styles.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, old money, y2k,
goth, street, grunge, dark.
Do not generate an image.
\"\"\"
'''

plant_end_str = 'Do not generate an image.\n"""\n'
idx = content.find('PLANT_INSTRUCTIONS =')
if idx != -1:
    end_idx = content.find(plant_end_str, idx)
    if end_idx != -1:
        insert_pos = end_idx + len(plant_end_str)
        content = content[:insert_pos] + '\n' + small_plant_inst + content[insert_pos:]
        print("Inserted SMALL_PLANT_INSTRUCTIONS")

# 2. Update FAST_TYPE_INSTRUCTIONS
fast_small_plant = '''    "small_plant": """
SMALL_PLANT titles MUST copy this comma structure from the product image:
specific plant type with a visible detail, comma, colour or features on
its own, comma, the word "style" then style tokens, then size S.
English models to copy:
"Artificial bonsai tree, pink flowers dark trunk grey pot, style elegant, size S"
French models to copy:
"Bonsaï artificiel, fleurs roses tronc sombre pot gris, style elegant, taille S"
Write "style elegant", never "elegant style" or
"in an elegant style". Never mention material/fabric. Never put measurements in
the title. Name only details visible on this plant. Return empty strings
for english_description and french_description.
The formatter will build this English block with a blank line between each
step: title once, title a second time, "price are negotiable :)",
"Perfect condition." Do not add measurements.
French block: title once, title a second time, "prix négociable :)",
"Parfait état."
Both titles MUST be between 80 and 100 characters by adding another visible
detail from the image, never generic filler.
""",
'''
idx = content.find('FAST_TYPE_INSTRUCTIONS = {')
if idx != -1:
    insert_pos = content.find('\n', idx) + 1
    content = content[:insert_pos] + fast_small_plant + content[insert_pos:]
    print("Inserted small_plant into FAST_TYPE_INSTRUCTIONS")

# 3. Update type_instructions dict
idx = content.find('type_instructions = {')
if idx != -1:
    insert_pos = content.find('\n', idx) + 1
    content = content[:insert_pos] + '        "small_plant": SMALL_PLANT_INSTRUCTIONS,\n' + content[insert_pos:]
    print("Inserted small_plant into type_instructions")

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
