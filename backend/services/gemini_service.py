from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import unicodedata
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.config import settings
from backend.errors import AppError
from backend.schemas import ListingPair, ListingVersion, clip_listing_title
from backend.services.image_service import (
    candidate_reference_image_urls,
    fetch_image,
    prepare_image_for_gemini,
    read_local_download_image,
)


logger = logging.getLogger(__name__)

INVENTED_BRANDS = (
    "Avelisse",
    "Rovelia",
    "Lunara",
    "Elvoria",
    "Merelle",
    "Sovelia",
    "Cirelle",
    "Vanora",
    "Orvessa",
    "Belloria",
    "Nuvessa",
    "Arlune",
    "Veloria",
    "Caelora",
    "Solvane",
    "Mirane",
    "Elaris",
    "Novelle",
)

GENERIC_OR_REPEATED_BRANDS = {
    "velmora",
    "aetheria",
    "brand",
    "fictionalbrand",
}

STYLE_BRAND_TOKENS = (
    "boho",
    "bohemian",
    "chic",
    "elegant",
    "oldmoney",
    "y2k",
    "goth",
    "street",
    "grunge",
    "dark",
)

BASE_INSTRUCTIONS = """
You write polished Vinted-style resale listings from the verified product facts
and product image supplied by the application. Return one English listing, one
fully French listing, and one newly coined fictional brand name.

Rules shared by every listing:
- Return only the requested structured JSON.
- Titles must be exactly one natural-sounding line, contain no material/fabric,
  and target 80 to 100 characters without ever exceeding 100 characters.
- To reach that length, use only visible item details from the product image
  such as cut, neckline, sleeve, shape, print, drape, or fastening. Do not add
  generic filler phrases.
- Never invent or add a detail merely to make a title longer.
- Keep the literal word "style" in both English and French titles.
- Descriptions must not mention material/fabric.
- The English condition line must be exactly "Perfect condition."
- The French condition line must be exactly "Parfait état."
- Each language must have 20 to 25 relevant hashtags.
- Hashtags must not use Vinted, France, new, brandnew, unused, neuf, neuve,
  nouveau, nouvelle, or close synonyms of those words.
- Do not invent measurements or factual product features. Use a measurement
  only when it appears in the verified facts.
- The fictional_brand must be one professional-sounding invented word with no
  spaces. Coin a new name rather than using a known brand.
- Do not generate an image. The supplied image is reference input only.
"""

VISUAL_ACCURACY_INSTRUCTIONS = """
Strict accuracy rules:
- Treat the attached product image as the highest authority for item type,
  colour, print, neckline, sleeve shape, length, silhouette, and visible design.
- Never describe a colour, pattern, cut, sleeve, neckline, object, or accessory
  unless it is plainly visible in the attached product image or verified facts.
- If page text, product title, category, or colour conflicts with the attached
  product image, ignore the conflicting text and follow the attached image.
- Do not reuse details from a previous product, previous generation, template
  example, or another SKU. Each response must describe only the current item.
- Do not invent romantic marketing paragraphs, wearer descriptions, occasions,
  materials, brands, body shape claims, or styling claims.
- Do not mention bags, shoes, jewelry, coats, skulls, sequins, lace, flowers,
  sleeves, straps, collars, or prints unless they are actually visible on this
  item.
- If uncertain about a visual detail, leave it out and use a simpler accurate
  category/colour/style title.
"""

DRESS_INSTRUCTIONS = """
Listing type: DRESS

Write each title in this exact comma structure, naming only details that are
visible on THIS dress:
1. specific dress type plus one visible construction detail, then "with"
   plus another visible detail;
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36). Never mention material/fabric.

Copy this English pattern exactly:
"Long off-shoulder dress with ruffle details, black, style gothic elegant size S"
"Lace maxi dress with tiered details, cream white, style bohemian elegant size S"
"Asymmetric maxi dress with color block panels, black brown and beige, style sophisticated size S"

Copy this French pattern exactly:
"Robe longue à épaules dénudées avec volants, noire, style gothic elegant taille S"
"Robe maxi en dentelle à étages, blanc crème, style bohemian elegant taille S"
"Robe maxi asymétrique à panneaux color-block, noir marron et beige, style sophisticated taille S"

Do not write "elegant style" or "in an elegant style". Write "style" first,
then the style tokens. The French title must put the category first:
"Robe longue", never "Longue robe".

The listing title field is one copy of the title. The description may repeat
that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step:
- Line 1: that language's title exactly, once.
- Blank line.
- Size S measurements from verified facts, if present.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- Final line: the exact required condition sentence.
- Do not add a second title line or a descriptive paragraph.
- Never invent, round, convert, or omit an extracted measurement.
"""

DRESS_M_INSTRUCTIONS = (
    DRESS_INSTRUCTIONS.replace("Listing type: DRESS", "Listing type: DRESS M")
    .replace("size S", "size M")
    .replace("taille S", "taille M")
    .replace("Size S", "Size M")
)

EARRINGS_INSTRUCTIONS = """
Listing type: EARRINGS

For each title, follow this exact order:
1. earrings category with specific visible/relevant details such as drop,
   floral, hoop, statement, or shape when supported;
2. colour;
3. the word "style" followed by one or more best-matching choices from only:
   boho, bohemian, elegant, western, cowboy, y2k, gothic, streetwear, grunge,
   Wild West, cowgirl, sophisticated;
4. one size fits all in English, or taille unique in French.

For each description:
- First line: repeat that language's title exactly.
- If verified earring length or width is supplied, add a blank line followed
  by one concise measurement line containing only those verified values.
- Add a blank line followed by the exact required condition sentence.
- Never output XX placeholders or invent missing measurements.
"""

HAT_INSTRUCTIONS = """
Listing type: HAT

For each title, follow this exact order:
1. hat category with specific visible/relevant details such as cowboy hat,
   Western hat, wide-brim hat, bucket hat, cap, or decorative shape when
   supported;
2. colour;
3. the word "style" followed by one or more best-matching choices from only:
   boho, bohemian, elegant, western, cowboy, y2k, gothic, streetwear, grunge,
   Wild West, cowgirl, sophisticated;
4. one size fits all in English, or taille unique in French.

For each description:
- First line: repeat that language's title exactly.
- If verified hat length or width is supplied, add a blank line followed by
  one concise measurement line containing only those verified values.
- Do not add a descriptive paragraph.
- Blank line.
- English: add the exact line: prices are negotiable :)
- French: add the exact line: prix négociable :)
- Blank line.
- Add the exact required condition sentence.
- Never output XX placeholders or invent missing measurements.

For the fictional_brand schema field, return exactly one clothing style token
instead of a brand: boho, elegant, western, cowboy, cowgirl, y2k, streetwear,
grunge, wildwest, sophisticated.
"""

MASK_INSTRUCTIONS = """
Listing type: MASK

This is a women's Vinted ad for a face mask / masquerade mask / costume mask.
Describe this exact mask, not a bag, clutch, hat, coat, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS mask:
1. specific mask type plus one visible construction detail, then "with"
   plus another visible detail (sequin, masquerade, lace, feather, cutout,
   party, cat, butterfly). Never mention a belt;
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, western, cowboy, y2k, gothic, streetwear, grunge,
   Wild West, cowgirl, sophisticated;
4. one size fits all in English, or taille unique in French.
Never mention material/fabric such as leather, faux leather, polyester, or lace
unless lace is a visible construction detail of the mask itself.
Never write "Halloween costume style". Write "style" first, then the style tokens.
Never put size S, size 36, or measurements in the title.

Copy this English pattern exactly:
"Sequin masquerade mask with cutout details, black, style gothic y2k, one size fits all"
"Lace party mask with feather details, black, style elegant gothic, one size fits all"

Copy this French pattern exactly:
"Masque de soirée à paillettes avec découpes, noir, style gothic y2k, taille unique"
"Masque festif en dentelle avec plumes, noir, style elegant gothic, taille unique"

Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler. Exactly one line.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified length and/or width from facts if present. Never invent XX
  placeholders. Never add bust, waist, or other body measurements.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 item-specific tags about this mask, colour, cut, and styles.
  Include tags such as mask, womensmask, sequinmask, masquerademask, oneSize
  when they match the item. Never use bag, clutch, coat, cape, size36, or
  condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style token
instead of a brand: boho, elegant, western, cowboy, cowgirl, y2k, streetwear,
grunge, wildwest, sophisticated.
Do not generate an image.
"""

BAG_INSTRUCTIONS = """
Listing type: BAG

This is a women's Vinted ad for a handbag / shoulder bag / tote / crossbody.
Describe this exact bag, not a coat, dress, skirt, jeans, or previous item.

Write each title in this exact comma structure, naming only details that
are visible on THIS bag:
1. specific bag type plus one visible construction detail, then "with"
   plus another visible detail (handbag, shoulder bag, crossbody, tote,
   clutch, chain strap, buckle, zipper, flap, handle, structured);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. comma, then ALWAYS end with one size fits all / taille unique.
Never mention material/fabric such as leather, faux leather, suede, or
polyester. Never put size S, size M, size 38, or measurements in the title.

Copy this English pattern exactly:
"Women's shoulder bag with chain strap, black, style y2k gothic, one size fits all"
"Structured handbag with buckle details, cream, style elegant old money, one size fits all"
"Crossbody bag with zipper details, brown, style streetwear grunge, one size fits all"

Copy this French pattern exactly:
"Sac bandoulière femme avec chaîne, noir, style y2k gothic, taille unique"
"Sac à main structuré avec boucle, crème, style elegant old money, taille unique"
"Sac croisé avec zip, marron, style streetwear grunge, taille unique"

Do not write "y2k style" or "in a vintage Y2K style". Write "style" first,
then the style tokens: "style y2k gothic". The ending "one size fits all" /
"taille unique" is mandatory.
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Do not mention material/fabric.
- Do not use size S, size M, or size 38.
- Always finish with "one size fits all" / "taille unique".

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified length/width measurements if present in the product facts,
  never invented "XX cm" placeholders.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not invent measurements; only include them if verified.
- Do not add material/fabric.

Hashtags:
- 20 to 25 item-specific tags about this bag, colour, shape, and styles.
  Include tags such as handbag, womenshandbag, shoulderbag, y2k when they
  match the item.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

PLANT_INSTRUCTIONS = """
Listing type: PLANT

This is a Vinted ad for an indoor plant, artificial plant, or decorative
potted plant. Describe this exact plant, not a dress, bag, or previous item.

Write each title in this exact comma structure, naming only details that
are visible on THIS plant:
1. specific plant type plus one visible detail, then "with" plus another
   visible detail (indoor plant, succulent, cactus, hanging plant,
   decorative pot, planter, leafy, trailing);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated.
Never put a size, one size, or measurements in the title.

Copy this English pattern exactly:
"Indoor plant with decorative pot, green, style boho elegant"
"Artificial succulent with ceramic planter, cream, style chic boho"
"Hanging plant with trailing leaves, green, style bohemian"

Copy this French pattern exactly:
"Plante d'intérieur avec pot décoratif, vert, style boho elegant"
"Succulente artificielle avec cache-pot, crème, style chic boho"
"Plante retombante avec feuilles, vert, style bohemian"

Do not write "boho style". Write "style" first, then the style tokens.
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Do not use size S, size M, size 38, one size, or taille unique.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add measurements, XX placeholders, a second title line, or a
  descriptive paragraph.

Hashtags:
- 20 to 25 item-specific tags about this plant, colour, pot, and styles.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""


SMALL_PLANT_INSTRUCTIONS = """
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
"""

SHELF_INSTRUCTIONS = """
Listing type: SHELF

This is a Vinted ad for a wall shelf / floating shelf / set of shelves.
Describe this exact shelf, not a bag, plant, coat, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS shelf:
1. specific shelf type plus one visible construction detail, then "with"
   plus another visible detail (set of 4, wall, floating, simple, rustic,
   bracket, storage). Never mention a belt;
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, vintage;
4. one size in English, or taille unique in French.
Never mention material/fabric such as wood, MDF, metal, or laminate.
Never write "vintage-style" or "vintage style". Write "style" first, then
the style tokens. Never put size S, size 36, or measurements in the title.

Copy this English pattern exactly:
"Set of 4 wall shelves with simple cut, brown, style vintage, one size"
"Floating wall shelf with bracket details, cream, style boho elegant, one size"

Copy this French pattern exactly:
"Lot de 4 étagères murales simples, marron, style vintage, taille unique"
"Étagère murale flottante avec supports, crème, style boho elegant, taille unique"

Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler. Exactly one line.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified length and/or width from facts if present. Never invent XX
  placeholders. Never add bust, waist, or other body measurements.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 item-specific tags about this shelf, colour, set, and styles.
  Include tags such as shelf, wallshelf, vintageshelf, oneSize when they
  match the item. Never use bag, clutch, handbag, coat, size36, or
  condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, invent one newly coined professional
word with no spaces. Do not return a style token.
Do not generate an image.
"""

BEANIE_INSTRUCTIONS = """
Listing type: BEANIE

This is a Vinted ad for a beanie / knit cap. Describe this exact beanie,
not a hat, mask, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS beanie:
1. specific beanie type plus one or more visible construction details
   (cat-ear, pom-pom, cuffed, slouchy, knit, fuzzy, plush, striped, ribbed);
2. comma, then the colour(s) on their own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, western, cowboy, y2k, gothic, streetwear, grunge,
   Wild West, cowgirl, sophisticated;
4. one size fits all in English, or taille unique in French.
Never mention material/fabric such as wool, acrylic, polyester, or fleece.
Never write "vintage-style" or "y2k style". Write "style" first, then the
style tokens. Never put size S, size 36, or measurements in the title.

Copy this English pattern exactly:
"Fuzzy plush striped cat-ear beanie, brown and cream, style y2k, one size fits all"
"Cuffed pom-pom knit beanie, black, style streetwear grunge, one size fits all"

Copy this French pattern exactly:
"Bonnet oreilles de chat en peluche rayé, marron et crème, style y2k, taille unique"
"Bonnet à revers avec pompon en tricot, noir, style streetwear grunge, taille unique"

Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler. Exactly one line.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified length and/or width from facts if present. Never invent XX
  placeholders. Never add bust, waist, or other body measurements.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 item-specific tags about this beanie, colour, and styles.
  Include tags such as beanie, catear, y2k, kawaii when they match the
  item. Never use bag, clutch, handbag, coat, size36, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, invent one newly coined professional
word with no spaces. Do not return a style token.
Do not generate an image.
"""

LAMP_INSTRUCTIONS = """
Listing type: LAMP

This is a Vinted ad for a table lamp, floor lamp, or light fixture.
Describe this exact lamp, not a shelf, plant, coat, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS lamp:
1. specific lamp type plus one visible construction detail, then "with"
   plus another visible detail (twisted, ribbed, gourd-shaped, 3D-printed,
   base, shade). Never mention a belt;
2. comma, then the colour(s) on their own;
3. comma, then the literal word "style" followed by one or more of:
   decorative, modern, minimalist, vintage, industrial, scandinavian,
   artdeco, boho;
4. one size fits all in English, or taille unique in French.
Never mention material/fabric such as glass, wood, resin, or metal.
Never write "vintage-style" or "vintage style". Write "style" first, then
the style tokens. Never put size S, size 36, or measurements in the title.

Copy this English pattern exactly:
"Table lamp with a twisted 3D-printed design, white and natural wood, style modern, one size fits all"
"Gourd-shaped ribbed glass table lamp, amber, style vintage artdeco, one size fits all"

Copy this French pattern exactly:
"Lampe de table avec design torsadé imprimé en 3D, blanc et bois naturel, style modern, taille unique"
"Lampe de table forme calebasse en verre côtelé, ambre, style vintage artdeco, taille unique"

Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler. Exactly one line.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified length and/or width from facts if present. Never invent XX
  placeholders. Never add bust, waist, or other body measurements.
- Blank line.
- English: the exact line: price negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 item-specific tags about this lamp, colour, shape, and styles.
  Include tags such as tablelamp, modernlamp, vintagestyle when they match
  the item. Never use bag, clutch, handbag, coat, size36, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, invent one newly coined professional
word with no spaces. Do not return a style token.
Do not generate an image.
"""

CHANDELIER_INSTRUCTIONS = """
Listing type: CHANDELIER

This is a Vinted ad for a chandelier, pendant light, or ceiling light
fixture. Describe this exact fixture, not a lamp, shelf, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS fixture:
1. specific fixture type plus one or more visible construction details
   (multi-tiered, tiered, crystal, beaded, cage, drum, candle-style). You
   may mention crystal or metal here since they are the defining visible
   look of this fixture;
2. comma, then the colour(s) on their own;
3. comma, then the literal word "style" followed by one or more of:
   decorative, modern, minimalist, vintage, industrial, scandinavian,
   artdeco, boho;
4. one size fits all in English, or taille unique in French.
Never write "decorative-style" or "decorative style". Write "style" first,
then the style tokens. Never put size S, size 36, or measurements in the
title.

Copy this English pattern exactly:
"Multi-tiered gold metal and crystal chandelier, gold, style decorative, one size fits all"
"Crystal beaded pendant light with a cage frame, silver, style vintage industrial, one size fits all"

Copy this French pattern exactly:
"Lustre à plusieurs niveaux en métal doré et cristal, doré, style decorative, taille unique"
"Suspension à perles de cristal avec structure cage, argenté, style vintage industrial, taille unique"

Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler. Exactly one line.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified length and/or width from facts if present. Never invent XX
  placeholders. Never add bust, waist, or other body measurements.
- Blank line.
- English: the exact line: price negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.

Hashtags:
- 20 to 25 item-specific tags about this fixture, colour, and styles.
  Include tags such as chandelier, ceilinglight, crystaldecor when they
  match the item. Never use bag, clutch, handbag, coat, size36, or
  condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, invent one newly coined professional
word with no spaces. Do not return a style token.
Do not generate an image.
"""

CARPET_INSTRUCTIONS = """
Listing type: CARPET

This is a Vinted ad for a carpet, area rug, or runner. Describe this
exact carpet, not a lamp, shelf, plant, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS carpet:
1. specific carpet/rug type plus one visible construction detail
   (shaggy pile, geometric pattern, woven texture, hand-woven, round,
   runner-style);
2. comma, then the colour(s) on their own;
3. comma, then the literal word "style" followed by one or more of:
   decorative, modern, minimalist, vintage, industrial, scandinavian,
   artdeco, boho;
4. one size fits all in English, or taille unique in French.
Never mention material/fabric such as wool, cotton, jute, or polyester.
Never write "vintage-style" or "vintage style". Write "style" first, then
the style tokens. Never put size S, size 36, or measurements in the title.

Copy this English pattern exactly:
"Area rug with a geometric pattern, light grey, style decorative, one size fits all"
"Hand-woven shaggy rug, cream and beige, style boho minimalist, one size fits all"

Copy this French pattern exactly:
"Tapis à motif géométrique, gris clair, style decorative, taille unique"
"Tapis à poils longs tissé à la main, crème et beige, style boho minimalist, taille unique"

Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler. Exactly one line.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified length and/or width from facts if present. Never invent XX
  placeholders. Never add bust, waist, or other body measurements.
- Blank line.
- English: the exact line: price negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 item-specific tags about this carpet, colour, pattern, and
  styles. Never use bag, clutch, handbag, coat, size36, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, invent one newly coined professional
word with no spaces. Do not return a style token.
Do not generate an image.
"""

CUSHION_INSTRUCTIONS = """
Listing type: CUSHION

This is a Vinted ad for a decorative cushion, throw pillow, or cushion
cover. Describe this exact cushion, not a carpet, lamp, plant, or previous
item.

Write each title in this exact comma structure, naming only details visible
on THIS cushion:
1. specific cushion type plus one visible construction detail (textured
   weave, embroidered, tufted, piped edges, tassel trim, ruffled, quilted,
   woven texture);
2. comma, then the colour(s) on their own;
3. comma, then the literal word "style" followed by one or more of:
   decorative, modern, minimalist, vintage, industrial, scandinavian,
   artdeco, boho;
4. one size fits all in English, or taille unique in French.
Never mention material/fabric such as linen, cotton, velvet, or polyester.
Never write "vintage-style" or "vintage style". Write "style" first, then
the style tokens. Never put size S, size 36, or measurements in the title.

Copy this English pattern exactly:
"Decorative cushion with a textured weave, ivory, style decorative, one size fits all"
"Embroidered cushion with tassel trim, terracotta and cream, style boho, one size fits all"

Copy this French pattern exactly:
"Coussin décoratif à texture travaillée, ivoire, style decorative, taille unique"
"Coussin brodé avec pompons, terracotta et crème, style boho, taille unique"

Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler. Exactly one line.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified length and/or width from facts if present. Never invent XX
  placeholders. Never add bust, waist, or other body measurements.
- Blank line.
- English: the exact line: price negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 item-specific tags about this cushion, colour, texture, and
  styles. Never use bag, clutch, handbag, coat, size36, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, invent one newly coined professional
word with no spaces. Do not return a style token.
Do not generate an image.
"""

SKIRT_INSTRUCTIONS = """
Listing type: SKIRT

Follow this Vinted ad format strictly. The attached product image is the
authority: describe this exact skirt, not a coat, dress, jeans, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS skirt:
1. specific skirt type plus one visible construction detail, then "with"
   plus another visible detail (split, buckle, lace, tiers, panels, wrap,
   pleats). Never mention a belt;
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size M, an S-size belt, or a numeric EU size).
Never mention material/fabric. Never mention a belt. Never write "in an"
/ "in a" style phrasing, or any belt.

Copy this English pattern exactly:
"Long cream white maxi skirt with wrap details, cream white, style elegant size S"
"Long split black skirt with buckle details, black, style gothic size S"
"Midi pleated navy skirt with a high waist, navy, style old money size S"

Copy this French pattern exactly:
"Jupe maxi blanc crème avec détails portefeuille, blanc crème, style élégant taille S"
"Jupe longue fendue avec boucles, noire, style gothic taille S"
"Jupe midi plissée à taille haute, bleu marine, style old money taille S"

Write "style elegant size S", never "in an elegant style with an S-size belt"
and never "with an S-size belt".
The ending is ALWAYS "size S" / "taille S". Never add a belt.
The French title must put the category first: "Jupe midi" or "Jupe longue",
never "Midi jupe".
Do not use vague filler such as defined waist, fluid line, tombé fluide,
floor-grazing hem, or flattering shape.
Titles must stay between 65 and 100 characters by naming extra visible
details from THIS skirt's image, never by adding filler or another product.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S".
- Do not mention a belt, an S-size belt, or "in an" / "in a".
- Do not mention material/fabric.
- Do not use size 36 or any numeric EU size.

The listing title field is one copy of the title. The description may repeat
that title once as its first line, never twice. Total: 2 times.
The description must match THIS skirt only: same colour, length, and visible
details as the title. No random words and no leftover paragraph.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Size S measurements from verified facts, if present.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: État parfait
- Do not add a second title line, measurements invented as XX, or a
  descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 tags unique to THIS generate and THIS skirt: colour, length,
  cut, and styles visible on the selected product.
- Never reuse another product's tags or generic filler such as fashion,
  wardrobe, daylook, closetstyle.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle, belt, ceinture.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark. Never reuse a style or brand name that was
already generated for another listing.
Do not generate an image.
"""

JEANS_INSTRUCTIONS = """
Listing type: JEANS

This is a men's Vinted ad. Describe men's jeans, never women's clothing.
The attached product image is the authority: describe this exact pair of
jeans, not a coat, trench, dress, skirt, or previous item.

These jeans are ALWAYS listed as baggy. The word "baggy" MUST sit directly
next to "jeans" / "jean" in the title every single time, regardless of what
else is visible. Never write "wide-leg", "wide leg", or "with wide legs" in
the title — even if the product is a wide-leg cut, describe it as baggy
instead. Never write "men's", "men", or "for men" in the title — this is a
men's item by category, not by title wording.

Write each title in this exact comma structure, naming only details visible
on THESE jeans:
1. the word "Baggy" immediately next to "jeans", then one more visible
   construction detail introduced with "with" (cargo pockets, straight,
   bootcut, ripped, relaxed, tapered, oversized, flare). Never wide-leg,
   never a belt;
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size L (never size S, size 40, an S-size belt, an L-size belt, or a
   numeric EU size).
Never mention material/fabric such as denim, cotton, or polyester.
Never mention a belt. Never write trench coat, dress, skirt, "in an" / "in a" style phrasing,
or any belt.

Copy this English pattern exactly:
"Baggy jeans with cargo pockets, black, style streetwear size L"
"Baggy jeans with a relaxed fit, cream, style grunge size L"
"Baggy cargo jeans with ripped details, blue, style gothic streetwear size L"

Copy this French pattern exactly:
"Jean baggy avec poches cargo, noir, style streetwear taille L"
"Jean baggy avec coupe relaxed, crème, style grunge taille L"
"Jean baggy cargo avec détails déchirés, bleu, style gothic streetwear taille L"

Write "style streetwear size L", never "in a streetwear style with an
L-size belt" and never "with an S-size belt".
The ending is ALWAYS "size L" / "taille L". Never add a belt.
The French title must put the category first: "Jean baggy", never
"Baggy jean".
Do not use vague filler such as flattering fit, everyday staple, or
premium denim.
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size L" / "taille L".
- Always include "baggy" directly next to "jeans" / "jean".
- Never write "wide-leg", "wide leg", "men's", "men", or "for men".
- Do not mention a belt, an S-size belt, an L-size belt, or "in an" / "in a".
- Do not mention material/fabric.
- Do not use size S, size 40, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Size L measurements from verified facts, if present.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add measurements invented as XX, a second title line, or a
  descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 tags unique to THESE jeans: colour, fit, pockets, and style
  visible on this pair. Include tags such as baggyjeans, mensjeans,
  sizeL when they match the item.
- Never reuse another product's hashtag list. Never use coat, trench,
  dress, skirt, belt, or women's tags.
- Use men's clothing tags only. Never use womens, womensjeans, ladies,
  or femme.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

LONG_BOOTS_INSTRUCTIONS = """
Listing type: LONG BOOTS

This is a women's Vinted ad for long boots / knee-high boots. Describe this
exact pair of boots, not a coat, cape, dress, jeans, or previous item.

Write each title in this exact comma structure, naming only details that
are visible on THESE boots:
1. specific boot type plus visible details (knee-high, thigh-high, high
   heel, pointed toe, sequins, zipper, buckle, platform, slouch);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, grunge, sophisticated;
4. size 38 (never size M, never size 36, never size S).
Never mention material/fabric such as leather, suede, or polyester.

Copy this English pattern exactly:
"Knee-high heeled sequined boots, cream white, style old money elegant size 38"
"Thigh-high pointed boots with zipper, black, style gothic sophisticated size 38"
"Knee-high platform boots with buckle details, brown, style boho grunge size 38"

Copy this French pattern exactly:
"Bottes hautes à talons sequin, blanc crème, style old money elegant taille 38"
"Cuissardes pointues avec zip, noires, style gothic sophisticated taille 38"
"Bottes hautes plateforme avec boucles, marron, style boho grunge taille 38"

Do not write "elegant style" or "in an old money style". Write "style"
first, then the style tokens: "style old money elegant".
Titles must stay between 80 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Do not mention material/fabric.
- Use size 38 / taille 38 only. Do not use size M.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add measurements, XX placeholders, a second title line, or a
  descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 item-specific tags about these long boots, colour, heel, and
  styles. Include tags such as kneehighboots, longboots, size38, heeledboots
  when they match the item.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return one newly coined professional
brand word with no spaces. Do not return a style token.
Do not generate an image.
"""

HEELS_INSTRUCTIONS = """
Listing type: HEELS

This is a women's Vinted ad for heeled sandals / heels. Describe this exact
pair of heels, not a coat, cape, dress, boots, or previous item.

Write each title in this exact comma structure, naming only details that
are visible on THESE heels:
1. specific heel type plus visible details (heeled sandals, pumps, stilettos,
   sequins, pointed toe, strap, platform, slingback, peep toe);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, grunge, sophisticated;
4. size 38 (never size M, never size 36, never size S).
Never mention material/fabric such as leather, suede, or polyester.

Copy this English pattern exactly:
"Heeled sandals with sequins, cream white, style old money size 38"
"Pointed heeled sandals with ankle strap, black, style gothic elegant size 38"
"Platform heeled sandals with bow details, gold, style y2k sophisticated size 38"

Copy this French pattern exactly:
"Sandales à talons avec sequins, blanc crème, style old money taille 38"
"Sandales à talons bout pointu avec bride, noires, style gothic elegant taille 38"
"Sandales plateforme avec nœud, doré, style y2k sophisticated taille 38"

Do not write "elegant style" or "in an old money style". Write "style"
first, then the style tokens: "style old money".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Do not mention material/fabric.
- Use size 38 / taille 38 only. Do not use size M.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add measurements, XX placeholders, a second title line, or a
  descriptive paragraph.
- Do not add material/fabric.

Hashtags:
- 20 to 25 item-specific tags about these heels, colour, heel, and
  styles. Include tags such as heeledsandals, heels, size38, womensheels
  when they match the item.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return one newly coined professional
brand word with no spaces. Do not return a style token.
Do not generate an image.
"""

COAT_INSTRUCTIONS = """
Listing type: COAT

This is a women's Vinted ad for a coat / trench / cape coat. Describe this
exact coat, not a dress, skirt, jeans, boots, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS coat:
1. specific coat type plus one visible construction detail, then "with"
   plus another visible detail (trench, wrap, oversized, collar, cape,
   double-breasted, long). Never mention a belt;
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size 38, size M, or an S-size belt).
Never mention material/fabric such as wool, cotton, polyester, leather, or fur.
Never mention a belt. Never write "in an elegant style" or "with an S-size belt".

Copy this English pattern exactly:
"Long cream white trench coat with wrap details, cream white, style elegant size S"
"Oversized black wrap coat with collar details, black, style gothic size S"
"Long beige cape coat with oversized cut, beige, style bohemian size S"

Copy this French pattern exactly:
"Trench long blanc crème avec col, blanc crème, style elegant taille S"
"Manteau oversize noir portefeuille, noir, style gothic taille S"
"Manteau cape long beige, beige, style bohemian taille S"

Write "style elegant size S", never "in an elegant style with an S-size belt"
and never "elegant style" without the word "style" first.
The ending is ALWAYS "size S" / "taille S". Never add a belt.
The French title must put the category first: "Manteau long" or "Trench long",
never "Long trench".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S".
- Do not mention a belt, an S-size belt, or "in an" / "in a" style phrasing.
- Do not mention material/fabric.
- Do not use size 36, size 38, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified measurements from facts if present (length, sleeve length,
  waist). Never invent XX placeholders.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Never omit extracted measurements.

Hashtags:
- 20 to 25 item-specific tags about this coat, colour, cut, and styles.
  Include tags such as coat, trenchcoat, womenscoat, cape, sizeS when they
  match the item. Never use size36, belt, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

JACKET_INSTRUCTIONS = """
Listing type: JACKET

This is a women's Vinted ad for a jacket / biker / bomber / moto jacket.
Describe this exact jacket, not a coat, cape, dress, skirt, jeans, or
previous item.

Write each title in this exact comma structure, naming only details visible
on THIS jacket:
1. specific jacket type plus one visible construction detail, then "with"
   plus another visible detail (biker, bomber, cropped, oversized, zipper,
   collar, moto, patterned, quilted). Never mention a belt;
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size 38, size M, or an S-size belt).
Never mention material/fabric such as wool, cotton, polyester, leather,
faux leather, or fur.
Never mention a belt. Never write "in an elegant style" or "Y2K style"
without the word "style" first. Never write "with an S-size belt".

Copy this English pattern exactly:
"Patterned biker jacket with zipper details, brown, style y2k size S"
"Oversized bomber jacket with collar details, black, style streetwear size S"
"Cropped moto jacket with zipper details, beige, style elegant size S"

Copy this French pattern exactly:
"Veste biker à motif avec zip et col, marron, style y2k taille S"
"Blouson bomber oversize avec col, noir, style streetwear taille S"
"Veste cropped moto avec zip, beige, style elegant taille S"

Write "style y2k size S", never "Y2K style" and never "in a vintage Y2K style".
The ending is ALWAYS "size S" / "taille S". Never add a belt.
The French title must put the category first: "Veste biker" or "Blouson bomber",
never "Biker veste".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S".
- Do not mention a belt, an S-size belt, or "in an" / "in a" style phrasing.
- Do not mention material/fabric.
- Do not use size 36, size 38, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not add measurements, length, sleeve length, waist, or XX placeholders.

Hashtags:
- 20 to 25 item-specific tags about this jacket, colour, cut, and styles.
  Include tags such as jacket, womensjacket, bikerjacket, sizeS when they
  match the item. Never use size36, belt, cape, poncho, coat, trench, or
  condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

ORGANIZER_INSTRUCTIONS = """
Listing type: ORGANIZER

This is a Vinted ad for a home or kitchen organizer, storage rack, or
holder item (not clothing). Describe this exact organizer, not a shelf,
bag, plant, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS organizer:
1. specific organizer type plus one visible construction detail (tiers,
   compartments, mounting, mesh baskets, rotating, stackable, wall-mounted,
   countertop);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size 38, size M, or measurements).
Never mention material/fabric such as metal, plastic, wood, or wire gauge.
Never write "in an elegant style" or "Y2K style" without the word "style"
first.

Copy this English pattern exactly:
"3-tier metal countertop organizer with mesh baskets, black, style elegant size S"
"Rotating spice rack organizer with 4 tiers, white, style old money size S"
"Stackable drawer organizer with mesh sides, cream, style sophisticated size S"

Copy this French pattern exactly:
"Organiseur de comptoir en métal à 3 niveaux avec paniers, noir, style elegant taille S"
"Organiseur d'épices rotatif à 4 niveaux, blanc, style old money taille S"
"Organiseur à tiroirs empilable avec grille, crème, style sophisticated taille S"

Write "style elegant size S", never "elegant style" and never "in a vintage
style". The ending is ALWAYS "size S" / "taille S".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S".
- Do not mention material/fabric.
- Do not use size 36, size 38, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not add measurements, length, width, or XX placeholders.

Hashtags:
- 20 to 25 item-specific tags about this organizer, colour, and use
  (kitchen, storage, countertop, wall-mount). Never use size36, belt,
  jacket, coat, dress, bag, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

MIRROR_INSTRUCTIONS = """
Listing type: MIRROR

This is a Vinted ad for a decorative wall or tabletop mirror (not
clothing). Describe this exact mirror, not an organizer, shelf, plant, or
previous item.

Write each title in this exact comma structure, naming only details visible
on THIS mirror:
1. specific mirror type plus one visible construction detail (asymmetrical,
   organic-shaped, wall-mounted, tabletop, hand-carved, sculptural, arched,
   round, oval);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size 38, size M, or measurements).
Never mention material/fabric such as wood, walnut, resin, or metal.
Never write "in an elegant style" or "Y2K style" without the word "style"
first.

Copy this English pattern exactly:
"Asymmetrical organic-shaped wall mirror, natural, style boho sophisticated size S"
"Hand-carved sculptural tabletop mirror, black, style dark academia size S"

Copy this French pattern exactly:
"Miroir mural asymétrique de forme organique, naturel, style boho sophisticated taille S"
"Miroir de table sculpté à la main, noir, style dark academia taille S"

Write "style boho sophisticated size S", never "boho style" and never "in a
vintage style". The ending is ALWAYS "size S" / "taille S".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S".
- Do not mention material/fabric.
- Do not use size 36, size 38, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: price negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not add measurements, length, width, or XX placeholders.

Hashtags:
- 20 to 25 item-specific tags about this mirror, colour, shape, and
  styles. Never use size36, belt, jacket, coat, dress, bag, or condition
  tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

SCULPTURE_INSTRUCTIONS = """
Listing type: SCULPTURE

This is a Vinted ad for a decorative statue or sculpture (not clothing).
Describe this exact sculpture, not a mirror, organizer, plant, or previous
item.

Write each title in this exact comma structure, naming only details visible
on THIS sculpture:
1. specific sculpture/statue type plus one visible construction detail
   (delicate, carved, abstract, modern, geometric, figurine-style);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size 38, size M, or measurements).
Never mention material/fabric such as resin, ceramic, wood, or metal.
Never write "in an elegant style" or "Y2K style" without the word "style"
first.

Copy this English pattern exactly:
"Delicate Justice sculpture with carved details, brown, style elegant sophisticated size S"
"Abstract figurine sculpture, black, style dark academia size S"

Copy this French pattern exactly:
"Sculpture Justice délicate aux détails sculptés, marron, style elegant sophisticated taille S"
"Sculpture figurine abstraite, noire, style dark academia taille S"

Write "style elegant sophisticated size S", never "elegant style" and
never "in a vintage style". The ending is ALWAYS "size S" / "taille S".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S". Never "one size" or
  "taille unique".
- Do not mention material/fabric.
- Do not use size 36, size 38, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: price negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not add measurements, length, width, or XX placeholders.

Hashtags:
- 20 to 25 item-specific tags about this sculpture, colour, shape, and
  styles. Never use size36, belt, jacket, coat, dress, bag, cape, or
  condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

CURTAIN_INSTRUCTIONS = """
Listing type: CURTAIN

This is a Vinted ad for a decorative curtain or window drape (not
clothing). Describe this exact curtain, not a carpet, cushion, mirror, or
previous item.

Write each title in this exact comma structure, naming only details visible
on THIS curtain:
1. specific curtain type plus one visible construction detail (lace, sheer,
   scalloped edge, floral, blackout, grommet, eyelet, pleated,
   embroidered, ruffled);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size 38, size M, or measurements).
Never mention material/fabric such as linen, cotton, polyester, or velvet.
Never write "in an elegant style" or "Y2K style" without the word "style"
first.

Copy this English pattern exactly:
"Floral lace scalloped-edge sheer curtain, black, style gothic size S"
"Embroidered blackout curtain panel, cream, style old money size S"

Copy this French pattern exactly:
"Rideau en dentelle florale à bordure festonnée, noir, style gothic taille S"
"Panneau de rideau occultant brodé, crème, style old money taille S"

Write "style gothic size S", never "gothic style" and never "in a vintage
style". The ending is ALWAYS "size S" / "taille S".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S". Never "one size" or
  "taille unique".
- Do not mention material/fabric.
- Do not use size 36, size 38, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: price are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not add measurements, length, width, or XX placeholders.

Hashtags:
- 20 to 25 item-specific tags about this curtain, colour, and styles. Never
  use size36, belt, jacket, coat, dress, bag, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

NECKTIE_INSTRUCTIONS = """
Listing type: NECKTIE

This is a Vinted ad for a necktie (not clothing worn as a full outfit).
Describe this exact necktie, not a curtain, cushion, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS necktie:
1. specific necktie type plus one visible pattern/construction detail
   (paisley, striped, polka-dot, printed, knitted, skinny, slim,
   embroidered);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, western, cowboy, y2k, gothic, streetwear, grunge,
   wild west, cowgirl, sophisticated.
Never mention a fixed size and never add "size S", "one size fits all", or
any numeric size. Never mention material/fabric such as silk, polyester,
cotton, or wool.
Never write "in an elegant style" or "Y2K style" without the word "style"
first.

Copy this English pattern exactly:
"Dark green paisley-patterned necktie, dark green, style elegant"
"Striped silk-look necktie with a slim cut, navy and gold, style sophisticated"

Copy this French pattern exactly:
"Cravate a motifs cachemire, vert fonce, style elegant"
"Cravate rayee a coupe fine, bleu marine et or, style sophisticated"

Write "style elegant", never "elegant style" and never "in a vintage
style". Never end with a size marker of any kind.
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Never add a size marker: no "size S", no "one size fits all", no
  numeric size.
- Do not mention material/fabric.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- Verified length/width measurements if present in the product facts,
  never invented "XX cm" placeholders.
- Blank line.
- English: the exact line: price are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not invent measurements; only include them if verified.

Hashtags:
- 20 to 25 item-specific tags about this necktie, colour, pattern, and
  styles. Never use size36, belt, jacket, coat, dress, bag, or condition
  tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, invent one newly coined professional
word with no spaces. Do not return a style token.
Do not generate an image.
"""

LEG_WARMER_INSTRUCTIONS = """
Listing type: LEG_WARMER

This is a Vinted ad for faux fur leg warmers (not a full outfit). Describe
this exact pair of leg warmers, not a coat, jacket, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS item:
1. specific leg warmer type plus one visible construction detail (fluffy,
   plush, shaggy, chunky, cropped, oversized);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size 38, size M, or measurements).
Never mention material/fabric such as faux fur, fur, polyester, or acrylic.
Never write "in an elegant style" or "Y2K style" without the word "style"
first.

Copy this English pattern exactly:
"Fluffy faux fur leg warmers, rich brown, style boho size S"
"Plush shaggy leg warmers with a cropped cut, cream, style y2k grunge size S"

Copy this French pattern exactly:
"Jambières en fausse fourrure moelleuse, marron riche, style boho taille S"
"Jambières pelucheuses courtes et texturées, crème, style y2k grunge taille S"

Write "style boho size S", never "boho style" and never "in a vintage
style". The ending is ALWAYS "size S" / "taille S".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S".
- Do not mention material/fabric.
- Do not use size 36, size 38, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not add measurements, length, width, or XX placeholders.

Hashtags:
- 20 to 25 item-specific tags about these leg warmers, colour, and styles.
  Never use size36, belt, jacket, coat, dress, bag, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

JEWELRY_BOX_INSTRUCTIONS = """
Listing type: JEWELRY_BOX

This is a Vinted ad for a jewelry box (not clothing). Describe this exact
jewelry box, not an organizer, shelf, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS jewelry box:
1. specific jewelry box type plus one visible construction detail
   (textured, tiered, multi-level, mirrored, lockable, drawer);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size L (never size 40, size 42, size M, or measurements).
Never mention material/fabric such as leather, velvet, or faux leather.
Never write "in an elegant style" or "Y2K style" without the word "style"
first.

Copy this English pattern exactly:
"Large jewelry box with multiple storage tiers, pink textured, style chic size L"
"Mirrored jewelry box with a lockable drawer, black, style elegant size L"

Copy this French pattern exactly:
"Grand coffret à bijoux à plusieurs niveaux, rose texturé, style chic taille L"
"Coffret à bijoux avec miroir et tiroir verrouillable, noir, style elegant taille L"

Write "style chic size L", never "chic style" and never "in a vintage
style". The ending is ALWAYS "size L" / "taille L".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size L" / "taille L".
- Do not mention material/fabric.
- Do not use size 40, size 42, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: price are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not add measurements, length, width, or XX placeholders.

Hashtags:
- 20 to 25 item-specific tags about this jewelry box, colour, and styles.
  Keep them gender-neutral, never women-targeted tags. Never use size36,
  size40, belt, jacket, coat, dress, bag, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

LACE_UMBRELLA_INSTRUCTIONS = """
Listing type: LACE_UMBRELLA

This is a Vinted ad for a decorative lace parasol / lace umbrella (not
clothing). Describe this exact parasol, not a curtain, cushion, or
previous item.

Write each title in this exact comma structure, naming only details visible
on THIS parasol:
1. specific parasol type plus one visible construction detail (lace,
   embroidered, ruffled, fringed, floral, folding);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size 38, size M, or measurements).
Never mention material/fabric such as lace fabric, satin, polyester, or
cotton as a material claim. Never write "in an elegant style" or "Y2K
style" without the word "style" first.

Copy this English pattern exactly:
"Elegant bohemian-style lace parasol, white, style elegant boho size S"
"Embroidered floral lace parasol with a ruffled trim, cream, style old money size S"

Copy this French pattern exactly:
"Ombrelle en dentelle élégante de style bohème, blanc, style elegant boho taille S"
"Ombrelle en dentelle fleurie brodée à volants, crème, style old money taille S"

Write "style elegant boho size S", never "elegant style" and never "in a
vintage style". The ending is ALWAYS "size S" / "taille S".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S".
- Do not mention material/fabric.
- Do not use size 36, size 38, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: prices are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not add measurements, length, width, or XX placeholders.

Hashtags:
- 20 to 25 item-specific tags about this parasol, colour, and styles.
  Never use size36, belt, jacket, coat, dress, bag, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

BELT_INSTRUCTIONS = """
Listing type: BELT

This is a Vinted ad for a belt (not a full outfit). Describe this exact
belt, not a bag, jacket, or previous item.

Write each title in this exact comma structure, naming only details visible
on THIS belt:
1. specific belt type plus one visible construction detail (carved,
   buckle, studded, braided, woven, chain);
2. comma, then the colour on its own;
3. comma, then the literal word "style" followed by one or more of: boho,
   bohemian, elegant, old money, y2k, gothic, streetwear, grunge,
   dark academia, sophisticated;
4. size S (never size 36, size 38, size M, or measurements).
Never mention material/fabric such as leather, faux leather, suede, or
metal as a material claim. Never write "in an elegant style" or "Y2K
style" without the word "style" first.

Copy this English pattern exactly:
"Vintage western-style carved buckle belt, brown, style boho bohemian size S"
"Studded chain belt with a metal buckle, black, style street grunge size S"

Copy this French pattern exactly:
"Ceinture western vintage à boucle sculptée, marron, style boho bohemian taille S"
"Ceinture à chaîne cloutée avec boucle métallique, noir, style street grunge taille S"

Write "style boho bohemian size S", never "boho style" and never "in a
vintage style". The ending is ALWAYS "size S" / "taille S".
Titles must stay between 65 and 100 characters by naming extra visible
details from the image, never by adding filler.

Title rules:
- Exactly one line, never more than 100 characters.
- Keep the literal word "style" immediately before the style tokens.
- Always finish with "size S" / "taille S".
- Do not mention material/fabric.
- Do not use size 36, size 38, size M, or a numeric EU size.

The listing title field is one copy of the title. The description may
repeat that title once as its first line, never twice. Total: 2 times.

For each description, skip a blank line between every step and use this
exact block (no extra sentences):
- Line 1: that language's title exactly, once.
- Blank line.
- English: the exact line: price are negotiable :)
- French: the exact line: prix négociable :)
- Blank line.
- English final line: Perfect condition.
- French final line: Parfait état.
- Do not add a second title line or a descriptive paragraph.
- Do not add material/fabric.
- Do not add measurements, length, width, or XX placeholders.

Hashtags:
- 20 to 25 item-specific tags about this belt, colour, and styles. Never
  use size36, jacket, coat, dress, bag, or condition tags.
- Forbidden words and synonyms: vinted, france, new, unused, neuf, neuve,
  nouveau, nouvelle.

For the fictional_brand schema field, return exactly one clothing style
token instead of a brand: boho, bohemian, chic, elegant, oldmoney, y2k,
goth, street, grunge, dark.
Do not generate an image.
"""

FAST_BASE_INSTRUCTIONS = """
Create a detailed bilingual Vinted listing draft from verified product facts.
Return only structured JSON containing fictional_brand, english_title,
french_title, english_description, french_description, english_hashtags,
and french_hashtags.

Rules:
- Titles are one natural-sounding line, target 80 to 100 characters, never
  exceed 100 characters, contain no material/fabric, and include the literal
  word "style" in both English and French.
- Use only the strongest verified category, colour, visible detail, and style.
  Never stack keywords, repeat a feature, or invent filler to reach a length.
- Do not use vague filler such as refined cut details, chic feminine finish,
  flattering fitted look, coupe raffinée, finition chic, or similar padding.
- Follow the selected listing type's title pattern. Never copy a coat, jacket,
  dress, skirt, bag, or previous-product title onto a different item.
- Skirt and dress titles MUST be 80 to 100 characters. Reach that length with
  visible cut details from the product image, never with generic filler.
- Return 20 to 22 highly item-specific hashtags per language. Do not reuse
  another product's hashtag list.
- Hashtags must not use Vinted, France, new, unused, neuf, neuve, nouveau,
  nouvelle, or synonyms.
- Use only verified facts and visible details. Do not invent features.
- The supplied product image is authoritative for the garment colour and
  visible design. Extracted page text can describe a different colour variant;
  never use a text colour that conflicts with the visible product image.
- fictional_brand is one newly coined professional word with no spaces.
- Follow the selected listing type's description rule. Return empty
  description strings when that type forbids a descriptive paragraph.
- Do not generate an image.
"""

FAST_TYPE_INSTRUCTIONS = {
    "small_plant": """
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
    "dress": """
DRESS titles MUST copy this comma structure from the product image:
specific dress type with a visible construction detail, comma, colour on
its own, comma, the word "style" then style tokens, then size S.
English models to copy:
"Long off-shoulder dress with ruffle details, black, style gothic elegant size S"
"Lace maxi dress with tiered details, cream white, style bohemian elegant size S"
"Asymmetric maxi dress with color block panels, black brown and beige, style sophisticated size S"
French models to copy:
"Robe longue à épaules dénudées avec volants, noire, style gothic elegant taille S"
"Robe maxi en dentelle à étages, blanc crème, style bohemian elegant taille S"
"Robe maxi asymétrique à panneaux color-block, noir marron et beige, style sophisticated taille S"
Write "style gothic elegant", never "elegant gothic style" or
"in an elegant style". The French title must be "Robe longue", never
"Longue robe". Never mention material/fabric. Never put measurements in
the title. Name only details visible on this dress. Return empty strings
for english_description and french_description.
The formatter will build this English block with a blank line between each
step: title once, verified Size S measurements, "prices are negotiable :)",
"Perfect condition." Do not repeat the title a second time in the
description. The listing title field is the other copy, for a total of 2.
French block: title once, verified Size S measurements, "prix négociable :)",
"Parfait état."
Both titles MUST be between 80 and 100 characters by adding another visible
cut detail from the image, never generic filler.
""",
    "dress_m": """
DRESS M titles MUST copy this comma structure from the product image:
specific dress type with a visible construction detail, comma, colour on
its own, comma, the word "style" then style tokens, then size M.
English models to copy:
"Long off-shoulder dress with ruffle details, black, style gothic elegant size M"
"Lace maxi dress with tiered details, cream white, style bohemian elegant size M"
"Asymmetric maxi dress with color block panels, black brown and beige, style sophisticated size M"
French models to copy:
"Robe longue à épaules dénudées avec volants, noire, style gothic elegant taille M"
"Robe maxi en dentelle à étages, blanc crème, style bohemian elegant taille M"
"Robe maxi asymétrique à panneaux color-block, noir marron et beige, style sophisticated taille M"
Write "style gothic elegant", never "elegant gothic style" or
"in an elegant style". The French title must be "Robe longue", never
"Longue robe". Never mention material/fabric. Never put measurements in
the title. Name only details visible on this dress. Return empty strings
for english_description and french_description.
The formatter will build this English block with a blank line between each
step: title once, verified Size M measurements, "prices are negotiable :)",
"Perfect condition." Do not repeat the title a second time in the
description. The listing title field is the other copy, for a total of 2.
French block: title once, verified Size M measurements, "prix négociable :)",
"Parfait état."
Both titles MUST be between 80 and 100 characters by adding another visible
cut detail from the image, never generic filler.
""",
    "skirt": """
SKIRT titles MUST copy this comma structure from the product image:
specific skirt plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"Long cream white maxi skirt with wrap details, cream white, style elegant size S"
"Long split black skirt with buckle details, black, style gothic size S"
French models to copy:
"Jupe maxi blanc crème avec détails portefeuille, blanc crème, style élégant taille S"
"Jupe longue fendue avec boucles, noire, style gothic taille S"
Write "style elegant size S", never "in an elegant style" and never
"with an S-size belt". The ending "size S" / "taille S" is mandatory.
Never mention a belt. Never mention material/fabric. Never put size 36
in the title. Name only details visible on THIS skirt, never a previous
product and never random filler.
Return empty strings for english_description and french_description;
the listing must not contain a descriptive paragraph.
The formatter will build this exact English block with a blank line between
each step: title once, verified Size S measurements, "prices are
negotiable :)", "Perfect condition."
French block: title once, verified Size S measurements,
"prix négociable :)", "État parfait".
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Never reuse a style or brand name that was already generated.
Both titles MUST be between 65 and 100 characters by adding another visible
cut detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 hashtags unique to this generate and this skirt, such as
pleatedskirt and sizeS. Never reuse another product's tags. Never use belt tags.
Do not generate an image.
""",
    "jeans": """
JEANS titles MUST copy this comma structure from the product image:
the word "Baggy" directly next to "jeans" plus one more visible detail,
comma, colour on its own, comma, the word "style" then style tokens, then
ALWAYS size L.
English models to copy:
"Baggy jeans with cargo pockets, black, style streetwear size L"
"Baggy jeans with a relaxed fit, cream, style grunge size L"
French models to copy:
"Jean baggy avec poches cargo, noir, style streetwear taille L"
"Jean baggy avec coupe relaxed, crème, style grunge taille L"
Write "style streetwear size L", never "in a streetwear style" and never
"with an L-size belt" or "with an S-size belt". The ending "size L" /
"taille L" is mandatory. Never mention a belt.
These jeans are ALWAYS baggy: "baggy" must sit directly next to "jeans" /
"jean" every time, even if the item looks wide-leg. Never write "wide-leg",
"wide leg", "men's", "men", or "for men" anywhere in the title — this is a
men's item by category, not by title wording.
This is men's clothing. Never mention material/fabric. Never write trench
coat, dress, skirt, size S, or size 40. Name only details visible on
THESE jeans, never a previous product.
Return empty strings for english_description and french_description;
the listing must not contain a descriptive paragraph.
The formatter will build this exact English block with a blank line between
each step: title once, verified Size L measurements, "prices are negotiable :)",
"Perfect condition."
French block: title once, verified Size L measurements, "prix négociable :)",
"Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
cut detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 men's jeans hashtags unique to this pair, such as
baggyjeans and sizeL. Never reuse another product's tags. Never use belt tags.
Do not generate an image.
""",
    "long_boots": """
LONG BOOTS titles MUST copy this comma structure from the product image:
specific boot type with a visible construction detail, comma, colour on
its own, comma, the word "style" then style tokens, then size 38.
English models to copy:
"Knee-high heeled sequined boots, cream white, style old money elegant size 38"
"Thigh-high pointed boots with zipper, black, style gothic sophisticated size 38"
French models to copy:
"Bottes hautes à talons sequin, blanc crème, style old money elegant taille 38"
"Cuissardes pointues avec zip, noires, style gothic sophisticated taille 38"
Write "style old money elegant", never "old money style" or "in an elegant
style". Never mention material/fabric. Never put measurements, size M, or
size 36 in the title. Name only details visible on these boots. Return
empty strings for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "prices are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
fictional_brand must be one newly coined professional word, not a style
token.
Both titles MUST be between 80 and 100 characters by adding another visible
cut detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 boot hashtags such as kneehighboots and size38.
Do not generate an image.
""",
    "heels": """
HEELS titles MUST copy this comma structure from the product image:
specific heel type with a visible construction detail, comma, colour on
its own, comma, the word "style" then style tokens, then size 38.
English models to copy:
"Heeled sandals with sequins, cream white, style old money size 38"
"Pointed heeled sandals with ankle strap, black, style gothic elegant size 38"
French models to copy:
"Sandales à talons avec sequins, blanc crème, style old money taille 38"
"Sandales à talons bout pointu avec bride, noires, style gothic elegant taille 38"
Write "style old money", never "old money style" or "in an elegant
style". Never mention material/fabric. Never put measurements, size M, or
size 36 in the title. Name only details visible on these heels. Return
empty strings for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "prices are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
fictional_brand must be one newly coined professional word, not a style
token.
Both titles MUST be between 65 and 100 characters by adding another visible
cut detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 heel hashtags such as heeledsandals and size38.
Do not generate an image.
""",
    "coat": """
COAT titles MUST copy this comma structure from the product image:
specific coat plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"Long cream white trench coat with wrap details, cream white, style elegant size S"
"Oversized black wrap coat with collar details, black, style gothic size S"
French models to copy:
"Trench long blanc crème avec col, blanc crème, style elegant taille S"
"Manteau oversize noir portefeuille, noir, style gothic taille S"
Write "style elegant size S", never "in an elegant style" and never
"with an S-size belt". The ending "size S" / "taille S" is mandatory.
Never mention a belt. Never mention material/fabric. Never put size 36,
size 38, or size M in the title. Name only details visible on this coat.
Return empty strings for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified measurements, "prices are negotiable :)",
"Perfect condition."
French block: title once, verified measurements, "prix négociable :)",
"Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
cut detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 coat hashtags unique to this item, such as trenchcoat,
cape, and sizeS. Never use size36 or belt tags.
Do not generate an image.
""",
    "jacket": """
JACKET titles MUST copy this comma structure from the product image:
specific jacket plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"Patterned biker jacket with zipper details, brown, style y2k size S"
"Oversized bomber jacket with collar details, black, style streetwear size S"
French models to copy:
"Veste biker à motif avec zip et col, marron, style y2k taille S"
"Blouson bomber oversize avec col, noir, style streetwear taille S"
Write "style y2k size S", never "Y2K style" and never
"with an S-size belt". The ending "size S" / "taille S" is mandatory.
Never mention a belt. Never mention material/fabric. Never put size 36,
size 38, size M, or measurements in the title. Name only details visible
on this jacket. Return empty strings for english_description and
french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "prices are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
cut detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 jacket hashtags unique to this item, such as bikerjacket
and sizeS. Never use size36, belt, cape, poncho, or coat tags.
Do not generate an image.
""",
    "earrings": """
EARRINGS titles, in order: specific earrings category/details; colour; the
word style plus one or more of boho, bohemian, elegant, western, cowboy, y2k,
gothic, streetwear, grunge, Wild West, cowgirl, sophisticated; one size fits
all in English or taille unique in French.
""",
    "hat": """
HAT titles, in order: specific hat category/details; colour; the word style
plus one or more of boho, bohemian, elegant, western, cowboy, y2k, gothic,
streetwear, grunge, Wild West, cowgirl, sophisticated; one size fits all in
English or taille unique in French. Return empty strings for english_description
and french_description; the final formatted hat listing must include the exact
English line "prices are negotiable :)" before the English condition line and
the exact French line "prix négociable :)" before the French condition line.
In the fictional_brand field, return exactly one clothing style token from:
boho, elegant, western, cowboy, cowgirl, y2k, streetwear, grunge, wildwest,
sophisticated.
""",
    "mask": """
MASK titles MUST copy this comma structure from the product image:
specific mask plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS one size fits all /
taille unique.
English models to copy:
"Sequin masquerade mask with cutout details, black, style gothic y2k, one size fits all"
"Lace party mask with feather details, black, style elegant gothic, one size fits all"
French models to copy:
"Masque de soirée à paillettes avec découpes, noir, style gothic y2k, taille unique"
"Masque festif en dentelle avec plumes, noir, style elegant gothic, taille unique"
Write "style gothic y2k", never "Halloween costume style" or "y2k style".
Never mention material/fabric. Never put size S, size 36, or measurements
in the title. Name only details visible on this mask. Return empty strings
for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified length/width if present, "prices are negotiable :)",
"Perfect condition."
French block: title once, verified length/width if present, "prix négociable :)",
"Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, elegant, western, cowboy, cowgirl, y2k, streetwear, grunge, wildwest,
sophisticated.
Both titles MUST be between 65 and 100 characters by adding another visible
cut detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 mask hashtags unique to this item, such as sequinmask and
oneSize. Never use bag, clutch, coat, or size36 tags.
Do not generate an image.
""",
    "bag": """
BAG titles MUST copy this comma structure from the product image:
specific bag type with a visible construction detail, comma, colour on
its own, comma, the word "style" then style tokens, then ALWAYS one size
fits all / taille unique.
English models to copy:
"Women's shoulder bag with chain strap, black, style y2k gothic, one size fits all"
"Structured handbag with buckle details, cream, style elegant old money, one size fits all"
French models to copy:
"Sac bandoulière femme avec chaîne, noir, style y2k gothic, taille unique"
"Sac à main structuré avec boucle, crème, style elegant old money, taille unique"
Write "style y2k gothic", never "y2k style" or "in a vintage Y2K style". The
ending "one size fits all" / "taille unique" is mandatory.
Never mention material/fabric. Never put measurements, size S, or size M in
the title. Name only details visible on this bag. Return empty strings for
english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified length/width if present, "prices are negotiable :)", "Perfect condition."
French block: title once, verified length/width if present, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
cut detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 bag hashtags such as handbag and y2k.
Do not generate an image.
""",
    "plant": """
PLANT titles MUST copy this comma structure from the product image:
specific plant type with a visible detail, comma, colour on its own,
comma, the word "style" then style tokens. No size.
English models to copy:
"Indoor plant with decorative pot, green, style boho elegant"
"Artificial succulent with ceramic planter, cream, style chic boho"
French models to copy:
"Plante d'intérieur avec pot décoratif, vert, style boho elegant"
"Succulente artificielle avec cache-pot, crème, style chic boho"
Write "style boho elegant", never "boho style".
Never put measurements, size S, size M, or one size in the title. Name
only details visible on this plant. Return empty strings for
english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "prices are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 plant hashtags such as indoorplant and succulent.
Do not generate an image.
""",
    "shelf": """
SHELF titles MUST copy this comma structure from the product image:
specific shelf plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS one size / taille unique.
English models to copy:
"Set of 4 wall shelves with simple cut, brown, style vintage, one size"
"Floating wall shelf with bracket details, cream, style boho elegant, one size"
French models to copy:
"Lot de 4 étagères murales simples, marron, style vintage, taille unique"
"Étagère murale flottante avec supports, crème, style boho elegant, taille unique"
Write "style vintage", never "vintage-style" or "vintage style".
Never mention material/fabric. Never put size S, size 36, or measurements
in the title. Name only details visible on this shelf. Return empty strings
for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified length/width if present, "prices are negotiable :)",
"Perfect condition."
French block: title once, verified length/width if present, "prix négociable :)",
"Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
fictional_brand must be one newly coined professional word, not a style token.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 shelf hashtags unique to this item, such as wallshelf and
oneSize. Never use bag, clutch, handbag, or size36 tags.
Do not generate an image.
""",
    "beanie": """
BEANIE titles MUST copy this comma structure from the product image:
specific beanie plus one or more visible details (cat-ear, pom-pom, cuffed,
slouchy, knit, fuzzy, plush, striped, ribbed), comma, colour on its own,
comma, the word "style" then style tokens, then ALWAYS one size fits all /
taille unique.
English models to copy:
"Fuzzy plush striped cat-ear beanie, brown and cream, style y2k, one size fits all"
"Cuffed pom-pom knit beanie, black, style streetwear grunge, one size fits all"
French models to copy:
"Bonnet oreilles de chat en peluche rayé, marron et crème, style y2k, taille unique"
"Bonnet à revers avec pompon en tricot, noir, style streetwear grunge, taille unique"
Write "style y2k", never "y2k-style" or "y2k style".
Never mention material/fabric such as wool, acrylic, polyester, or fleece.
Never put size S, size 36, or measurements in the title. Name only details
visible on this beanie. Return empty strings for english_description and
french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified length/width if present, "prices are negotiable :)",
"Perfect condition."
French block: title once, verified length/width if present, "prix négociable :)",
"Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
fictional_brand must be one newly coined professional word, not a style token.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 beanie hashtags unique to this item, such as beanie and
catear. Never use bag, clutch, handbag, coat, or size36 tags.
Do not generate an image.
""",
    "organizer": """
ORGANIZER titles MUST copy this comma structure from the product image:
specific organizer plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"3-tier metal countertop organizer with mesh baskets, black, style elegant size S"
"Rotating spice rack organizer with 4 tiers, white, style old money size S"
French models to copy:
"Organiseur de comptoir en métal à 3 niveaux avec paniers, noir, style elegant taille S"
"Organiseur d'épices rotatif à 4 niveaux, blanc, style old money taille S"
Write "style elegant size S", never "elegant style" and never "in a vintage
style". The ending "size S" / "taille S" is mandatory.
Never mention material/fabric. Never put size 36, size 38, size M, or
measurements in the title. Name only details visible on this organizer.
Return empty strings for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "prices are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 22 organizer hashtags unique to this item, such as
kitchenorganizer and sizeS. Never use belt, jacket, coat, dress, bag, or
size36 tags.
Do not generate an image.
""",
    "lamp": """
LAMP titles MUST copy this comma structure from the product image:
specific lamp plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS one size fits all /
taille unique.
English models to copy:
"Table lamp with a twisted 3D-printed design, white and natural wood, style modern, one size fits all"
"Gourd-shaped ribbed glass table lamp, amber, style vintage artdeco, one size fits all"
French models to copy:
"Lampe de table avec design torsadé imprimé en 3D, blanc et bois naturel, style modern, taille unique"
"Lampe de table forme calebasse en verre côtelé, ambre, style vintage artdeco, taille unique"
Write "style modern", never "modern-style" or "modern style". Never mention
material/fabric such as glass, wood, resin, or metal. Never put size S,
size 36, or measurements in the title. Name only details visible on this
lamp. Return empty strings for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified length/width if present, "price negotiable :)",
"Perfect condition."
French block: title once, verified length/width if present, "prix négociable :)",
"Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
fictional_brand must be one newly coined professional word, not a style token.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 lamp hashtags unique to this item, such as tablelamp and
modernlamp. Never use bag, clutch, handbag, coat, or size36 tags.
Do not generate an image.
""",
    "chandelier": """
CHANDELIER titles MUST copy this comma structure from the product image:
specific fixture plus one or more visible details (multi-tiered, crystal,
tiered, beaded, cage, drum, candle-style, metal), comma, colour on its own,
comma, the word "style" then style tokens, then ALWAYS one size fits all /
taille unique. Crystal and metal may be named since they define this
fixture's look.
English models to copy:
"Multi-tiered gold metal and crystal chandelier, gold, style decorative, one size fits all"
"Crystal beaded pendant light with a cage frame, silver, style vintage industrial, one size fits all"
French models to copy:
"Lustre à plusieurs niveaux en métal doré et cristal, doré, style decorative, taille unique"
"Suspension à perles de cristal avec structure cage, argenté, style vintage industrial, taille unique"
Write "style decorative", never "decorative-style" or "decorative style".
Never put size S, size 36, or measurements in the title. Name only details
visible on this fixture. Return empty strings for english_description and
french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified length/width if present, "price negotiable :)",
"Perfect condition."
French block: title once, verified length/width if present, "prix négociable :)",
"Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
fictional_brand must be one newly coined professional word, not a style token.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 chandelier hashtags unique to this item, such as chandelier
and ceilinglight. Never use bag, clutch, handbag, coat, or size36 tags.
Do not generate an image.
""",
    "carpet": """
CARPET titles MUST copy this comma structure from the product image:
specific carpet/rug plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS one size fits all /
taille unique.
English models to copy:
"Area rug with a geometric pattern, light grey, style decorative, one size fits all"
"Hand-woven shaggy rug, cream and beige, style boho minimalist, one size fits all"
French models to copy:
"Tapis à motif géométrique, gris clair, style decorative, taille unique"
"Tapis à poils longs tissé à la main, crème et beige, style boho minimalist, taille unique"
Write "style decorative", never "decorative-style" or "decorative style".
Never mention material/fabric such as wool, cotton, jute, or polyester.
Never put size S, size 36, or measurements in the title. Name only details
visible on this carpet. Return empty strings for english_description and
french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified length/width if present, "price negotiable :)",
"Perfect condition."
French block: title once, verified length/width if present, "prix négociable :)",
"Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
fictional_brand must be one newly coined professional word, not a style token.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 carpet hashtags unique to this item, such as arearug and
geometricrug. Never use bag, clutch, handbag, coat, or size36 tags.
Do not generate an image.
""",
    "cushion": """
CUSHION titles MUST copy this comma structure from the product image:
specific cushion plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS one size fits all /
taille unique.
English models to copy:
"Decorative cushion with a textured weave, ivory, style decorative, one size fits all"
"Embroidered cushion with tassel trim, terracotta and cream, style boho, one size fits all"
French models to copy:
"Coussin décoratif à texture travaillée, ivoire, style decorative, taille unique"
"Coussin brodé avec pompons, terracotta et crème, style boho, taille unique"
Write "style decorative", never "decorative-style" or "decorative style".
Never mention material/fabric such as linen, cotton, velvet, or polyester.
Never put size S, size 36, or measurements in the title. Name only details
visible on this cushion. Return empty strings for english_description and
french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified length/width if present, "price negotiable :)",
"Perfect condition."
French block: title once, verified length/width if present, "prix négociable :)",
"Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
fictional_brand must be one newly coined professional word, not a style token.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 cushion hashtags unique to this item, such as throwpillow
and decoraccessory. Never use bag, clutch, handbag, coat, or size36 tags.
Do not generate an image.
""",
    "mirror": """
MIRROR titles MUST copy this comma structure from the product image:
specific mirror plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"Asymmetrical organic-shaped wall mirror, natural, style boho sophisticated size S"
"Hand-carved sculptural tabletop mirror, black, style dark academia size S"
French models to copy:
"Miroir mural asymétrique de forme organique, naturel, style boho sophisticated taille S"
"Miroir de table sculpté à la main, noir, style dark academia taille S"
Write "style boho sophisticated size S", never "boho style" and never "in a
vintage style". The ending "size S" / "taille S" is mandatory.
Never mention material/fabric such as wood, walnut, resin, or metal. Never
put size 36, size 38, size M, or measurements in the title. Name only
details visible on this mirror. Return empty strings for
english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "price negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 mirror hashtags unique to this item, such as
organicmirror and bohochic. Never use belt, jacket, coat, dress, bag, or
size36 tags.
Do not generate an image.
""",
    "sculpture": """
SCULPTURE titles MUST copy this comma structure from the product image:
specific sculpture plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"Delicate Justice sculpture with carved details, brown, style elegant sophisticated size S"
"Abstract figurine sculpture, black, style dark academia size S"
French models to copy:
"Sculpture Justice délicate aux détails sculptés, marron, style elegant sophisticated taille S"
"Sculpture figurine abstraite, noire, style dark academia taille S"
Write "style elegant sophisticated size S", never "elegant style" and
never "in a vintage style". The ending "size S" / "taille S" is mandatory.
Never say "one size" or "taille unique" for this type.
Never mention material/fabric such as resin, ceramic, wood, or metal. Never
put size 36, size 38, size M, or measurements in the title. Name only
details visible on this sculpture. Return empty strings for
english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "price negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 sculpture hashtags unique to this item, such as
sculpturedecor and artpiece. Never use cape, poncho, fur, belt, jacket,
coat, dress, bag, or size36 tags.
Do not generate an image.
""",
    "curtain": """
CURTAIN titles MUST copy this comma structure from the product image:
specific curtain plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"Floral lace scalloped-edge sheer curtain, black, style gothic size S"
"Embroidered blackout curtain panel, cream, style old money size S"
French models to copy:
"Rideau en dentelle florale à bordure festonnée, noir, style gothic taille S"
"Panneau de rideau occultant brodé, crème, style old money taille S"
Write "style gothic size S", never "gothic style" and never "in a vintage
style". The ending "size S" / "taille S" is mandatory.
Never mention material/fabric such as linen, cotton, polyester, or velvet.
Never put size 36, size 38, size M, or measurements in the title. Name only
details visible on this curtain. Return empty strings for
english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "price are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 curtain hashtags unique to this item, such as lacecurtain
and gothichomedecor. Never use belt, jacket, coat, dress, bag, or size36
tags.
Do not generate an image.
""",
    "necktie": """
NECKTIE titles MUST copy this comma structure from the product image:
specific necktie plus a visible pattern detail, comma, colour on its own,
comma, the word "style" then style tokens. Never add a size marker of any
kind.
English models to copy:
"Dark green paisley-patterned necktie, dark green, style elegant"
"Striped silk-look necktie with a slim cut, navy and gold, style sophisticated"
French models to copy:
"Cravate a motifs cachemire, vert fonce, style elegant"
"Cravate rayee a coupe fine, bleu marine et or, style sophisticated"
Write "style elegant", never "elegant style" and never "in a vintage
style". Never write "size S", "one size fits all", or any numeric size.
Never mention material/fabric such as silk, polyester, cotton, or wool.
Name only details visible on this necktie. Return empty strings for
english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, verified length/width if present, "price are negotiable :)", "Perfect condition."
French block: title once, verified length/width if present, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
fictional_brand must be one newly coined professional word, not a style token.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 necktie hashtags unique to this item, such as paisleytie
and menswear. Never use belt, jacket, coat, dress, bag, or size36 tags.
Do not generate an image.
""",
    "leg_warmer": """
LEG_WARMER titles MUST copy this comma structure from the product image:
specific leg warmer type plus a visible detail, comma, colour on its own,
comma, the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"Fluffy faux fur leg warmers, rich brown, style boho size S"
"Plush shaggy leg warmers with a cropped cut, cream, style y2k grunge size S"
French models to copy:
"Jambières en fausse fourrure moelleuse, marron riche, style boho taille S"
"Jambières pelucheuses courtes et texturées, crème, style y2k grunge taille S"
Write "style boho size S", never "boho style" and never "in a vintage
style". The ending "size S" / "taille S" is mandatory.
Never mention material/fabric. Never put size 36, size 38, size M, or
measurements in the title. Name only details visible on this item.
Return empty strings for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "prices are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 leg warmer hashtags unique to this item, such as fauxfur
and legwarmers. Never use belt, jacket, coat, dress, bag, or size36 tags.
Do not generate an image.
""",
    "jewelry_box": """
JEWELRY_BOX titles MUST copy this comma structure from the product image:
specific jewelry box type plus a visible detail, comma, colour on its own,
comma, the word "style" then style tokens, then ALWAYS size L.
English models to copy:
"Large jewelry box with multiple storage tiers, pink textured, style chic size L"
"Mirrored jewelry box with a lockable drawer, black, style elegant size L"
French models to copy:
"Grand coffret à bijoux à plusieurs niveaux, rose texturé, style chic taille L"
"Coffret à bijoux avec miroir et tiroir verrouillable, noir, style elegant taille L"
Write "style chic size L", never "chic style" and never "in a vintage
style". The ending "size L" / "taille L" is mandatory.
Never mention material/fabric. Never put size 40, size 42, size M, or
measurements in the title. Name only details visible on this jewelry box.
Return empty strings for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "price are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 jewelry box hashtags unique to this item, such as
jewelrybox and jewelrystorage, gender-neutral, never women-targeted tags.
Never use belt, jacket, coat, dress, bag, size36, or size40 tags.
Do not generate an image.
""",
    "lace_umbrella": """
LACE_UMBRELLA titles MUST copy this comma structure from the product image:
specific parasol type plus a visible detail, comma, colour on its own,
comma, the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"Elegant bohemian-style lace parasol, white, style elegant boho size S"
"Embroidered floral lace parasol with a ruffled trim, cream, style old money size S"
French models to copy:
"Ombrelle en dentelle élégante de style bohème, blanc, style elegant boho taille S"
"Ombrelle en dentelle fleurie brodée à volants, crème, style old money taille S"
Write "style elegant boho size S", never "elegant style" and never "in a
vintage style". The ending "size S" / "taille S" is mandatory.
Never mention material/fabric. Never put size 36, size 38, size M, or
measurements in the title. Name only details visible on this item.
Return empty strings for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "prices are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 lace parasol hashtags unique to this item, such as
laceparasol and bohemian. Never use belt, jacket, coat, dress, bag, or
size36 tags.
Do not generate an image.
""",
    "belt": """
BELT titles MUST copy this comma structure from the product image:
specific belt type plus a visible detail, comma, colour on its own, comma,
the word "style" then style tokens, then ALWAYS size S.
English models to copy:
"Vintage western-style carved buckle belt, brown, style boho bohemian size S"
"Studded chain belt with a metal buckle, black, style street grunge size S"
French models to copy:
"Ceinture western vintage à boucle sculptée, marron, style boho bohemian taille S"
"Ceinture à chaîne cloutée avec boucle métallique, noir, style street grunge taille S"
Write "style boho bohemian size S", never "boho style" and never "in a
vintage style". The ending "size S" / "taille S" is mandatory.
Never mention material/fabric. Never put size 36, size 38, size M, or
measurements in the title. Name only details visible on this belt.
Return empty strings for english_description and french_description.
The formatter will build this exact English block with a blank line between
each step: title once, "price are negotiable :)", "Perfect condition."
French block: title once, "prix négociable :)", "Parfait état."
Do not repeat the title a second time in the description. The listing title
field is the other copy, for a total of 2.
In the fictional_brand field, return exactly one clothing style token from:
boho, bohemian, chic, elegant, oldmoney, y2k, goth, street, grunge, dark.
Both titles MUST be between 65 and 100 characters by adding another visible
detail from the image, never generic filler.
Forbidden hashtag words and synonyms: vinted, france, new, unused, neuf, neuve.
Generate 20 to 25 belt hashtags unique to this item, such as bohobelt and
westernbelt. Never use jacket, coat, dress, bag, or size36 tags.
Do not generate an image.
""",
}


class FastListingDraft(BaseModel):
    fictional_brand: str = Field(min_length=3, max_length=30)
    english_title: str = Field(min_length=65, max_length=100)
    french_title: str = Field(min_length=65, max_length=100)
    english_description: str = Field(default="", max_length=1_200)
    french_description: str = Field(default="", max_length=1_200)
    english_hashtags: list[str] = Field(min_length=5, max_length=25)
    french_hashtags: list[str] = Field(min_length=5, max_length=25)

    @field_validator("fictional_brand")
    @classmethod
    def normalize_brand(cls, value: str) -> str:
        return "".join(value.split())

    @field_validator("english_title", "french_title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return " ".join(value.split())


ENGLISH_MEASUREMENT_LABELS = {
    "Poitrine": "Bust",
    "Tour de taille": "Waist",
    "Hanches": "Hips",
    "Longueur": "Length",
    "Largeur": "Width",
    "Longueur des manches": "Sleeve length",
    "Longueur des brides": "Strap length",
    "Carrure": "Shoulder width",
    "Tour de bras": "Arm circumference",
    "Tour de poignet": "Wrist circumference",
    "Cuisse": "Thigh",
    "Entrejambe": "Inseam",
    "Hauteur": "Height",
    "Profondeur": "Depth",
    "Diamètre": "Diameter",
    "Circonférence": "Circumference",
    "Épaisseur": "Thickness",
}

FALLBACK_HASHTAGS = {
    "dress": {
        "english": [
            "#dress", "#womensdress", "#sizeS", "#elegantstyle", "#fashion",
            "#outfit", "#wardrobe", "#chic", "#occasionwear", "#eveninglook",
            "#daydress", "#mididress", "#feminine", "#styleinspiration",
            "#womensfashion", "#dressstyle", "#closetstyle", "#fashionlook",
            "#outfitinspiration", "#sophisticatedstyle",
        ],
        "french": [
            "#robe", "#robefemme", "#tailleS", "#styleelegant", "#mode",
            "#tenue", "#garderobe", "#chic", "#tenueoccasion", "#lookdusoir",
            "#robedujour", "#robemidi", "#feminin", "#inspirationstyle",
            "#modefemme", "#stylerobe", "#styledressing", "#lookmode",
            "#inspirationtenue", "#stylesophistique",
        ],
    },
    "dress_m": {
        "english": [
            "#dress", "#womensdress", "#sizeM", "#elegantstyle", "#fashion",
            "#outfit", "#wardrobe", "#chic", "#occasionwear", "#eveninglook",
            "#daydress", "#mididress", "#feminine", "#styleinspiration",
            "#womensfashion", "#dressstyle", "#closetstyle", "#fashionlook",
            "#outfitinspiration", "#sophisticatedstyle",
        ],
        "french": [
            "#robe", "#robefemme", "#tailleM", "#styleelegant", "#mode",
            "#tenue", "#garderobe", "#chic", "#tenueoccasion", "#lookdusoir",
            "#robedujour", "#robemidi", "#feminin", "#inspirationstyle",
            "#modefemme", "#stylerobe", "#styledressing", "#lookmode",
            "#inspirationtenue", "#stylesophistique",
        ],
    },
    "earrings": {
        "english": [
            "#earrings", "#womensearrings", "#jewellery", "#accessories",
            "#earringstyle", "#statementearrings", "#fashionjewellery",
            "#accessorystyle", "#elegantstyle", "#chic", "#jewellerylover",
            "#outfitaccessories", "#womensfashion", "#styleinspiration",
            "#earringlover", "#jewellerystyle", "#fashionaccessory",
            "#sophisticatedstyle", "#giftidea", "#oneSize",
        ],
        "french": [
            "#bouclesdoreilles", "#bijouxfemme", "#bijoux", "#accessoires",
            "#stylebijoux", "#bouclesstatement", "#bijouxfantaisie",
            "#styleaccessoire", "#styleelegant", "#chic", "#passionbijoux",
            "#accessoiretenue", "#modefemme", "#inspirationstyle",
            "#bouclesfemme", "#stylebijou", "#accessoiremode",
            "#stylesophistique", "#ideecadeau", "#tailleunique",
        ],
    },
    "skirt": {
        "english": [
            "#skirt", "#womensskirt", "#sizeS", "#midiskirt", "#maxiskirt",
            "#longskirt", "#pleatedskirt", "#splitskirt", "#wrapskirt",
            "#highwaistskirt", "#alineskirt", "#miniskirt", "#pencilskirt",
            "#flowyskirt", "#tieredskirt", "#laceskirt", "#printedskirt",
            "#blackskirt", "#navyskirt", "#creamskirt", "#skirtsizeS",
            "#asymmetricskirt", "#buckleskirt", "#ruffleskirt",
        ],
        "french": [
            "#jupe", "#jupefemme", "#tailleS", "#jupemidi", "#jupemaxi",
            "#jupelongue", "#jupeplissee", "#jupefendue", "#jupeportefeuille",
            "#jupetaillehaute", "#jupeevasee", "#jupemini", "#jupecrayon",
            "#jupefluide", "#jupeetages", "#jupedentelle", "#jupeimprimee",
            "#jupenoire", "#jupebleumarine", "#jupecreme", "#jupetailleS",
            "#jupeasymetrique", "#jupeboucles", "#jupevolants",
        ],
    },
    "jeans": {
        "english": [
            "#jeans", "#mensjeans", "#baggyjeans", "#sizeL", "#menswear",
            "#streetwear", "#widelegjeans", "#mensoutfit", "#mensfashion",
            "#relaxedfit", "#cargojeans", "#streetstyle", "#y2k", "#grunge",
            "#mensclothing", "#wideleg", "#straightjeans", "#mensstyle",
            "#casualwear", "#menslook",
        ],
        "french": [
            "#jean", "#jeanhomme", "#jeanbaggy", "#tailleL", "#modehomme",
            "#streetwear", "#jeanlarge", "#lookhomme", "#vetementhomme",
            "#coupeoversize", "#jeancargo", "#stylestreet", "#y2k", "#grunge",
            "#jambelarge", "#jeandroit", "#stylehomme", "#tenuehomme",
            "#inspirationlook", "#lookstreet",
        ],
    },
    "long_boots": {
        "english": [
            "#boots", "#kneehighboots", "#longboots", "#size38", "#heeledboots",
            "#womensboots", "#highheels", "#bootstyle", "#elegantstyle",
            "#oldmoney", "#winterboots", "#outfitboots", "#womensfashion",
            "#pointedtoe", "#platformboots", "#gothicboots", "#y2kboots",
            "#bootseason", "#chicboots", "#sophisticatedstyle",
        ],
        "french": [
            "#bottes", "#botteshautes", "#botteslongues", "#taille38",
            "#bottestalons", "#bottesfemme", "#talons", "#stylebottes",
            "#styleelegant", "#oldmoney", "#botteshiver", "#lookbottes",
            "#modefemme", "#boutpointu", "#bottesplateforme", "#bottesgothic",
            "#bottesy2k",             "#saisonbottes", "#botteschic", "#stylesophistique",
        ],
    },
    "heels": {
        "english": [
            "#heels", "#heeledsandals", "#sandals", "#size38", "#womensheels",
            "#highheels", "#eveningheels", "#heelstyle", "#elegantstyle",
            "#oldmoney", "#partyheels", "#outfitheels", "#womensfashion",
            "#pointedtoe", "#platformheels", "#gothicheels", "#y2kheels",
            "#sequinheels", "#chicheels", "#sophisticatedstyle",
        ],
        "french": [
            "#talons", "#sandalesatalons", "#sandales", "#taille38",
            "#talonsfemme", "#hautstalons", "#talonsssoir", "#styletalons",
            "#styleelegant", "#oldmoney", "#looktalons", "#modefemme",
            "#boutpointu", "#talonsplateforme", "#talonsgothic",
            "#talonsy2k", "#talonspaillettes", "#talonschic", "#stylesophistique",
            "#sandalesfemme",
        ],
    },
    "coat": {
        "english": [
            "#coat", "#womenscoat", "#trenchcoat", "#sizeS", "#longcoat",
            "#wintercoat", "#elegantcoat", "#coatstyle", "#womensfashion",
            "#cape", "#womenscape", "#poncho", "#chiccoat", "#oldmoney",
            "#winteroutfit", "#outerwear", "#coatlook", "#elegantstyle",
            "#creamcoat", "#autumncoat",
        ],
        "french": [
            "#manteau", "#manteaufemme", "#trench", "#tailleS", "#manteaulong",
            "#manteauhiver", "#manteauelegant", "#stylemanteau", "#modefemme",
            "#cape", "#poncho", "#manteauoversize", "#manteauchic", "#oldmoney",
            "#lookhiver", "#outerwear", "#lookmanteau", "#styleelegant",
            "#manteaucreme", "#manteauautomne",
        ],
    },
    "jacket": {
        "english": [
            "#jacket", "#womensjacket", "#bikerjacket", "#sizeS", "#bomberjacket",
            "#motojacket", "#croppedjacket", "#outerwear", "#streetwear", "#y2k",
            "#grunge", "#womensfashion", "#jacketstyle", "#autumnlook",
            "#casualjacket", "#zipjacket", "#patternedjacket", "#chiclook",
            "#everydayjacket", "#womensouterwear",
        ],
        "french": [
            "#veste", "#vestefemme", "#vestebiker", "#tailleS", "#blouson",
            "#vestemoto", "#vestecropped", "#outerwear", "#streetwear", "#y2k",
            "#grunge", "#modefemme", "#styleveste", "#lookautomne",
            "#vestecasual", "#vestezip", "#vestemotif", "#lookchic",
            "#vestequotidien", "#vesteouterwear",
        ],
    },
    "bag": {
        "english": [
            "#handbag", "#womenshandbag", "#womensbag", "#shoulderbag",
            "#blackbag", "#accessory", "#womensfashion", "#chiclook", "#chic",
            "#y2k", "#vintage", "#vintagehandbag", "#minimalist", "#90s",
            "#bagstyle", "#everydaybag", "#crossbodybag", "#elegantstyle",
            "#wardrobe", "#fashionlook",
        ],
        "french": [
            "#sac", "#sacfemme", "#sacmain", "#sacbandouliere", "#accessoires",
            "#stylesac", "#modefemme", "#lookchic", "#chic", "#y2k",
            "#vintage", "#sacvintage", "#minimaliste", "#annees90",
            "#sacquotidien", "#saccroise", "#styleelegant", "#garderobe",
            "#lookmode", "#accessoiretenue",
        ],
    },
    "hat": {
        "english": [
            "#hat", "#womenshat", "#cowboyhat", "#westernhat", "#accessories",
            "#hatstyle", "#westernstyle", "#cowboystyle", "#cowgirlstyle",
            "#wildwest", "#bohostyle", "#elegantstyle", "#streetwearstyle",
            "#grungestyle", "#y2kstyle", "#outfitaccessory", "#fashionaccessory",
            "#chiclook", "#statementhat", "#oneSize",
        ],
        "french": [
            "#chapeau", "#chapeaufemme", "#chapeaucowboy", "#stylewestern",
            "#accessoires", "#stylechapeau", "#stylecowboy", "#stylecowgirl",
            "#wildwest", "#styleboho", "#styleelegant", "#stylestreetwear",
            "#stylegrunge", "#styley2k", "#accessoiretenue", "#accessoiremode",
            "#lookchic", "#chapeaustatement", "#tailleunique", "#modefemme",
        ],
    },
    "mask": {
        "english": [
            "#mask", "#womensmask", "#sequinmask", "#masquerademask", "#blackmask",
            "#halloweenmask", "#partymask", "#costumemask", "#y2k", "#gothic",
            "#accessories", "#oneSize", "#masquerade", "#sparklemask",
            "#festivalmask", "#outfitaccessory", "#eveningmask", "#chiclook",
            "#womensaccessory", "#maskstyle",
        ],
        "french": [
            "#masque", "#masquefemme", "#masquepaillettes", "#masquemascarade",
            "#masquenoir", "#masquehalloween", "#masquefete", "#masquecostume",
            "#y2k", "#gothique", "#accessoires", "#tailleunique", "#mascarade",
            "#masquebrillant", "#masquefestival", "#accessoiretenue",
            "#masquesoir", "#lookchic", "#accessoirefemme", "#stylemasque",
        ],
    },
    "plant": {
        "english": [
            "#plant", "#indoorplant", "#houseplant", "#pottedplant",
            "#artificialplant", "#succulent", "#homedecor", "#plantdecor",
            "#greenplant", "#plantlover", "#bohohome", "#plantstyle",
            "#decorativeplant", "#plantpot", "#leafyplant", "#plantstyling",
            "#homestyle", "#greenery", "#plantshop", "#fauxplant",
        ],
        "french": [
            "#plante", "#planteinterieur", "#planteverte", "#planteartificielle",
            "#succulente", "#decointerieur", "#decoplante", "#cachepot",
            "#styleboho", "#plantesverte", "#amourplantes", "#decoration",
            "#plantepot", "#feuillage", "#decoverte", "#styleplante",
            "#maisonboho", "#plantes", "#plantefaux", "#decohome",
        ],
    },
    "shelf": {
        "english": [
            "#shelf", "#wallshelf", "#woodenshelf", "#brownshelf", "#vintageshelf",
            "#setof4", "#homedecor", "#wallstorage", "#bohohome", "#vintage",
            "#floatingshelf", "#storageshelf", "#simpleshelf", "#walldecor",
            "#displayshelf", "#onesize", "#rusticshelf", "#homeshelf",
            "#shelfstyling", "#interiordecor",
        ],
        "french": [
            "#etagere", "#etageremurale", "#etagerebois", "#etageremarron",
            "#etagerevintage", "#lotde4", "#decointerieur", "#rangement",
            "#maisonboho", "#vintage", "#etagereflottante", "#decoration",
            "#etagererustique", "#etageresimple", "#decmurale", "#tailleunique",
            "#etageremaison", "#rangementmural", "#styleetagere", "#decohome",
        ],
    },
    "beanie": {
        "english": [
            "#beanie", "#cathear", "#plush", "#fuzzy", "#striped",
            "#brownandcream", "#y2k", "#harajuku", "#kawaii", "#streetwear",
            "#accessories", "#winterfashion", "#cute", "#cozy", "#trendy",
            "#fashion", "#aesthetic", "#headwear", "#animalears", "#unisex",
        ],
        "french": [
            "#bonnet", "#oreillesdechat", "#peluche", "#doux", "#raye",
            "#marronetcreme", "#y2k", "#harajuku", "#kawaii", "#streetwear",
            "#accessoires", "#modehiver", "#mignon", "#cosy", "#tendance",
            "#mode", "#esthetique", "#couvrechef", "#oreillesanimales", "#unisexe",
        ],
    },
    "organizer": {
        "english": [
            "#kitchenorganization", "#kitchendecor", "#countertopstorage",
            "#spacesaver", "#industrialstyle", "#homestorage",
            "#minimalistkitchen", "#heavyduty", "#tidyhome", "#modernkitchen",
            "#spiceorganizer", "#shelfie", "#kitchenessentials",
            "#smallspacesolutions", "#metalwork", "#interiorinspiration",
            "#organizedlife", "#functionaldecor", "#homeorganization",
            "#storageideas",
        ],
        "french": [
            "#organisationcuisine", "#decocuisine", "#rangementcomptoir",
            "#gainplace", "#styleindustriel", "#rangementmaison",
            "#cuisineminimaliste", "#robuste", "#maisonrangee",
            "#cuisinemoderne", "#rangementepices", "#etagerestyle",
            "#essentielscuisine", "#petitespaces", "#travailmetal",
            "#inspirationinterieur", "#vieorganisee", "#decofonctionnelle",
            "#organisationmaison", "#ideerangement",
        ],
    },
    "lamp": {
        "english": [
            "#tablelamp", "#gourdshape", "#glasslamp", "#ribbedglass",
            "#modernlamp", "#vintagestyle", "#artdeco", "#3dprintedlamp",
            "#modernlighting", "#twisteddesign", "#homedecor",
            "#interiordesign", "#scandinaviandesign", "#minimalist",
            "#ambientlight", "#cozyhome", "#uniquelamp", "#bedsidelamp",
            "#livingroomdecor", "#designobject", "#ecofriendlydesign",
            "#geometricdecor", "#modernart", "#lightingideas",
            "#creativehomedecor",
        ],
        "french": [
            "#lampedetable", "#formecalebasse", "#lampeenverre",
            "#verrecotele", "#lampemoderne", "#stylevintage", "#artdeco",
            "#lampeimprimee3d", "#eclairagemoderne", "#designtorsade",
            "#decointerieur", "#designinterieur", "#designscandinave",
            "#minimaliste", "#lumieredambiance", "#ambiancecosy",
            "#lampeunique", "#lampedechevet", "#decosalon", "#objetdesign",
        ],
    },
    "chandelier": {
        "english": [
            "#gold", "#crystal", "#ceilinglight", "#flushmount",
            "#chandelier", "#glamorous", "#luxury", "#lighting",
            "#homedecor", "#interiordesign", "#sparkle", "#elegant",
            "#statementpiece", "#homeluxury", "#cozy", "#ambiance",
            "#homestyling", "#decorativestyle", "#golddecor",
            "#crystaldecor",
        ],
        "french": [
            "#dore", "#cristal", "#plafonnier", "#lustreplafond",
            "#lustre", "#glamour", "#luxe", "#eclairage",
            "#decointerieur", "#designinterieur", "#etincelant", "#elegant",
            "#piecestatement", "#luxemaison", "#cosy", "#ambiance",
            "#decorationmaison", "#styledecoratif", "#decodoree",
            "#decocristal",
        ],
    },
    "carpet": {
        "english": [
            "#arearug", "#geometricrug", "#shaggyrug", "#handwovenrug",
            "#homedecor", "#floordecor", "#modernrug", "#bohorug",
            "#interiordesign", "#minimalistdecor", "#scandinaviandesign",
            "#cozyhome", "#livingroomdecor", "#uniquerug", "#runnerrug",
            "#roundrug", "#decorativerug", "#softfurnishing",
            "#designobject", "#creativehomedecor",
        ],
        "french": [
            "#tapisdecoration", "#tapisgeometrique", "#tapispoilslongs",
            "#tapistissemain", "#decointerieur", "#decosol", "#tapismoderne",
            "#tapisboheme", "#designinterieur", "#decominimaliste",
            "#designscandinave", "#ambiancecosy", "#decosalon",
            "#tapisunique", "#tapiscouloir", "#tapisrond", "#tapisdeco",
            "#textilemaison", "#objetdesign", "#decointerieurcreatif",
        ],
    },
    "cushion": {
        "english": [
            "#decor", "#home", "#livingroom", "#bedroom", "#textile",
            "#ivory", "#decorativestyle", "#scandinavian", "#minimalist",
            "#bohemian", "#comfort", "#decoraccessory", "#interiordesign",
            "#homesweethome", "#decoridea", "#cozy", "#hygge",
            "#throwpillow", "#cushioncover", "#softfurnishing",
        ],
        "french": [
            "#decoration", "#maison", "#salon", "#chambre", "#textile",
            "#ivoire", "#styledecoratif", "#scandinave", "#minimaliste",
            "#boheme", "#confort", "#accessoiredeco", "#designinterieur",
            "#chezmoi", "#ideedeco", "#cocooning", "#hygge",
            "#coussindecoratif", "#houssedecoussin", "#textilemaison",
        ],
    },
    "mirror": {
        "english": [
            "#walnutmirror", "#organicmirror", "#asymmetricalmirror",
            "#scandiboho", "#woodhomedecor", "#tabletopmirror",
            "#uniquemirror", "#interiorstyling", "#handcarvedwood",
            "#sculpturaldesign", "#bohochic", "#sophisticateddecor",
            "#eclecticdecor", "#bohemianstyle", "#minimalistwood",
            "#wabisabi", "#artisanalwood", "#modernorganic", "#homedecor",
            "#statementpiece", "#smallfurniture", "#naturalmaterials",
        ],
        "french": [
            "#miroirnoyer", "#miroirorganique", "#miroirasymetrique",
            "#scandiboheme", "#decoboisinterieur", "#miroirdetable",
            "#miroirunique", "#stylinginterieur", "#boissculptemain",
            "#designsculptural", "#bohochic", "#decosophistiquee",
            "#decoeclectique", "#stylebohemien", "#boisminimaliste",
            "#wabisabi", "#boisartisanal", "#organiquemoderne",
            "#decointerieur", "#pieceforte", "#petitmobilier",
            "#materiauxnaturels",
        ],
    },
    "sculpture": {
        "english": [
            "#sculpturedecor", "#artpiece", "#statuedecor", "#decorativeart",
            "#figurine", "#homedecor", "#tabletopdecor", "#modernsculpture",
            "#interiordesign", "#uniquedecor", "#elegantdecor", "#shelfie",
            "#accentdecor", "#artlover", "#statementpiece",
            "#livingroomdecor", "#giftidea", "#artobject", "#sculptedart",
            "#decorativeobject",
        ],
        "french": [
            "#sculpturedeco", "#objetdart", "#statuedeco", "#artdecoratif",
            "#figurine", "#decointerieur", "#decodetable", "#sculpturemoderne",
            "#designinterieur", "#decounique", "#decoelegante", "#etagere",
            "#decoaccent", "#amateurdart", "#pieceforte", "#decosalon",
            "#idecadeau", "#decomoderne", "#artsculpte", "#objetdecoratif",
        ],
    },
    "curtain": {
        "english": [
            "#blacklace", "#curtain", "#gothicstyle", "#darkhomedecor",
            "#victorianstyle", "#lacecurtains", "#windowtreatments",
            "#moodyinteriors", "#bohogoth", "#alternativehome",
            "#housedecor", "#gothichomedecor", "#romanticgoth",
            "#sheercurtains", "#homeindustry", "#darkaesthetic",
            "#uniquehomedecor", "#curtainpanel", "#windowdecor",
            "#interiordesign",
        ],
        "french": [
            "#dentellenoire", "#rideau", "#stylegothique", "#decosombre",
            "#stylevictorien", "#rideauxdentelle", "#decofenetre",
            "#ambiancesombre", "#bohogoth", "#maisonalternative",
            "#decomaison", "#decogothique", "#romantiquegoth",
            "#rideauvoile", "#industriemaison", "#esthetiquesombre",
            "#decouniquemaison", "#panneaurideau", "#decofenetrerideau",
            "#designinterieur",
        ],
    },
    "necktie": {
        "english": [
            "#necktie", "#tie", "#darkgreen", "#paisley", "#paisleytie",
            "#menswear", "#formalwear", "#businesscasual", "#gentlemanstyle",
            "#classicsuit", "#silknecktie", "#accessorize", "#mensstyle",
            "#gqstyle", "#weddingattire", "#workwear", "#dapper",
            "#tailored", "#smartcasual", "#vintageinspired",
        ],
        "french": [
            "#cravate", "#cravates", "#vertfonce", "#cachemire", "#cravatecachemire",
            "#modehomme", "#tenueformelle", "#businesscasual", "#styleelegant",
            "#costumeclassique", "#cravatesoie", "#accessoirehomme", "#stylehomme",
            "#chic", "#tenuemariage", "#tenuetravail", "#elegance",
            "#surmesure", "#smartcasual", "#inspirationvintage",
        ],
    },
    "leg_warmer": {
        "english": [
            "#fauxfur", "#furyleggings", "#legwarmers", "#winterfashion",
            "#y2kfashion", "#festivalfashion", "#bohochic", "#hippie",
            "#vintageinspired", "#fluffy", "#cosy", "#bootsaccessories",
            "#winteroutfit", "#streetstyle", "#grunge", "#goth",
            "#ravewear", "#apreski", "#y2k", "#warmers", "#fauxfurtrim",
            "#statementpiece",
        ],
        "french": [
            "#faussefourrure", "#jambieresfourrure", "#jambieres", "#modehiver",
            "#styley2k", "#modefestival", "#bohochic", "#hippie",
            "#inspirationvintage", "#moelleux", "#cosy", "#accessoirebottes",
            "#tenuehiver", "#streetstyle", "#grunge", "#goth",
            "#tenuerave", "#apreski", "#y2k", "#chauffejambes",
            "#borduresfourrure", "#pieceforte",
        ],
    },
    "jewelry_box": {
        "english": [
            "#jewelrybox", "#storage", "#organizer", "#jewelrystorage",
            "#pinkjewelrybox", "#leatherlook", "#chic", "#luxury",
            "#jewelrystand", "#earringholder", "#necklaceholder", "#ringbox",
            "#homedecor", "#accessories", "#vanity", "#giftidea",
            "#jewelrydisplay", "#modern", "#traveljewelrycase", "#jewelryorganizer",
        ],
        "french": [
            "#coffretabijoux", "#rangement", "#organiseur", "#rangementbijoux",
            "#coffretrose", "#effetcuir", "#chic", "#luxe",
            "#presentoirbijoux", "#porteboucles", "#portecollier", "#boiteabague",
            "#decomaison", "#accessoires", "#coiffeuse", "#idecadeau",
            "#vitrinebijoux", "#moderne", "#etuivoyagebijoux", "#organiseurbijoux",
        ],
    },
    "lace_umbrella": {
        "english": [
            "#lace", "#parasol", "#umbrella", "#wedding", "#bride",
            "#boho", "#bohemian", "#elegant", "#oldmoney", "#y2k",
            "#gothic", "#streetwear", "#grunge", "#darkacademia",
            "#sophisticated", "#chic", "#vintage", "#accessory", "#summer",
            "#embroidery", "#white", "#cream", "#party",
        ],
        "french": [
            "#dentelle", "#ombrelle", "#parapluie", "#mariage", "#mariee",
            "#boheme", "#bohemien", "#elegant", "#oldmoney", "#y2k",
            "#gothique", "#streetwear", "#grunge", "#darkacademia",
            "#sophistique", "#chic", "#vintage", "#accessoire", "#ete",
            "#broderie", "#blanc", "#creme", "#fete",
        ],
    },
    "belt": {
        "english": [
            "#cowboybelt", "#brownbelt", "#bohobelt", "#bohemianbelt",
            "#westernbelt", "#statementbelt", "#vintagebelt", "#retrostyle",
            "#alternativestyle", "#streetwear", "#accessoryaddict",
            "#beltlover", "#bohochic", "#bohoaesthetic", "#wardrobestaple",
            "#statementaccessory", "#secondhandstyle", "#bohofashion",
            "#westernfashion", "#carvedbelt",
        ],
        "french": [
            "#ceinturecowboy", "#ceinturemarron", "#ceintureboho", "#ceinturebohemien",
            "#ceinturewestern", "#ceinturestatement", "#ceinturevintage", "#stylevintage",
            "#stylealternatif", "#streetwear", "#accroaccessoires",
            "#amoureuxceintures", "#bohochic", "#esthetiqueboheme", "#basique",
            "#accessoirestatement", "#stylesecondemain", "#modeboheme",
            "#modewestern", "#ceinturesculptee",
        ],
    },
}

FORBIDDEN_HASHTAGS = {
    "vinted",
    "france",
    "new",
    "brandnew",
    "unused",
    "unworn",
    "neverused",
    "jamaisporte",
    "jamaisportee",
    "neuf",
    "neuve",
    "nouveau",
    "nouvelle",
}


def _clean_draft_hashtags(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        tag = "#" + value.strip().lstrip("#").replace(" ", "")
        normalized = tag[1:].casefold()
        if (
            tag == "#"
            or any(
                normalized == word
                or normalized.startswith(word)
                or normalized.endswith(word)
                for word in FORBIDDEN_HASHTAGS
            )
            or tag in cleaned
        ):
            continue
        cleaned.append(tag)
    return cleaned


def _rotate_sequence(values: list[str], seed: str) -> list[str]:
    if not values:
        return values
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()
    offset = int(digest[:8], 16) % len(values)
    return values[offset:] + values[:offset]


def _ensure_twenty_hashtags(
    values: list[str],
    listing_type: str,
    language: str,
    product: dict[str, Any] | None = None,
) -> list[str]:
    completed = list(dict.fromkeys(values))
    if listing_type == "jeans":
        blocked = (
            "women", "womens", "ladies", "femme", "size36", "size40", "sizes",
            "coat", "trench", "dress", "robe", "skirt", "jupe",
            "belt", "ceinture", "lsizebelt",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
        for tag in _listing_specific_hashtags(product, listing_type, language):
            if tag not in completed:
                completed.insert(0, tag)
    elif listing_type == "skirt":
        blocked = (
            "dress", "robe", "jeans", "jean", "coat", "trench", "boots",
            "heels", "bag", "belt", "ceinture", "fashion", "wardrobe",
            "daylook", "closetstyle", "garderobe", "lookdujour",
            "styledressing",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
        specific = _listing_specific_hashtags(product, listing_type, language)
        completed = list(dict.fromkeys([*specific, *completed]))
    elif listing_type == "coat":
        blocked = ("belt", "ceinture", "size36", "taille36", "verygoodcondition")
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "jacket":
        blocked = (
            "belt", "ceinture", "size36", "taille36", "verygoodcondition",
            "cape", "poncho", "coat", "trench", "manteau", "fauxfur",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "mask":
        blocked = (
            "bag", "clutch", "sac", "belt", "ceinture", "size36", "taille36",
            "verygoodcondition", "cape", "poncho", "coat", "trench",
            "fauxleather", "fauxfur",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "shelf":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "size36",
            "taille36", "verygoodcondition", "cape", "poncho", "coat",
            "fauxleather", "parisian",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "beanie":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "organizer":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "lamp":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "chandelier":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "carpet":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "cushion":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "mirror":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "sculpture":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "cape", "poncho", "fur", "fourrure",
            "dress", "robe", "size36", "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "curtain":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "necktie":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "leg_warmer":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "jewelry_box":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "forher", "pourelle",
            "size36", "size40", "taille36", "taille40", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "lace_umbrella":
        blocked = (
            "bag", "clutch", "handbag", "sac", "belt", "ceinture", "jacket",
            "veste", "coat", "manteau", "dress", "robe", "size36",
            "taille36", "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    elif listing_type == "belt":
        blocked = (
            "bag", "clutch", "handbag", "sac", "jacket", "veste", "coat",
            "manteau", "dress", "robe", "size36", "taille36",
            "verygoodcondition",
        )
        completed = [
            tag
            for tag in completed
            if not any(word in tag.casefold() for word in blocked)
        ]
    fallbacks = list(FALLBACK_HASHTAGS[listing_type][language])
    seed_parts = [
        str((product or {}).get("sku") or (product or {}).get("title") or ""),
        str((product or {}).get("colour") or ""),
        language,
        listing_type,
    ]
    if listing_type == "skirt":
        seed_parts.append(str((product or {}).get("_hashtag_nonce") or time.time_ns()))
    seed = "|".join(seed_parts)
    if seed and fallbacks:
        fallbacks = _rotate_sequence(fallbacks, seed)
    if listing_type == "skirt":
        specific = _listing_specific_hashtags(product, listing_type, language)
        extras = [tag for tag in [*completed, *fallbacks] if tag not in specific]
        extras = _rotate_sequence(extras, seed) if extras else extras
        completed = list(dict.fromkeys([*specific, *extras]))
        return completed[:25] if len(completed) >= 20 else completed
    if len(completed) >= 20:
        return completed[:25]
    for fallback in fallbacks:
        if fallback not in completed:
            completed.append(fallback)
        if len(completed) >= 20:
            break
    return completed[:25]


def _listing_specific_hashtags(
    product: dict[str, Any] | None,
    listing_type: str,
    language: str,
) -> list[str]:
    if not product:
        return []
    if listing_type == "skirt":
        tags: list[str] = []
        colour = _english_colour(str(product.get("colour") or "").strip())
        compact_colour = re.sub(r"[^a-z0-9]", "", colour.casefold())
        if compact_colour and compact_colour != "neutral":
            tags.append(
                f"#jupe{compact_colour}" if language == "french" else f"#{compact_colour}skirt"
            )
        source = _words_from_product(product)
        length = str(
            product.get("selected_skirt_length")
            or ("midi" if "midi" in source else "long" if "long" in source or "maxi" in source else "")
        )
        if length == "midi":
            tags.append("#jupemidi" if language == "french" else "#midiskirt")
        elif length in {"long", "maxi"}:
            tags.append("#jupelongue" if language == "french" else "#longskirt")
            if "maxi" in source:
                tags.append("#jupemaxi" if language == "french" else "#maxiskirt")
        for detail in _infer_details(product):
            token = re.sub(r"[^a-z0-9]", "", detail.casefold())
            if token:
                tags.append(
                    f"#jupe{token}" if language == "french" else f"#{token}skirt"
                )
        if language == "french":
            tags.extend(["#tailleS", "#jupefemme"])
        else:
            tags.extend(["#sizeS", "#womensskirt"])
        return list(dict.fromkeys(tags))
    if listing_type != "jeans":
        return []
    tags = []
    colour = str(product.get("colour") or "").strip()
    compact_colour = re.sub(r"[^a-z0-9]", "", colour.casefold())
    if compact_colour and compact_colour != "neutral":
        tags.append(
            f"#jean{compact_colour}" if language == "french" else f"#{compact_colour}jeans"
        )
    for detail in _infer_jeans_details(product):
        token = re.sub(r"[^a-z0-9]", "", detail.casefold())
        if token:
            tags.append(
                f"#jean{token}" if language == "french" else f"#{token}jeans"
            )
    if language == "french":
        tags.extend(["#tailleL", "#jeanhomme"])
    else:
        tags.extend(["#sizeL", "#mensjeans"])
    return list(dict.fromkeys(tags))


def listing_from_fast_draft(
    draft: FastListingDraft,
    listing_type: str,
) -> ListingPair:
    english_tags = _ensure_twenty_hashtags(
        _clean_draft_hashtags(draft.english_hashtags),
        listing_type,
        "english",
    )
    french_tags = _ensure_twenty_hashtags(
        _clean_draft_hashtags(draft.french_hashtags),
        listing_type,
        "french",
    )
    output_label = draft.fictional_brand
    if listing_type in {"skirt", "hat", "mask", "jeans", "bag", "coat", "jacket", "plant", "organizer", "mirror", "sculpture", "curtain", "leg_warmer", "jewelry_box", "lace_umbrella", "belt"}:
        style_source = " ".join(
            (
                draft.fictional_brand,
                draft.english_title,
                draft.french_title,
                draft.english_description,
                draft.french_description,
            )
        ).casefold()
        style_aliases = (
            ("old money", "oldmoney"),
            ("oldmoney", "oldmoney"),
            ("wild west", "wildwest"),
            ("wildwest", "wildwest"),
            ("bohemian", "bohemian"),
            ("boho", "boho"),
            ("chic", "chic"),
            ("elegant", "elegant"),
            ("y2k", "y2k"),
            ("western", "western"),
            ("cowboy", "cowboy"),
            ("cowgirl", "cowgirl"),
            ("gothic", "goth"),
            ("goth", "goth"),
            ("streetwear", "street"),
            ("street", "street"),
            ("grunge", "grunge"),
            ("dark academia", "dark"),
            ("dark", "dark"),
        )
        output_label = next(
            (
                normalized
                for keyword, normalized in style_aliases
                if keyword in style_source
            ),
            "western" if listing_type == "hat" else "street" if listing_type == "jeans" else "y2k" if listing_type in {"bag", "jacket", "mask", "organizer"} else "boho" if listing_type in {"plant", "mirror", "leg_warmer", "belt"} else "goth" if listing_type == "curtain" else "chic" if listing_type == "jewelry_box" else "elegant",
        )
        if listing_type in {"hat", "mask"}:
            if output_label == "street":
                output_label = "streetwear"
            if listing_type == "mask" and output_label == "goth":
                output_label = "y2k"
            allowed_hat_tokens = {
                "boho",
                "elegant",
                "western",
                "cowboy",
                "cowgirl",
                "y2k",
                "streetwear",
                "grunge",
                "wildwest",
                "sophisticated",
            }
            if output_label not in allowed_hat_tokens:
                output_label = "y2k" if listing_type == "mask" else "western"
    return ListingPair(
        fictional_brand=output_label,
        english=ListingVersion(
            title=draft.english_title,
            description=draft.english_description,
            hashtags=english_tags,
        ),
        french=ListingVersion(
            title=draft.french_title,
            description=draft.french_description,
            hashtags=french_tags,
        ),
    )


def _stable_choice(seed: str, choices: tuple[str, ...]) -> str:
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()
    return choices[int(digest[:8], 16) % len(choices)]


def _reserved_brand_set(reserved_brands: set[str] | list[str] | None) -> set[str]:
    return {
        str(name).strip().casefold()
        for name in (reserved_brands or [])
        if str(name).strip()
    }


def _style_token_from_label(value: str) -> str:
    aliases = {
        "old money": "oldmoney",
        "oldmoney": "oldmoney",
        "gothic": "goth",
        "goth": "goth",
        "streetwear": "street",
        "street": "street",
        "dark academia": "dark",
        "dark": "dark",
    }
    folded = value.strip().casefold()
    return aliases.get(folded, folded)


def _fallback_output_label(
    product: dict[str, Any],
    listing_type: str,
    reserved_brands: set[str] | list[str] | None = None,
) -> str:
    reserved = _reserved_brand_set(reserved_brands)
    seed = "|".join(
        str(value or "")
        for value in (
            listing_type,
            product.get("sku"),
            product.get("title"),
            product.get("main_image_url"),
            product.get("colour"),
            product.get("category"),
            ",".join(sorted(reserved)),
        )
    )
    if listing_type == "hat":
        return "western"
    if listing_type == "mask":
        return "y2k"
    if listing_type == "skirt":
        preferred = _style_token_from_label(
            _extract_title_style(_infer_styles(product, "skirt")) or ""
        )
        available = [token for token in STYLE_BRAND_TOKENS if token not in reserved]
        if preferred in available:
            return preferred
        if available:
            return _stable_choice(seed, tuple(available))
        invented = [
            brand for brand in INVENTED_BRANDS if brand.casefold() not in reserved
        ]
        return _stable_choice(seed, tuple(invented or INVENTED_BRANDS))
    if listing_type == "jeans":
        return "street"
    if listing_type == "bag":
        return "y2k"
    if listing_type == "plant":
        return "boho"
    if listing_type == "coat":
        return "elegant"
    if listing_type == "jacket":
        return "y2k"
    if listing_type == "organizer":
        return "y2k"
    if listing_type == "mirror":
        return "boho"
    if listing_type == "sculpture":
        return "elegant"
    if listing_type == "curtain":
        return "goth"
    if listing_type == "leg_warmer":
        return "boho"
    if listing_type == "jewelry_box":
        return "chic"
    if listing_type == "lace_umbrella":
        return "elegant"
    if listing_type == "belt":
        return "boho"
    available_brands = [
        brand for brand in INVENTED_BRANDS if brand.casefold() not in reserved
    ]
    return _stable_choice(seed, tuple(available_brands or INVENTED_BRANDS))


def _replace_generic_output_label(
    listing: ListingPair,
    product: dict[str, Any],
    listing_type: str,
    reserved_brands: set[str] | list[str] | None = None,
) -> ListingPair:
    reserved = _reserved_brand_set(reserved_brands)
    current = listing.fictional_brand.strip()
    if listing_type == "skirt":
        if (
            current.casefold() in reserved
            or current.casefold() in GENERIC_OR_REPEATED_BRANDS
            or not current
        ):
            listing.fictional_brand = _fallback_output_label(
                product,
                listing_type,
                reserved,
            )
        return listing
    if listing_type in {"hat", "mask", "jeans", "bag", "coat", "jacket", "plant", "organizer", "mirror", "sculpture", "curtain", "leg_warmer", "jewelry_box", "lace_umbrella", "belt"}:
        return listing
    if listing_type in {"shelf", "lamp", "carpet", "cushion", "necktie", "chandelier", "beanie"} and (
        current.casefold() in STYLE_BRAND_TOKENS
        or current.casefold() in {"vintage", "western", "cowboy", "cowgirl"}
        or not current
    ):
        listing.fictional_brand = _fallback_output_label(
            product,
            listing_type,
            reserved,
        )
        return listing
    if current.casefold() in GENERIC_OR_REPEATED_BRANDS or current.casefold() in reserved:
        listing.fictional_brand = _fallback_output_label(
            product,
            listing_type,
            reserved,
        )
    return listing


def facts_are_descriptive_enough(product: dict[str, Any]) -> bool:
    title = " ".join(str(product.get("title") or "").split())
    category = str(product.get("category") or "").strip()
    colour = str(product.get("colour") or "").strip()
    details = product.get("additional_details") or {}
    has_detail = any(details.get(key) for key in ("style", "pattern", "season"))
    return len(title) >= 45 and bool(category or colour or has_detail)


def _normalize_french_dress_title(value: str) -> str:
    """Keep the French category first: 'Robe longue', never 'Longue robe'."""
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    robe_index = next(
        (index for index, word in enumerate(words) if word.casefold() == "robe"),
        None,
    )
    if robe_index is None:
        return value
    has_longue = any(word.casefold() == "longue" for word in words)
    remaining = [
        word
        for index, word in enumerate(words)
        if index != robe_index and not (
            has_longue and word.casefold() == "longue"
        )
    ]
    prefix = ["Robe"] + (["longue"] if has_longue else [])
    return " ".join(prefix + remaining)


MATERIAL_WORDS_PATTERN = re.compile(
    r"\b(?:faux\s*leather|fauxleather|simili\s*cuir|similicuir|"
    r"polyester|cotton|coton|satin|silk|soie|linen|lin|wool|laine|"
    r"nylon|viscose|acrylic|acrylique|spandex|elastane|élasthanne|chiffon|"
    r"velvet|velours|denim|leather|cuir|suede|daim|tweed|knit|maille|"
    r"mesh|rayon)\b",
    flags=re.I,
)


def _strip_materials(value: str) -> str:
    return " ".join(MATERIAL_WORDS_PATTERN.sub(" ", value).split())


def _strip_belt_and_in_an(value: str) -> str:
    value = re.sub(r"\bwith an [SL]-size belt\b", "", value, flags=re.I)
    value = re.sub(r"\bavec une ceinture(?:\s+taille\s+[SL])?\b", "", value, flags=re.I)
    value = re.sub(
        rf"\bin an?\s+(?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*\s+style\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        rf"\bdans un style\s+(?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*",
        "",
        value,
        flags=re.I,
    )
    return " ".join(value.split())


def _normalize_english_skirt_title(
    value: str,
    preferred_length: str | None = None,
) -> str:
    value = _strip_belt_and_in_an(_strip_materials(_strip_size_noise(value)))
    words = value.split()
    length_tokens = {"long", "midi", "short", "mini", "maxi"}
    if preferred_length and not any(word.casefold() in length_tokens for word in words):
        prefix = "Long" if preferred_length == "long" else "Midi"
        value = f"{prefix} {value}"
    if not re.search(r"\bskirts?\b", value, flags=re.I):
        value = f"Skirt {value}".strip()
    return value


def _normalize_french_skirt_title(
    value: str,
    preferred_length: str | None = None,
) -> str:
    value = _strip_belt_and_in_an(_strip_materials(_strip_size_noise(value)))
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    skirt_index = next(
        (index for index, word in enumerate(words) if word.casefold() == "jupe"),
        None,
    )
    if skirt_index is None:
        return value
    length_words = ("longue", "midi", "courte", "mini")
    has_maxi = any(word.casefold() == "maxi" for word in words)
    length = (
        "midi" if preferred_length == "midi"
        else None if preferred_length == "long" and has_maxi
        else "longue" if preferred_length == "long"
        else next(
            (
                word.casefold()
                for word in words
                if word.casefold() in length_words
            ),
            None,
        )
    )
    remaining = [
        word
        for index, word in enumerate(words)
        if index != skirt_index and word.casefold() not in length_words
    ]
    return " ".join(["Jupe"] + ([length] if length else []) + remaining)


_STYLE_TAIL = re.compile(
    r"(?:,\s*|\s+)("
    r"in an?\s+\S+\s+style\b.*|"
    r"style\b.*|"
    r"[^\s,]+\s+style\b.*"
    r")$",
    flags=re.I,
)


def _trim_title_head(head: str, budget: int) -> str:
    trimmed = head[: max(12, budget)].rstrip(" ,;:-")
    if " " in trimmed and len(head) > budget:
        trimmed = trimmed.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return trimmed


_SKIRT_STYLE_TOKEN = (
    r"(?:old\s+money|dark\s+academia|streetwear|bohemian|elegant|élégant|"
    r"boho|gothic|grunge|y2k|sophisticated|chic|oldmoney|vintage)"
)


def _style_tokens_after_style_word(value: str) -> str:
    value = re.sub(
        rf"(?:,\s*|\s+)in an?\s+((?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*)\s+style\b",
        r", style \1",
        value,
        flags=re.I,
    )
    value = re.sub(
        rf",\s*((?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*)\s+style\b",
        r", style \1",
        value,
        flags=re.I,
    )
    value = re.sub(
        rf"\s+((?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*)\s+style\b",
        r", style \1",
        value,
        flags=re.I,
    )
    return " ".join(value.split())


def _ensure_literal_style(
    value: str,
    language: str,
    *,
    skirt_style: bool = False,
) -> str:
    if skirt_style:
        value = _style_tokens_after_style_word(value)
    if re.search(r"\bstyle\b", value, flags=re.I):
        return value
    insert = "style élégant" if language == "french" else (
        "style elegant" if skirt_style else "in an elegant style"
    )
    cleaned = value.rstrip(" ,;:-")
    return f"{cleaned}, {insert}" if cleaned else insert


def _ensure_letter_size(
    value: str,
    language: str,
    size: str,
    *,
    require_style: bool = False,
    skirt_style: bool = False,
) -> str:
    value = _strip_title_filler(
        value,
        translate_french_colours=language == "english",
    )
    value = _strip_materials(value)
    value = re.sub(r"\bwith an [SL]-size belt\b", "", value, flags=re.I)
    value = re.sub(r"\bavec une ceinture taille [SL]\b", "", value, flags=re.I)
    value = re.sub(r"\b(?:size|taille)\s*(?:36|38|40|42)\b", "", value, flags=re.I)
    value = re.sub(r"\b[SML][- ]size\b", "", value, flags=re.I)
    value = re.sub(r"\b(?:size|taille)\s*[SML]\b", "", value, flags=re.I)
    if require_style:
        value = _ensure_literal_style(
            value,
            language,
            skirt_style=skirt_style,
        )
    suffix = f"taille {size}" if language == "french" else f"size {size}"
    cleaned = " ".join(value.split()).rstrip(" ,;:-")
    style_match = _STYLE_TAIL.search(cleaned)
    if style_match:
        head = cleaned[: style_match.start()].rstrip(" ,;:-")
        style = " ".join(style_match.group(1).split())
        if not re.search(r"\bstyle\b", style, flags=re.I):
            style = f"style {style}"
        joiner = " " if style.casefold().startswith("in ") else ", "
        budget = 100 - len(suffix) - 1 - len(style) - len(joiner)
        head = _trim_title_head(head, budget)
        return f"{head}{joiner}{style} {suffix}"
    maximum_base_length = 100 - len(suffix) - 1
    return f"{_trim_title_head(cleaned, maximum_base_length)} {suffix}"


def _ensure_skirt_size_s(
    value: str,
    language: str,
    *,
    require_style: bool = False,
    skirt_style: bool = False,
) -> str:
    return _ensure_letter_size(
        value,
        language,
        "S",
        require_style=require_style,
        skirt_style=skirt_style,
    )


_SKIRT_TITLE_STYLE_ALIASES = (
    ("old money", "old money"),
    ("oldmoney", "old money"),
    ("dark academia", "dark academia"),
    ("streetwear", "streetwear"),
    ("bohemian", "bohemian"),
    ("sophisticated", "sophisticated"),
    ("élégant", "elegant"),
    ("elegant", "elegant"),
    ("gothic", "gothic"),
    ("grunge", "grunge"),
    ("boho", "boho"),
    ("chic", "chic"),
    ("y2k", "y2k"),
    ("goth", "gothic"),
    ("street", "streetwear"),
    ("dark", "dark"),
)


def _extract_title_style(value: str) -> str | None:
    folded = value.casefold()
    matches = [
        (folded.find(keyword), normalized)
        for keyword, normalized in _SKIRT_TITLE_STYLE_ALIASES
        if keyword in folded
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: (item[0], -len(item[1])))[1]


def _ensure_skirt_in_an_style_belt(
    value: str,
    language: str,
    product: dict[str, Any] | None = None,
    listing_type: str = "skirt",
) -> str:
    value = _strip_title_filler(
        value,
        translate_french_colours=language == "english",
    )
    value = _strip_materials(value)
    style = _extract_title_style(value)
    if not style and product:
        style = _extract_title_style(_infer_styles(product, listing_type))
    style = style or "elegant"
    value = re.sub(r"\bwith an [SL]-size belt\b", "", value, flags=re.I)
    value = re.sub(r"\bavec une ceinture taille [SL]\b", "", value, flags=re.I)
    value = re.sub(
        rf"\bin an?\s+(?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*\s+style\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        rf"\bdans un style\s+(?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        rf",?\s*style\s+(?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b(?:size|taille)\s*[SML]\b", "", value, flags=re.I)
    head = " ".join(value.split()).rstrip(" ,;:-")
    belt = "S"
    if language == "french":
        french_style = "élégant" if style == "elegant" else style
        suffix = f"dans un style {french_style} avec une ceinture taille {belt}"
    else:
        suffix = (
            f"in {_style_article(style)} {style} style with an {belt}-size belt"
        )
    head = _trim_title_head(head, 100 - len(suffix) - 1)
    return f"{head} {suffix}".strip()


def _ensure_style_title_no_size(
    value: str,
    language: str,
    *,
    require_style: bool = True,
    skirt_style: bool = True,
) -> str:
    value = _strip_title_filler(
        value,
        translate_french_colours=language == "english",
    )
    value = _strip_materials(value)
    value = re.sub(r"\b(?:size|taille)\s*(?:36|38|40|42)\b", "", value, flags=re.I)
    value = re.sub(r"\b[SML][- ]size\b", "", value, flags=re.I)
    value = re.sub(r"\b(?:size|taille)\s*[SML]\b", "", value, flags=re.I)
    value = re.sub(
        r"\b(?:one\s+size\s+fits\s+all|taille\s+unique)\b",
        "",
        value,
        flags=re.I,
    )
    if require_style:
        value = _ensure_literal_style(
            value,
            language,
            skirt_style=skirt_style,
        )
    cleaned = " ".join(value.split()).rstrip(" ,;:-")
    style_match = _STYLE_TAIL.search(cleaned)
    if style_match:
        head = cleaned[: style_match.start()].rstrip(" ,;:-")
        style = " ".join(style_match.group(1).split())
        if not re.search(r"\bstyle\b", style, flags=re.I):
            style = f"style {style}"
        joiner = " " if style.casefold().startswith("in ") else ", "
        budget = 100 - len(style) - len(joiner)
        head = _trim_title_head(head, budget)
        return f"{head}{joiner}{style}"
    return _trim_title_head(cleaned, 100)


SKIRT_DETAIL_FR = {
    "floral": "avec motifs fleuris",
    "ruffle": "à volants",
    "ruffled": "à volants",
    "pleated": "avec plis",
    "wrap": "portefeuille",
    "sequin": "à sequins",
    "sequins": "à sequins",
    "slit": "fendue avec fente",
    "split": "fendue avec fente",
    "buckle": "avec boucles",
    "asymmetric": "asymétrique",
    "printed": "imprimée",
    "tiered": "à étages",
    "draped": "drapée",
    "bow": "avec nœud",
    "belted": "avec boucles",
    "bodycon": "près du corps",
    "lace": "en dentelle",
    "color-block": "à panneaux color-block",
    "colour-block": "à panneaux color-block",
    "off-shoulder": "à épaules dénudées",
    "halter": "dos nu",
    "strapless": "sans bretelles",
    "sleeveless": "sans manches",
    "backless": "dos nu",
    "v-neck": "col V",
    "square-neck": "col carré",
}

SKIRT_DETAIL_EN = {
    "floral": "with floral details",
    "ruffle": "with ruffle details",
    "ruffled": "with ruffle details",
    "pleated": "with pleated details",
    "wrap": "with wrap details",
    "sequin": "with sequin details",
    "sequins": "with sequin details",
    "slit": "with split details",
    "split": "with split details",
    "buckle": "with buckle details",
    "asymmetric": "with asymmetric details",
    "printed": "with printed details",
    "tiered": "with tiered details",
    "draped": "with draped details",
    "bow": "with bow details",
    "belted": "with buckle details",
    "bodycon": "fitted",
    "lace": "with lace details",
    "color-block": "with color block panels",
    "colour-block": "with color block panels",
    "off-shoulder": "with off-shoulder details",
    "halter": "with halter details",
    "strapless": "with strapless details",
    "sleeveless": "with sleeveless details",
    "backless": "with backless details",
    "v-neck": "with a V-neck",
    "square-neck": "with a square neck",
}

JEANS_DETAIL_FR = {
    "baggy": "baggy",
    "cargo": "avec poches cargo",
    "ripped": "avec détails déchirés",
    "distressed": "avec détails déchirés",
    "straight": "droit",
    "bootcut": "bootcut",
    "tapered": "à jambes fuselées",
    "oversized": "coupe oversized",
    "flare": "évasé",
    "relaxed": "coupe relaxed",
}

JEANS_DETAIL_EN = {
    "baggy": "with baggy legs",
    "cargo": "with cargo pockets",
    "ripped": "with ripped details",
    "distressed": "with ripped details",
    "straight": "with a straight cut",
    "bootcut": "with bootcut hems",
    "tapered": "with tapered legs",
    "oversized": "with an oversized fit",
    "flare": "with flared hems",
    "relaxed": "with a relaxed fit",
}

# "wide-leg"/"wide" are deliberately excluded: baggy jeans are never listed
# with a wide-leg fit called out in the title.
JEANS_DETAIL_WORDS = (
    "baggy",
    "cargo",
    "ripped",
    "distressed",
    "straight",
    "bootcut",
    "tapered",
    "oversized",
    "flare",
    "relaxed",
)

BOOTS_DETAIL_FR = {
    "heeled": "à talons",
    "high-heeled": "à talons hauts",
    "sequin": "à sequins",
    "sequined": "à sequins",
    "pointed": "à bout pointu",
    "platform": "plateforme",
    "zipper": "avec zip",
    "buckle": "avec boucles",
    "knee-high": "hautes",
    "thigh-high": "cuissardes",
    "chunky": "à talons épais",
    "slouch": "souples",
}

BOOTS_DETAIL_EN = {
    "heeled": "with a high heel",
    "high-heeled": "with a high heel",
    "sequin": "with sequin details",
    "sequined": "with sequin details",
    "pointed": "with a pointed toe",
    "platform": "with a platform sole",
    "zipper": "with zipper details",
    "buckle": "with buckle details",
    "knee-high": "knee-high",
    "thigh-high": "thigh-high",
    "chunky": "with a chunky heel",
    "slouch": "with a slouch shaft",
}

BOOTS_DETAIL_WORDS = (
    "thigh-high",
    "knee-high",
    "high-heeled",
    "heeled",
    "sequined",
    "sequin",
    "pointed",
    "platform",
    "zipper",
    "buckle",
    "chunky",
    "slouch",
)

HEELS_DETAIL_FR = {
    "sequin": "avec sequins",
    "sequined": "avec sequins",
    "pointed": "à bout pointu",
    "platform": "plateforme",
    "stiletto": "à talons aiguilles",
    "chunky": "à talons épais",
    "strap": "avec bride",
    "ankle": "avec bride cheville",
    "slingback": "slingback",
    "peep": "bout ouvert",
    "buckle": "avec boucles",
    "bow": "avec nœud",
    "open-toe": "bout ouvert",
    "mule": "mules",
    "pump": "escarpins",
}

HEELS_DETAIL_EN = {
    "sequin": "with sequins",
    "sequined": "with sequins",
    "pointed": "with a pointed toe",
    "platform": "with a platform sole",
    "stiletto": "with stiletto heels",
    "chunky": "with a chunky heel",
    "strap": "with strap details",
    "ankle": "with an ankle strap",
    "slingback": "with a slingback",
    "peep": "with a peep toe",
    "buckle": "with buckle details",
    "bow": "with bow details",
    "open-toe": "with an open toe",
    "mule": "mules",
    "pump": "pumps",
}

HEELS_DETAIL_WORDS = (
    "sequined",
    "sequin",
    "pointed",
    "platform",
    "stiletto",
    "chunky",
    "slingback",
    "ankle",
    "strap",
    "peep",
    "buckle",
    "bow",
    "open-toe",
    "mule",
    "pump",
)


COAT_DETAIL_FR = {
    "trench": "trench",
    "belt": "avec ceinture",
    "belted": "avec ceinture",
    "double-breasted": "croisé",
    "oversized": "oversize",
    "wrap": "portefeuille",
    "collar": "avec col",
    "cape": "cape",
    "button": "avec boutons",
    "long": "long",
}

COAT_DETAIL_EN = {
    "trench": "trench",
    "belt": "with belt details",
    "belted": "with belt details",
    "double-breasted": "with double-breasted buttons",
    "oversized": "with an oversized cut",
    "wrap": "with a wrap front",
    "collar": "with collar details",
    "cape": "cape",
    "button": "with button details",
    "long": "long",
}

COAT_DETAIL_WORDS = (
    "double-breasted",
    "oversized",
    "trench",
    "belted",
    "belt",
    "wrap",
    "collar",
    "cape",
    "button",
    "long",
)

JACKET_DETAIL_FR = {
    "biker": "biker",
    "bomber": "bomber",
    "cropped": "cropped",
    "oversized": "oversize",
    "zipper": "avec zip",
    "zip": "avec zip",
    "collar": "avec col",
    "moto": "moto",
    "patterned": "à motif",
    "quilted": "matelassée",
}

JACKET_DETAIL_EN = {
    "biker": "biker",
    "bomber": "bomber",
    "cropped": "cropped",
    "oversized": "with an oversized cut",
    "zipper": "with zipper details",
    "zip": "with zipper details",
    "collar": "with collar details",
    "moto": "moto",
    "patterned": "patterned",
    "quilted": "quilted",
}

JACKET_DETAIL_WORDS = (
    "oversized",
    "patterned",
    "quilted",
    "cropped",
    "biker",
    "bomber",
    "moto",
    "zipper",
    "zip",
    "collar",
)

ORGANIZER_DETAIL_FR = {
    "tiered": "à niveaux",
    "tier": "à niveaux",
    "rotating": "rotatif",
    "stackable": "empilable",
    "wall": "mural",
    "mounted": "mural",
    "mesh": "avec paniers grillagés",
    "wire": "en fil métallique",
    "drawer": "à tiroirs",
    "hanging": "suspendu",
    "countertop": "de comptoir",
    "rack": "type support",
}

ORGANIZER_DETAIL_EN = {
    "tiered": "with tiered shelves",
    "tier": "with tiered shelves",
    "rotating": "with a rotating base",
    "stackable": "stackable",
    "wall": "wall-mounted",
    "mounted": "wall-mounted",
    "mesh": "with mesh baskets",
    "wire": "with a wire frame",
    "drawer": "with drawers",
    "hanging": "hanging",
    "countertop": "countertop",
    "rack": "rack style",
}

ORGANIZER_DETAIL_WORDS = (
    "tiered",
    "tier",
    "rotating",
    "stackable",
    "wall",
    "mounted",
    "mesh",
    "wire",
    "drawer",
    "hanging",
    "countertop",
    "rack",
)

MIRROR_DETAIL_FR = {
    "asymmetrical": "asymétrique",
    "organic": "de forme organique",
    "wall": "mural",
    "tabletop": "de table",
    "carved": "sculpté à la main",
    "sculptural": "au cadre sculptural",
    "arched": "cintré",
    "round": "rond",
    "oval": "ovale",
}

MIRROR_DETAIL_EN = {
    "asymmetrical": "with an asymmetrical shape",
    "organic": "with an organic silhouette",
    "wall": "wall-mounted",
    "tabletop": "tabletop",
    "carved": "hand-carved",
    "sculptural": "with a sculptural frame",
    "arched": "arched",
    "round": "round",
    "oval": "oval",
}

MIRROR_DETAIL_WORDS = (
    "asymmetrical",
    "organic",
    "wall",
    "tabletop",
    "carved",
    "sculptural",
    "arched",
    "round",
    "oval",
)

SCULPTURE_DETAIL_FR = {
    "delicate": "délicate",
    "carved": "aux détails sculptés",
    "abstract": "de forme abstraite",
    "modern": "à la silhouette moderne",
    "geometric": "de forme géométrique",
    "figurine": "façon figurine",
}

SCULPTURE_DETAIL_EN = {
    "delicate": "delicate",
    "carved": "with carved details",
    "abstract": "with an abstract shape",
    "modern": "with a modern silhouette",
    "geometric": "with a geometric shape",
    "figurine": "figurine-style",
}

SCULPTURE_DETAIL_WORDS = (
    "delicate",
    "carved",
    "abstract",
    "modern",
    "geometric",
    "figurine",
)

CURTAIN_DETAIL_FR = {
    "lace": "en dentelle",
    "sheer": "voile",
    "scalloped": "à bordure festonnée",
    "floral": "à motif floral",
    "blackout": "occultant",
    "grommet": "à œillets",
    "eyelet": "à œillets",
    "pleated": "plissé",
    "embroidered": "brodé",
    "ruffled": "à volants",
}

CURTAIN_DETAIL_EN = {
    "lace": "lace",
    "sheer": "sheer",
    "scalloped": "with a scalloped edge",
    "floral": "with a floral pattern",
    "blackout": "blackout",
    "grommet": "with grommet top",
    "eyelet": "with eyelet top",
    "pleated": "pleated",
    "embroidered": "embroidered",
    "ruffled": "ruffled",
}

CURTAIN_DETAIL_WORDS = (
    "lace",
    "sheer",
    "scalloped",
    "floral",
    "blackout",
    "grommet",
    "eyelet",
    "pleated",
    "embroidered",
    "ruffled",
)

NECKTIE_DETAIL_FR = {
    "paisley": "à motifs cachemire",
    "striped": "rayée",
    "polka-dot": "à pois",
    "polkadot": "à pois",
    "printed": "imprimée",
    "knitted": "en maille",
    "skinny": "fine",
    "slim": "à coupe fine",
    "embroidered": "brodée",
}

NECKTIE_DETAIL_EN = {
    "paisley": "paisley-patterned",
    "striped": "striped",
    "polka-dot": "polka-dot",
    "polkadot": "polka-dot",
    "printed": "printed",
    "knitted": "knitted",
    "skinny": "skinny",
    "slim": "with a slim cut",
    "embroidered": "embroidered",
}

NECKTIE_DETAIL_WORDS = (
    "paisley",
    "striped",
    "polka-dot",
    "polkadot",
    "printed",
    "knitted",
    "skinny",
    "slim",
    "embroidered",
)

LEG_WARMER_DETAIL_FR = {
    "fluffy": "moelleuses",
    "plush": "pelucheuses",
    "shaggy": "à poils longs",
    "chunky": "épaisses",
    "cropped": "courtes",
    "oversized": "oversize",
}

LEG_WARMER_DETAIL_EN = {
    "fluffy": "fluffy",
    "plush": "plush",
    "shaggy": "shaggy",
    "chunky": "chunky",
    "cropped": "with a cropped cut",
    "oversized": "oversized",
}

LEG_WARMER_DETAIL_WORDS = (
    "fluffy",
    "plush",
    "shaggy",
    "chunky",
    "cropped",
    "oversized",
)

JEWELRY_BOX_DETAIL_FR = {
    "textured": "texturé",
    "tiered": "à plusieurs niveaux",
    "multi-level": "à plusieurs niveaux",
    "multilevel": "à plusieurs niveaux",
    "mirrored": "avec miroir",
    "lockable": "verrouillable",
    "drawer": "à tiroir",
}

JEWELRY_BOX_DETAIL_EN = {
    "textured": "textured",
    "tiered": "with multiple storage tiers",
    "multi-level": "with multiple storage levels",
    "multilevel": "with multiple storage levels",
    "mirrored": "mirrored",
    "lockable": "with a lockable drawer",
    "drawer": "with a drawer",
}

JEWELRY_BOX_DETAIL_WORDS = (
    "textured",
    "tiered",
    "multi-level",
    "multilevel",
    "mirrored",
    "lockable",
    "drawer",
)

LACE_UMBRELLA_DETAIL_FR = {
    "lace": "en dentelle",
    "embroidered": "brodée",
    "ruffled": "à volants",
    "fringed": "à franges",
    "floral": "fleurie",
    "folding": "pliante",
}

LACE_UMBRELLA_DETAIL_EN = {
    "lace": "lace",
    "embroidered": "embroidered",
    "ruffled": "with a ruffled trim",
    "fringed": "fringed",
    "floral": "floral",
    "folding": "folding",
}

LACE_UMBRELLA_DETAIL_WORDS = (
    "lace",
    "embroidered",
    "ruffled",
    "fringed",
    "floral",
    "folding",
)

BELT_DETAIL_FR = {
    "carved": "sculptée",
    "buckle": "à boucle",
    "studded": "cloutée",
    "braided": "tressée",
    "woven": "tissée",
    "chain": "à chaîne",
}

BELT_DETAIL_EN = {
    "carved": "carved",
    "buckle": "with a statement buckle",
    "studded": "studded",
    "braided": "braided",
    "woven": "woven",
    "chain": "chain",
}

BELT_DETAIL_WORDS = (
    "carved",
    "buckle",
    "studded",
    "braided",
    "woven",
    "chain",
)

MASK_DETAIL_FR = {
    "sequin": "à paillettes",
    "sequins": "à paillettes",
    "masquerade": "de soirée",
    "lace": "en dentelle",
    "feather": "avec plumes",
    "cutout": "avec découpes",
    "party": "festif",
    "cat": "chat",
    "butterfly": "papillon",
}

MASK_DETAIL_EN = {
    "sequin": "sequin",
    "sequins": "sequin",
    "masquerade": "masquerade",
    "lace": "lace",
    "feather": "with feather details",
    "cutout": "with cutout details",
    "party": "party",
    "cat": "cat",
    "butterfly": "butterfly",
}

MASK_DETAIL_WORDS = (
    "masquerade",
    "sequins",
    "sequin",
    "butterfly",
    "cutout",
    "feather",
    "party",
    "lace",
    "cat",
)

SHELF_DETAIL_FR = {
    "wall": "murale",
    "floating": "flottante",
    "simple": "simples",
    "rustic": "rustique",
    "bracket": "avec supports",
    "storage": "de rangement",
    "set": "lot",
}

SHELF_DETAIL_EN = {
    "wall": "wall",
    "floating": "floating",
    "simple": "with a simple cut",
    "rustic": "rustic",
    "bracket": "with bracket details",
    "storage": "storage",
    "set": "set of 4",
}

SHELF_DETAIL_WORDS = (
    "floating",
    "wall",
    "simple",
    "rustic",
    "bracket",
    "storage",
    "set",
)

BEANIE_DETAIL_FR = {
    "cat-ear": "oreilles de chat",
    "pom-pom": "avec pompon",
    "cuffed": "à revers",
    "slouchy": "ample",
    "knit": "en tricot",
    "fuzzy": "en peluche",
    "plush": "en peluche",
    "striped": "rayé",
    "ribbed": "côtelé",
}

BEANIE_DETAIL_EN = {
    "cat-ear": "cat-ear",
    "pom-pom": "pom-pom",
    "cuffed": "cuffed",
    "slouchy": "slouchy",
    "knit": "knit",
    "fuzzy": "fuzzy",
    "plush": "plush",
    "striped": "striped",
    "ribbed": "ribbed",
}

BEANIE_DETAIL_WORDS = (
    "cat-ear",
    "pom-pom",
    "cuffed",
    "slouchy",
    "knit",
    "fuzzy",
    "plush",
    "striped",
    "ribbed",
)

LAMP_DETAIL_FR = {
    "twisted": "avec design torsadé",
    "ribbed": "avec détails côtelés",
    "gourd": "forme calebasse",
    "3d": "imprimée en 3D",
    "printed": "imprimée en 3D",
    "base": "avec base sculpturale",
    "shade": "avec abat-jour",
    "geometric": "avec forme géométrique",
}

LAMP_DETAIL_EN = {
    "twisted": "with a twisted design",
    "ribbed": "with ribbed detailing",
    "gourd": "gourd-shaped",
    "3d": "3D-printed",
    "printed": "3D-printed",
    "base": "with a sculptural base",
    "shade": "with a shade",
    "geometric": "with a geometric shape",
}

LAMP_DETAIL_WORDS = (
    "twisted",
    "ribbed",
    "gourd",
    "3d",
    "printed",
    "base",
    "shade",
    "geometric",
)

CHANDELIER_DETAIL_FR = {
    "multi-tiered": "à plusieurs niveaux",
    "tiered": "à niveaux",
    "crystal": "avec touches de cristal",
    "beaded": "à perles",
    "cage": "à structure cage",
    "drum": "avec abat-jour cylindrique",
    "candle": "de style bougie",
    "metal": "en métal",
}

CHANDELIER_DETAIL_EN = {
    "multi-tiered": "multi-tiered",
    "tiered": "tiered",
    "crystal": "with crystal accents",
    "beaded": "beaded",
    "cage": "with a cage frame",
    "drum": "with a drum shade",
    "candle": "candle-style",
    "metal": "metal",
}

CHANDELIER_DETAIL_WORDS = (
    "multi-tiered",
    "tiered",
    "crystal",
    "beaded",
    "cage",
    "drum",
    "candle",
    "metal",
)

CARPET_DETAIL_FR = {
    "shaggy": "à poils longs",
    "geometric": "à motif géométrique",
    "woven": "à texture tissée",
    "handmade": "tissé à la main",
    "washable": "lavable en machine",
    "round": "rond",
    "runner": "type tapis de couloir",
}

CARPET_DETAIL_EN = {
    "shaggy": "with a shaggy pile",
    "geometric": "with a geometric pattern",
    "woven": "with a woven texture",
    "handmade": "hand-woven",
    "washable": "machine-washable",
    "round": "round",
    "runner": "runner-style",
}

CARPET_DETAIL_WORDS = (
    "shaggy",
    "geometric",
    "woven",
    "handmade",
    "washable",
    "round",
    "runner",
)

CUSHION_DETAIL_FR = {
    "textured": "à texture travaillée",
    "embroidered": "brodé",
    "tufted": "capitonné",
    "piped": "à bordure passepoilée",
    "tasseled": "avec pompons",
    "tasselled": "avec pompons",
    "ruffled": "à volants",
    "quilted": "matelassé",
    "woven": "à texture tissée",
}

CUSHION_DETAIL_EN = {
    "textured": "with a textured weave",
    "embroidered": "embroidered",
    "tufted": "with a tufted finish",
    "piped": "with piped edges",
    "tasseled": "with tassel trim",
    "tasselled": "with tassel trim",
    "ruffled": "ruffled",
    "quilted": "quilted",
    "woven": "with a woven texture",
}

CUSHION_DETAIL_WORDS = (
    "textured",
    "embroidered",
    "tufted",
    "piped",
    "tasseled",
    "tasselled",
    "ruffled",
    "quilted",
    "woven",
)

BAG_DETAIL_FR = {
    "chain": "avec chaîne",
    "strap": "avec bandoulière",
    "buckle": "avec boucle",
    "zipper": "avec zip",
    "zip": "avec zip",
    "flap": "à rabat",
    "handle": "avec anse",
    "stud": "avec clous",
    "structured": "structuré",
    "mini": "mini",
    "hobo": "hobo",
}

BAG_DETAIL_EN = {
    "chain": "with chain strap",
    "strap": "with strap details",
    "buckle": "with buckle details",
    "zipper": "with zipper details",
    "zip": "with zipper details",
    "flap": "with flap details",
    "handle": "with handle details",
    "stud": "with stud details",
    "structured": "with a structured shape",
    "mini": "mini",
    "hobo": "hobo",
}

BAG_DETAIL_WORDS = (
    "structured",
    "chain",
    "zipper",
    "zip",
    "buckle",
    "flap",
    "handle",
    "stud",
    "mini",
    "hobo",
    "strap",
    "shoulder",
    "crossbody",
    "tote",
    "clutch",
)


_SKIRT_DRAPE_KEYS = (
    "tombe",
    "ourlet",
    "hem",
    "floor",
    "grazing",
    "mollet",
    "fluide",
    "fluid",
)
_SKIRT_DETAIL_KEYS = {
    *_SKIRT_DRAPE_KEYS,
    "marquee",
    "waist",
    "plissee",
    "pleated",
    "wrap",
    "portefeuille",
    "sequin",
    "fente",
    "slit",
    "volants",
    "ruffle",
    "ruffled",
    "fleurie",
    "floral",
    "asymetrique",
    "asymmetric",
    "imprimee",
    "printed",
    "drapee",
    "draped",
    "ceinture",
    "belt",
    "noeud",
    "bow",
    "etages",
    "tiered",
}


def _fold_ascii(value: str) -> str:
    return (
        unicodedata.normalize("NFD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .casefold()
    )


def _title_already_has(title: str, phrase: str) -> bool:
    folded = _fold_ascii(title)
    phrase_tokens = set(re.findall(r"[a-z]+", _fold_ascii(phrase)))
    keys = phrase_tokens & _SKIRT_DETAIL_KEYS
    if keys:
        return any(re.search(rf"\b{re.escape(token)}\b", folded) for token in keys)
    tokens = [token for token in phrase_tokens if len(token) > 3]
    return bool(tokens) and all(
        re.search(rf"\b{re.escape(token)}\b", folded) for token in tokens
    )


def _has_drape_or_length(title: str) -> bool:
    folded = _fold_ascii(title)
    return any(re.search(rf"\b{re.escape(token)}\b", folded) for token in _SKIRT_DRAPE_KEYS)


def _insert_before_style_or_size(title: str, phrase: str, language: str) -> str:
    match = _STYLE_TAIL.search(title)
    if match:
        head = title[: match.start()].rstrip(" ,")
        return f"{head} {phrase}{title[match.start():]}"
    size_marker = (
        r",?\s*taille\s+(?:S|M|L|38)\b"
        if language == "french"
        else r",?\s*size\s+(?:S|M|L|38)\b"
    )
    match = re.search(size_marker, title, flags=re.I)
    if match:
        head = title[: match.start()].rstrip(" ,")
        return f"{head} {phrase}{title[match.start():]}"
    return f"{title.rstrip(' ,')} {phrase}"


def _add_style_tokens(title: str, styles: list[str], language: str) -> str:
    folded = _fold_ascii(title)
    extra = [
        style
        for style in styles
        if _fold_ascii(style) not in folded
    ]
    if not extra:
        return title
    addition = " ".join(extra[:2])
    match = re.search(r"\bstyle\b([^,]*)", title, flags=re.I)
    if not match:
        return title
    insert_at = match.end()
    grown = f"{title[:insert_at].rstrip()} {addition}{title[insert_at:]}"
    return grown if len(grown) <= 100 else title


def _looks_like_chatgpt_skirt_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:avec|fendue|plissée|asymétrique|dentelle|étages|"
                r"panneaux|boucles|portefeuille|épaules|volants|col)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\bwith\b.+\b(?:details|panels|buckle|split|tiers?|lace|pleat|"
            r"ruffle|shoulder|neck)",
            title,
            flags=re.I,
        )
    )


def _looks_like_in_an_skirt_title(title: str, language: str) -> bool:
    if language == "french":
        return bool(
            re.search(r"\bdans un style\b", title, flags=re.I)
            and re.search(r"\bavec une ceinture taille S\b", title, flags=re.I)
            and re.search(r"\bjupe\b", title, flags=re.I)
        )
    return bool(
        re.search(r"\bin an?\s+\S+", title, flags=re.I)
        and re.search(r"\bstyle\b", title, flags=re.I)
        and re.search(r"\bwith an S-size belt\b", title, flags=re.I)
        and re.search(r"\bskirt\b", title, flags=re.I)
    )


def _looks_like_in_an_jeans_title(title: str, language: str) -> bool:
    has_detail = bool(
        re.search(
            r"\b(?:baggy|cargo|ripped|wide|straight|bootcut|relaxed|"
            r"oversized|tapered|flare|pocket|jambes|poches|droit|"
            r"déchir|large)\b",
            title,
            flags=re.I,
        )
    )
    if language == "french":
        return bool(
            re.search(r"\bdans un style\b", title, flags=re.I)
            and re.search(r"\bavec une ceinture taille L\b", title, flags=re.I)
            and re.search(r"\bjeans?\b", title, flags=re.I)
            and has_detail
        )
    return bool(
        re.search(r"\bin an?\s+\S+", title, flags=re.I)
        and re.search(r"\bstyle\b", title, flags=re.I)
        and re.search(r"\bwith an L-size belt\b", title, flags=re.I)
        and re.search(r"\bjeans?\b", title, flags=re.I)
        and has_detail
    )


def _looks_like_in_an_coat_title(title: str, language: str) -> bool:
    if language == "french":
        return bool(
            re.search(r"\bdans un style\b", title, flags=re.I)
            and re.search(r"\bavec une ceinture taille S\b", title, flags=re.I)
            and re.search(r"\b(?:manteau|trench|cape)\b", title, flags=re.I)
        )
    return bool(
        re.search(r"\bin an?\s+\S+", title, flags=re.I)
        and re.search(r"\bstyle\b", title, flags=re.I)
        and re.search(r"\bwith an S-size belt\b", title, flags=re.I)
        and re.search(r"\b(?:coats?|trench|cape)\b", title, flags=re.I)
    )


def _looks_like_chatgpt_jeans_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:jean|avec|jambes|poches|baggy|cargo|droit|large|"
                r"déchir|bootcut|relaxed|oversize)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\bjeans?\b.+\bwith\b.+\b(?:details|pockets|legs|hems|fit|cut|"
            r"baggy|cargo|wide|ripped|bootcut|straight)",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_boots_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:bottes?|cuissardes?|talons?|sequin|paillettes|"
                r"hautes?|zip|boucle|plateforme|pointu)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:boots?|knee[- ]high|thigh[- ]high|heeled|sequin|"
            r"pointed|platform|zipper|buckle)\b",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_heels_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:sandales?|escarpins?|talons?|sequin|paillettes|"
                r"bride|plateforme|pointu|nœud|noeud|slingback)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:heels?|sandals?|pumps?|mules?|stilettos?|sequin|"
            r"pointed|platform|strap|slingback|peep)\b",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_jacket_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:veste|blouson|biker|bomber|col|zip|motif|"
                r"cropped|oversize|moto|matelass)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:jackets?|biker|bomber|collar|zipper|"
            r"cropped|moto|quilted|patterned)\b",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_organizer_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:organiseur|organisateur|rangement|niveaux|"
                r"rotatif|empilable|mural|tiroirs|grillagé|comptoir)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:organizer|rack|holder|tiered|rotating|stackable|"
            r"wall[- ]mounted|drawers?|mesh|countertop)\b",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_mirror_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:miroir|asymétrique|organique|mural|sculpté|"
                r"cintré|rond|ovale)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:mirror|asymmetrical|organic|wall[- ]mounted|"
            r"tabletop|carved|sculptural|arched|round|oval)\b",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_sculpture_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:sculpture|statue|statuette|figurine|d[ée]licat|"
                r"sculpt[ée]|abstraite?|g[ée]om[ée]trique)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:sculpture|statue|statuette|figurine|delicate|carved|"
            r"abstract|geometric|modern)\b",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_curtain_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:rideau|dentelle|voile|festonn[ée]|floral|occultant|"
                r"[oœ]illets?|pliss[ée]|brod[ée]|volants?)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:curtains?|drapes?|lace|sheer|scalloped|floral|"
            r"blackout|grommet|eyelet|pleated|embroidered|ruffled)\b",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_necktie_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:cravate|cachemire|ray[ée]e|pois|imprim[ée]e|maille|"
                r"fine|brod[ée]e)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:neckties?|ties?|paisley|striped|polka|printed|knitted|"
            r"skinny|slim|embroidered)\b",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_coat_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
            r"\b(?:manteau|trench|cape|col|oversize|"
            r"portefeuille|croisé|boutons?)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:coats?|trench|cape|collar|oversized|wrap|"
            r"double[- ]breasted|button)\b",
            title,
            flags=re.I,
        )
    )


def _looks_like_chatgpt_bag_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:sac|bandouli[eè]re|cabas|croisé|chaîne|chaine|"
                r"boucle|rabat|zip|anse)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:bags?|handbags?|tote|clutch|crossbody|shoulder|"
            r"chain|strap|buckle|zipper|flap|handle)\b",
            title,
            flags=re.I,
        )
    )


def _title_colour_phrases(language: str) -> tuple[str, ...]:
    if language == "french":
        return tuple(
            sorted(
                {french for _, french in ENGLISH_TO_FRENCH_COLOURS} | {"tabac", "noire", "noir"},
                key=len,
                reverse=True,
            )
        )
    return tuple(
        sorted(
            set(COLOR_WORDS) | {"tobacco", "tabac", "cream white", "off white"},
            key=len,
            reverse=True,
        )
    )


def _ensure_skirt_colour_clause(
    title: str,
    product: dict[str, Any],
    language: str,
) -> str:
    title = _style_tokens_after_style_word(title)
    style_match = _STYLE_TAIL.search(title)
    if not style_match:
        return title
    head = title[: style_match.start()].rstrip(" ,;:-")
    tail = title[style_match.start():]
    if "," in head:
        return title
    inferred = _infer_colour(product)
    if language == "french" and inferred and inferred != "neutral":
        inferred = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == inferred),
            inferred,
        )
    colour = next(
        (
            phrase
            for phrase in _title_colour_phrases(language)
            if re.search(rf"\b{re.escape(phrase)}\b", head, flags=re.I)
        ),
        inferred if inferred and inferred != "neutral" else "",
    )
    if not colour:
        return title
    head_without = re.sub(rf"\b{re.escape(colour)}\b", "", head, flags=re.I)
    head_without = " ".join(head_without.split()).rstrip(" ,;:-")
    if not head_without:
        return title
    return f"{head_without}, {colour}{tail}"


def _skirt_expansion_phrases(
    product: dict[str, Any],
    language: str,
    skirt_length: str | None,
) -> list[str]:
    source = _words_from_product(product)
    phrases: list[str] = []
    mapping = SKIRT_DETAIL_FR if language == "french" else SKIRT_DETAIL_EN
    for detail in _infer_details(product):
        key = detail.replace(" ", "-")
        phrase = mapping.get(key) or mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    if "high waist" in source or "taille haute" in source:
        phrases.append("à taille haute" if language == "french" else "with a high waist")
    if "a-line" in source or "evase" in source or "évas" in source:
        phrases.append("évasée" if language == "french" else "A-line")
    if "color block" in source or "colour block" in source or "color-block" in source:
        phrases.append(
            "à panneaux color-block"
            if language == "french"
            else "with color block panels"
        )
    _ = skirt_length
    return list(dict.fromkeys(phrases))


def _infer_jeans_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in JEANS_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _jeans_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = JEANS_DETAIL_FR if language == "french" else JEANS_DETAIL_EN
    for detail in _infer_jeans_details(product):
        key = detail.replace(" ", "-")
        phrase = mapping.get(key) or mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_jeans_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(r"\bwith an [SL]-size belt\b", "", value, flags=re.I)
    value = re.sub(r"\bavec une ceinture(?:\s+taille\s+[SL])?\b", "", value, flags=re.I)
    value = re.sub(
        rf"\bin an?\s+(?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*\s+style\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\b(?:trench\s+)?coats?\b|\bcape\b|\bdresses?\b|\bskirts?\b",
        "",
        value,
        flags=re.I,
    )
    # Never advertise a wide-leg fit or call the item out as "men's" —
    # these jeans are always listed as baggy, full stop.
    value = re.sub(r"\bwith\s+wide\s+legs\b", "", value, flags=re.I)
    value = re.sub(r"\bwide[- ]legs?\b", "", value, flags=re.I)
    value = re.sub(r"\bmen'?s\b|\bfor\s+men\b|\bmen\b", "", value, flags=re.I)
    value = " ".join(value.split())
    if not re.search(r"\bjeans?\b", value, flags=re.I):
        value = f"Baggy jeans {value}".strip()
    elif not re.search(r"\bbaggy\b", value, flags=re.I):
        value = re.sub(
            r"\bjeans?\b",
            lambda match: f"Baggy {match.group(0)}",
            value,
            count=1,
            flags=re.I,
        )
    return " ".join(value.split())


def _normalize_french_jeans_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = re.sub(r"\bwith an [SL]-size belt\b", "", value, flags=re.I)
    value = re.sub(r"\bavec une ceinture(?:\s+taille\s+[SL])?\b", "", value, flags=re.I)
    value = re.sub(
        rf"\bdans un style\s+(?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\b(?:trench|manteaux?|cape|robes?|jupes?)\b",
        "",
        value,
        flags=re.I,
    )
    # Never advertise a wide-leg fit or call the item out as "men's" —
    # these jeans are always listed as baggy, full stop.
    value = re.sub(r"\b[aà]\s+jambes\s+larges\b|\bjambes\s+larges\b", "", value, flags=re.I)
    value = re.sub(r"\bpour\s+homme\b|\bhommes?\b", "", value, flags=re.I)
    value = " ".join(value.split())
    value = _translate_french_title_colours(value)
    words = value.split()
    jean_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"jean", "jeans"}
        ),
        None,
    )
    if jean_index is None:
        remaining = words
    else:
        remaining = [word for index, word in enumerate(words) if index != jean_index]
    if remaining and remaining[0].casefold() == "baggy":
        remaining = remaining[1:]
    return " ".join(["Jean", "baggy", *remaining])


def _infer_boots_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in BOOTS_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _boots_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = BOOTS_DETAIL_FR if language == "french" else BOOTS_DETAIL_EN
    for detail in _infer_boots_details(product):
        key = detail.replace(" ", "-")
        phrase = mapping.get(key) or mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_boots_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    if not re.search(r"\bboots?\b", value, flags=re.I):
        value = f"Knee-high boots {value}".strip()
    return value


def _normalize_french_boots_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    boot_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"botte", "bottes", "cuissarde", "cuissardes"}
        ),
        None,
    )
    if boot_index is None:
        return f"Bottes hautes {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != boot_index]
    lead = "Cuissardes" if words[boot_index].casefold().startswith("cuissarde") else "Bottes"
    return " ".join([lead, *remaining])


def _infer_heels_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in HEELS_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _heels_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = HEELS_DETAIL_FR if language == "french" else HEELS_DETAIL_EN
    for detail in _infer_heels_details(product):
        key = detail.replace(" ", "-")
        phrase = mapping.get(key) or mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_heels_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    if not re.search(r"\b(?:heels?|sandals?|pumps?|mules?|stilettos?)\b", value, flags=re.I):
        value = f"Heeled sandals {value}".strip()
    return value


def _normalize_french_heels_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    heel_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {
                "sandale",
                "sandales",
                "escarpin",
                "escarpins",
                "talon",
                "talons",
                "mule",
                "mules",
            }
        ),
        None,
    )
    if heel_index is None:
        return f"Sandales à talons {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != heel_index]
    lead = "Escarpins" if words[heel_index].casefold().startswith("escarpin") else "Sandales"
    return " ".join([lead, *remaining])


def _infer_coat_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in COAT_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _coat_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = COAT_DETAIL_FR if language == "french" else COAT_DETAIL_EN
    for detail in _infer_coat_details(product):
        if detail in {"belt", "belted"}:
            continue
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _strip_coat_belt_and_in_an(value: str) -> str:
    value = re.sub(r"\bwith an [SL]-size belt\b", "", value, flags=re.I)
    value = re.sub(r"\bavec une ceinture(?:\s+taille\s+[SL])?\b", "", value, flags=re.I)
    value = re.sub(r"\bwith belt details\b", "", value, flags=re.I)
    value = re.sub(r"\bavec ceinture\b", "", value, flags=re.I)
    value = re.sub(r"\bbelted\b", "", value, flags=re.I)
    value = re.sub(
        rf"\bin an?\s+(?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*\s+style\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        rf"\bdans un style\s+(?:{_SKIRT_STYLE_TOKEN})(?:\s+(?:{_SKIRT_STYLE_TOKEN}))*",
        "",
        value,
        flags=re.I,
    )
    return " ".join(value.split())


def _normalize_english_coat_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(_strip_materials(_strip_size_noise(value)))
    if not re.search(r"\b(?:coats?|trench|cape)\b", value, flags=re.I):
        value = f"Long coat {value}".strip()
    return value


def _infer_jacket_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in JACKET_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _jacket_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = JACKET_DETAIL_FR if language == "french" else JACKET_DETAIL_EN
    for detail in _infer_jacket_details(product):
        if detail in {"biker", "bomber", "moto"}:
            continue
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    defaults = (
        ["biker", "cropped", "oversize", "avec col"]
        if language == "french"
        else ["cropped", "with an oversized cut", "with collar details"]
    )
    for phrase in defaults:
        if phrase not in phrases:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_jacket_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(_strip_materials(_strip_size_noise(value)))
    value = re.sub(
        r"\b(?:trench\s+)?coats?\b|\bcape\b|\bponcho\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\bjackets?\b", value, flags=re.I):
        if re.search(r"\bbiker\b", value, flags=re.I):
            value = re.sub(r"\bbiker\b", "biker jacket", value, count=1, flags=re.I)
        elif re.search(r"\bbomber\b", value, flags=re.I):
            value = re.sub(r"\bbomber\b", "bomber jacket", value, count=1, flags=re.I)
        else:
            value = f"Women's jacket {value}".strip()
    return value


def _normalize_french_jacket_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(_strip_materials(_strip_size_noise(value)))
    value = re.sub(
        r"\b(?:manteau|trench|cape|poncho)\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    jacket_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"veste", "blouson", "biker", "bomber"}
        ),
        None,
    )
    if jacket_index is None:
        return f"Veste {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != jacket_index]
    lead = "Blouson" if words[jacket_index].casefold() == "blouson" else "Veste"
    return " ".join([lead, *remaining])


def _infer_organizer_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in ORGANIZER_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _organizer_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = ORGANIZER_DETAIL_FR if language == "french" else ORGANIZER_DETAIL_EN
    for detail in _infer_organizer_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    defaults = (
        ["à niveaux", "avec paniers grillagés"]
        if language == "french"
        else ["with tiered shelves", "with mesh baskets"]
    )
    for phrase in defaults:
        if phrase not in phrases:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_organizer_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:metal|plastic|wooden|wood|wire|steel|iron)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\borganizers?\b", value, flags=re.I):
        if re.search(r"\brack\b", value, flags=re.I):
            value = re.sub(r"\brack\b", "rack organizer", value, count=1, flags=re.I)
        elif re.search(r"\bholder\b", value, flags=re.I):
            value = re.sub(r"\bholder\b", "holder organizer", value, count=1, flags=re.I)
        else:
            value = f"Kitchen organizer {value}".strip()
    return value


def _normalize_french_organizer_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:m[ée]tallique|m[ée]tal|plastique|bois|acier|fer)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    organizer_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"organiseur", "organisateur", "rangement"}
        ),
        None,
    )
    if organizer_index is None:
        return f"Organiseur {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != organizer_index]
    return " ".join(["Organiseur", *remaining])


def _infer_mirror_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in MIRROR_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _mirror_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = MIRROR_DETAIL_FR if language == "french" else MIRROR_DETAIL_EN
    for detail in _infer_mirror_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    defaults = (
        ["de forme organique", "asymétrique"]
        if language == "french"
        else ["with an organic silhouette", "with an asymmetrical shape"]
    )
    for phrase in defaults:
        if phrase not in phrases:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_mirror_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:wood|wooden|walnut|resin|metal|glass|rattan)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\bmirrors?\b", value, flags=re.I):
        value = f"Wall mirror {value}".strip()
    return value


def _normalize_french_mirror_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:bois|noyer|r[ée]sine|m[ée]tal|verre|rotin)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    mirror_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"miroir", "miroirs"}
        ),
        None,
    )
    if mirror_index is None:
        return f"Miroir {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != mirror_index]
    return " ".join(["Miroir", *remaining])


def _infer_sculpture_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in SCULPTURE_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _sculpture_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = SCULPTURE_DETAIL_FR if language == "french" else SCULPTURE_DETAIL_EN
    for detail in _infer_sculpture_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    defaults = (
        ["délicate", "aux détails sculptés"]
        if language == "french"
        else ["delicate", "with carved details"]
    )
    for phrase in defaults:
        if phrase not in phrases:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_sculpture_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:resin|ceramic|wood|wooden|metal|plaster|stone)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\b(?:sculptures?|statues?|statuettes?|figurines?)\b", value, flags=re.I):
        value = f"Sculpture {value}".strip()
    return value


def _normalize_french_sculpture_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:r[ée]sine|c[ée]ramique|bois|m[ée]tal|pl[âa]tre|pierre)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    sculpture_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"sculpture", "sculptures", "statue", "statuette", "figurine"}
        ),
        None,
    )
    if sculpture_index is None:
        return f"Sculpture {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != sculpture_index]
    lead = words[sculpture_index]
    return " ".join([lead, *remaining])


def _infer_curtain_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in CURTAIN_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _curtain_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = CURTAIN_DETAIL_FR if language == "french" else CURTAIN_DETAIL_EN
    for detail in _infer_curtain_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    defaults = (
        ["en dentelle", "voile"]
        if language == "french"
        else ["lace", "sheer"]
    )
    for phrase in defaults:
        if phrase not in phrases:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_curtain_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:linen|cotton|polyester|velvet|silk|voile)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\b(?:curtains?|drapes?)\b", value, flags=re.I):
        value = f"Curtain {value}".strip()
    return value


def _normalize_french_curtain_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:lin|coton|polyester|velours|soie)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    curtain_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"rideau", "rideaux", "panneau"}
        ),
        None,
    )
    if curtain_index is None:
        return f"Rideau {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != curtain_index]
    return " ".join(["Rideau", *remaining])


def _infer_necktie_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in NECKTIE_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _necktie_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = NECKTIE_DETAIL_FR if language == "french" else NECKTIE_DETAIL_EN
    for detail in _infer_necktie_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_necktie_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:one\s+size\s+fits\s+all|taille\s+unique)\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\b(?:silk|polyester|cotton|wool|nylon)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\b(?:neckties?|ties?)\b", value, flags=re.I):
        value = f"Necktie {value}".strip()
    return " ".join(value.split())


def _normalize_french_necktie_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:one\s+size\s+fits\s+all|taille\s+unique)\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\b(?:soie|polyester|coton|laine|nylon)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    necktie_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"cravate", "cravates"}
        ),
        None,
    )
    if necktie_index is None:
        return f"Cravate {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != necktie_index]
    return " ".join(["Cravate", *remaining])


def _looks_like_chatgpt_leg_warmer_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:jambi[eè]res?|moelleuses?|pelucheuses?|poils?\s+longs?|"
                r"[ée]paisses?|courtes?|oversize)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:leg\s*warmers?|fluffy|plush|shaggy|chunky|cropped|oversized)\b",
            title,
            flags=re.I,
        )
    )


def _infer_leg_warmer_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in LEG_WARMER_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _leg_warmer_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = LEG_WARMER_DETAIL_FR if language == "french" else LEG_WARMER_DETAIL_EN
    for detail in _infer_leg_warmer_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    defaults = (
        ["moelleuses", "pelucheuses"]
        if language == "french"
        else ["fluffy", "plush"]
    )
    for phrase in defaults:
        if phrase not in phrases:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_leg_warmer_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:faux\s*fur|fur|polyester|acrylic|wool)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\bleg\s*warmers?\b", value, flags=re.I):
        value = f"Leg warmers {value}".strip()
    return value


def _normalize_french_leg_warmer_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:fausse\s+fourrure|fourrure|polyester|acrylique|laine)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    leg_warmer_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"jambière", "jambières"}
        ),
        None,
    )
    if leg_warmer_index is None:
        return f"Jambières {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != leg_warmer_index]
    return " ".join(["Jambières", *remaining])


def _looks_like_chatgpt_jewelry_box_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:coffret|bijoux|niveaux|miroir|verrouillable|tiroir|"
                r"textur[ée])\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:jewelry\s*box|tiered|multi[- ]level|mirrored|lockable|"
            r"drawer|textured)\b",
            title,
            flags=re.I,
        )
    )


def _infer_jewelry_box_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in JEWELRY_BOX_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _jewelry_box_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = JEWELRY_BOX_DETAIL_FR if language == "french" else JEWELRY_BOX_DETAIL_EN
    for detail in _infer_jewelry_box_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    defaults = (
        ["à plusieurs niveaux", "texturé"]
        if language == "french"
        else ["with multiple storage tiers", "textured"]
    )
    for phrase in defaults:
        if phrase not in phrases:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_jewelry_box_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:leather|faux\s*leather|velvet|suede)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\bjewelry\s*box(?:es)?\b", value, flags=re.I):
        value = f"Jewelry box {value}".strip()
    return value


def _normalize_french_jewelry_box_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:cuir|simili\s*cuir|velours|daim)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    value = re.sub(
        r"\b(?:coffret|bo[iî]te)\s+[àa]\s+bijoux\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b(?:coffret|bo[iî]te)\b", "", value, flags=re.I)
    value = " ".join(value.split()).strip(" ,")
    return f"Coffret à bijoux {value}".strip()


def _looks_like_chatgpt_lace_umbrella_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:ombrelle|dentelle|brod[ée]e?|volants?|franges?|"
                r"fleurie|pliante)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:parasol|lace|embroidered|ruffled|fringed|floral|"
            r"folding)\b",
            title,
            flags=re.I,
        )
    )


def _infer_lace_umbrella_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in LACE_UMBRELLA_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _lace_umbrella_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = LACE_UMBRELLA_DETAIL_FR if language == "french" else LACE_UMBRELLA_DETAIL_EN
    for detail in _infer_lace_umbrella_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    defaults = (
        ["en dentelle", "brodée"]
        if language == "french"
        else ["lace", "embroidered"]
    )
    for phrase in defaults:
        if phrase not in phrases:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_lace_umbrella_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:satin|polyester|cotton|nylon)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\b(?:parasols?|umbrellas?)\b", value, flags=re.I):
        value = f"Lace parasol {value}".strip()
    return value


def _normalize_french_lace_umbrella_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:satin|polyester|coton|nylon)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    umbrella_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"ombrelle", "ombrelles"}
        ),
        None,
    )
    if umbrella_index is None:
        return f"Ombrelle {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != umbrella_index]
    return " ".join(["Ombrelle", *remaining])


def _looks_like_chatgpt_belt_title(title: str, language: str) -> bool:
    has_style = bool(re.search(r",\s*style\s+\S+", title, flags=re.I))
    if title.count(",") < 2 or not has_style:
        return False
    if language == "french":
        return bool(
            re.search(
                r"\b(?:ceintures?|boucle|sculpt[ée]e?|clout[ée]e?|"
                r"tress[ée]e?|tiss[ée]e?|cha[îi]ne)\b",
                title,
                flags=re.I,
            )
        )
    return bool(
        re.search(
            r"\b(?:belts?|buckle|carved|studded|braided|woven|chain)\b",
            title,
            flags=re.I,
        )
    )


def _infer_belt_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in BELT_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _belt_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = BELT_DETAIL_FR if language == "french" else BELT_DETAIL_EN
    for detail in _infer_belt_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    defaults = (
        ["sculptée", "à boucle"]
        if language == "french"
        else ["carved", "with a statement buckle"]
    )
    for phrase in defaults:
        if phrase not in phrases:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_belt_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\bmetal\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    if not re.search(r"\bbelts?\b", value, flags=re.I):
        value = f"Belt {value}".strip()
    return value


def _normalize_french_belt_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\bm[ée]tal(?:lique)?\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    belt_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"ceinture", "ceintures"}
        ),
        None,
    )
    if belt_index is None:
        return f"Ceinture {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != belt_index]
    return " ".join(["Ceinture", *remaining])


def _normalize_french_coat_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(_strip_materials(_strip_size_noise(value)))
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    coat_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"manteau", "trench", "cape"}
        ),
        None,
    )
    if coat_index is None:
        return f"Manteau {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != coat_index]
    lead = "Trench" if words[coat_index].casefold() == "trench" else (
        "Cape" if words[coat_index].casefold() == "cape" else "Manteau"
    )
    return " ".join([lead, *remaining])


def _infer_bag_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in BAG_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _bag_expansion_phrases(product: dict[str, Any], language: str) -> list[str]:
    phrases: list[str] = []
    mapping = BAG_DETAIL_FR if language == "french" else BAG_DETAIL_EN
    for detail in _infer_bag_details(product):
        phrase = mapping.get(detail)
        if phrase:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _normalize_english_bag_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:one\s+size\s+fits\s+all|taille\s+unique)\b",
        "",
        value,
        flags=re.I,
    )
    if not re.search(
        r"\b(?:bags?|handbags?|totes?|clutch(?:es)?|crossbody)\b",
        value,
        flags=re.I,
    ):
        value = f"Women's handbag {value}".strip()
    return " ".join(value.split())


def _normalize_french_bag_title(value: str) -> str:
    value = _strip_materials(_strip_size_noise(value))
    value = re.sub(
        r"\b(?:one\s+size\s+fits\s+all|taille\s+unique)\b",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    words = value.split()
    bag_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"sac", "cabas"}
        ),
        None,
    )
    if bag_index is None:
        return f"Sac à main {value}".strip()
    remaining = [word for index, word in enumerate(words) if index != bag_index]
    lead = "Cabas" if words[bag_index].casefold() == "cabas" else "Sac"
    return " ".join([lead, *remaining])


def _listing_title_size(listing_type: str) -> str:
    if listing_type == "dress_m":
        return "M"
    if listing_type == "jeans":
        return "L"
    if listing_type == "long_boots":
        return "38"
    if listing_type == "heels":
        return "38"
    if listing_type == "bag":
        return ""
    if listing_type == "plant":
        return ""
    if listing_type == "mask":
        return ""
    if listing_type == "shelf":
        return ""
    if listing_type == "lamp":
        return ""
    if listing_type == "carpet":
        return ""
    if listing_type == "cushion":
        return ""
    if listing_type == "necktie":
        return ""
    if listing_type == "jewelry_box":
        return "L"
    return "S"


def _fit_skirt_title(
    title: str,
    language: str,
    size: str = "S",
    listing_type: str = "skirt",
) -> str:
    if not size:
        return _ensure_style_title_no_size(
            title,
            language,
            require_style=True,
            skirt_style=True,
        )
    return _ensure_letter_size(
        title,
        language,
        size,
        require_style=True,
        skirt_style=True,
    )


def _expand_skirt_title(
    title: str,
    product: dict[str, Any],
    language: str,
    skirt_length: str | None,
    listing_type: str = "skirt",
) -> str:
    size = _listing_title_size(listing_type)
    looks_ready = (
        _looks_like_chatgpt_jeans_title
        if listing_type == "jeans"
        else _looks_like_chatgpt_boots_title
        if listing_type == "long_boots"
        else _looks_like_chatgpt_heels_title
        if listing_type == "heels"
        else _looks_like_chatgpt_coat_title
        if listing_type == "coat"
        else _looks_like_chatgpt_jacket_title
        if listing_type == "jacket"
        else _looks_like_chatgpt_bag_title
        if listing_type == "bag"
        else _looks_like_chatgpt_organizer_title
        if listing_type == "organizer"
        else _looks_like_chatgpt_mirror_title
        if listing_type == "mirror"
        else _looks_like_chatgpt_sculpture_title
        if listing_type == "sculpture"
        else _looks_like_chatgpt_curtain_title
        if listing_type == "curtain"
        else _looks_like_chatgpt_necktie_title
        if listing_type == "necktie"
        else _looks_like_chatgpt_leg_warmer_title
        if listing_type == "leg_warmer"
        else _looks_like_chatgpt_jewelry_box_title
        if listing_type == "jewelry_box"
        else _looks_like_chatgpt_lace_umbrella_title
        if listing_type == "lace_umbrella"
        else _looks_like_chatgpt_belt_title
        if listing_type == "belt"
        else _looks_like_chatgpt_skirt_title
    )
    expansion_phrases = (
        _jeans_expansion_phrases(product, language)
        if listing_type == "jeans"
        else _boots_expansion_phrases(product, language)
        if listing_type == "long_boots"
        else _heels_expansion_phrases(product, language)
        if listing_type == "heels"
        else _coat_expansion_phrases(product, language)
        if listing_type == "coat"
        else _jacket_expansion_phrases(product, language)
        if listing_type == "jacket"
        else _bag_expansion_phrases(product, language)
        if listing_type == "bag"
        else _organizer_expansion_phrases(product, language)
        if listing_type == "organizer"
        else _mirror_expansion_phrases(product, language)
        if listing_type == "mirror"
        else _sculpture_expansion_phrases(product, language)
        if listing_type == "sculpture"
        else _curtain_expansion_phrases(product, language)
        if listing_type == "curtain"
        else _necktie_expansion_phrases(product, language)
        if listing_type == "necktie"
        else _leg_warmer_expansion_phrases(product, language)
        if listing_type == "leg_warmer"
        else _jewelry_box_expansion_phrases(product, language)
        if listing_type == "jewelry_box"
        else _lace_umbrella_expansion_phrases(product, language)
        if listing_type == "lace_umbrella"
        else _belt_expansion_phrases(product, language)
        if listing_type == "belt"
        else _skirt_expansion_phrases(product, language, skirt_length)
    )
    title = _ensure_skirt_colour_clause(" ".join(title.split()), product, language)
    if looks_ready(title, language) and len(title) >= 65:
        return _fit_skirt_title(title, language, size, listing_type)
    styles = _infer_styles(product, listing_type).split()
    grown = _add_style_tokens(title, styles, language)
    if len(grown) <= 100:
        title = grown
    for phrase in expansion_phrases:
        if looks_ready(title, language) and len(title) >= 65:
            break
        if _title_already_has(title, phrase):
            continue
        candidate = _insert_before_style_or_size(title, phrase, language)
        if len(candidate) <= 100 and len(candidate) > len(title):
            title = candidate
    return _fit_skirt_title(title, language, size, listing_type)


def _ensure_one_size(value: str, language: str, *, fits_all: bool = True) -> str:
    value = _strip_size_noise(value)
    value = re.sub(
        r"\b(?:one\s+size\s+fits\s+all|one\s+size|taille\s+unique)\b",
        "",
        value,
        flags=re.I,
    )
    if language == "french":
        suffix = "taille unique"
    elif fits_all:
        suffix = "one size fits all"
    else:
        suffix = "one size"
    cleaned = " ".join(value.split()).rstrip(" ,;:-")
    separator = ", " if not fits_all else " "
    maximum_base_length = 100 - len(suffix) - len(separator)
    return clip_listing_title(
        f"{cleaned[:maximum_base_length].rstrip(' ,;:-')}{separator}{suffix}"
    )


def _infer_mask_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in MASK_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _strip_mask_costume_phrasing(value: str) -> str:
    value = re.sub(r"\bhalloween\s+costume\s+style\b", "", value, flags=re.I)
    value = re.sub(r"\bcostume\s+style\b", "", value, flags=re.I)
    value = re.sub(r"\bstyle\s+costume(?:\s+halloween)?\b", "", value, flags=re.I)
    value = re.sub(r"\bstyle\s+halloween\b", "", value, flags=re.I)
    value = re.sub(
        r"\b(?:bags?|handbags?|clutches?|sacs?)\b",
        "",
        value,
        flags=re.I,
    )
    return " ".join(value.split())


def _normalize_english_mask_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_mask_costume_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = _style_tokens_after_style_word(value)
    if not re.search(r"\bmasks?\b", value, flags=re.I):
        value = f"Sequin masquerade mask {value}".strip()
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style y2k"
    return value


def _normalize_french_mask_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_mask_costume_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    value = _style_tokens_after_style_word(value)
    words = value.split()
    mask_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"masque", "mask"}
        ),
        None,
    )
    if mask_index is None:
        value = f"Masque {value}".strip()
    else:
        remaining = [word for index, word in enumerate(words) if index != mask_index]
        value = " ".join(["Masque", *remaining])
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style y2k"
    return value


def _pad_mask_title(title: str, language: str) -> str:
    extras = (
        ["de soirée", "à paillettes", "avec découpes"]
        if language == "french"
        else ["sequin", "masquerade", "with cutout details"]
    )
    title = _ensure_one_size(title, language)
    for phrase in extras:
        if len(title) >= 65:
            break
        if _title_already_has(title, phrase):
            continue
        candidate = _insert_before_style_or_size(title, phrase, language)
        if len(candidate) <= 100 and len(candidate) > len(title):
            title = _ensure_one_size(candidate, language)
    return title


def _infer_shelf_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in SHELF_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    if re.search(r"\b(?:set of 4|lot de 4|\b4\b)\b", source):
        if "set" not in details:
            details.insert(0, "set")
    return details


def _strip_shelf_style_phrasing(value: str) -> str:
    value = re.sub(r"[, ]*vintage[- ]style\b", ", style vintage", value, flags=re.I)
    value = re.sub(r"[, ]*boho[- ]style\b", ", style boho", value, flags=re.I)
    value = re.sub(r"(?<!,)\s+style\s+", ", style ", value, flags=re.I)
    value = re.sub(
        r"\b(?:bags?|handbags?|clutches?|sacs?)\b",
        "",
        value,
        flags=re.I,
    )
    return " ".join(value.split()).replace(" ,", ",")


def _normalize_english_shelf_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_shelf_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = _style_tokens_after_style_word(value)
    if not re.search(r"\bshel(?:f|ves)\b", value, flags=re.I):
        value = f"Set of 4 wall shelves {value}".strip()
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style vintage"
    return value


def _normalize_french_shelf_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_shelf_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    value = _style_tokens_after_style_word(value)
    words = value.split()
    if words and words[0].casefold() == "lot":
        if not re.search(r"\bstyle\b", value, flags=re.I):
            value = f"{value.rstrip(' ,;:-')}, style vintage"
        return value
    shelf_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"étagère", "etagere", "étagères", "etageres", "shelf", "shelves"}
        ),
        None,
    )
    if shelf_index is None:
        value = f"Étagère {value}".strip()
    else:
        remaining = [word for index, word in enumerate(words) if index != shelf_index]
        value = " ".join(["Étagère", *remaining])
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style vintage"
    return value


def _comma_one_size_suffix(title: str) -> str:
    title = re.sub(r"(?<!,)\s+(one size|taille unique)$", r", \1", title, flags=re.I)
    return clip_listing_title(title)


def _pad_shelf_title(
    title: str,
    language: str,
    product: dict[str, Any] | None = None,
) -> str:
    extras = (
        ["lot de 4", "murales", "simples"]
        if language == "french"
        else ["set of 4", "wall", "with a simple cut"]
    )
    title = _comma_one_size_suffix(_ensure_one_size(title, language, fits_all=False))
    if product:
        title = _comma_one_size_suffix(
            _ensure_one_size(
                _ensure_skirt_colour_clause(title, product, language),
                language,
                fits_all=False,
            )
        )
    for phrase in extras:
        if len(title) >= 65:
            break
        if _title_already_has(title, phrase):
            continue
        candidate = _insert_before_style_or_size(title, phrase, language)
        if len(candidate) <= 100 and len(candidate) > len(title):
            title = _comma_one_size_suffix(
                _ensure_one_size(candidate, language, fits_all=False)
            )
    return title


def _infer_beanie_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in BEANIE_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _strip_beanie_style_phrasing(value: str) -> str:
    value = re.sub(r"[, ]*y2k[- ]style\b", ", style y2k", value, flags=re.I)
    value = re.sub(r"[, ]*cute[- ]style\b", ", style y2k", value, flags=re.I)
    value = re.sub(r"(?<!,)\s+style\s+", ", style ", value, flags=re.I)
    return " ".join(value.split()).replace(" ,", ",")


def _normalize_english_beanie_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_beanie_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = " ".join(value.split())
    value = _style_tokens_after_style_word(value)
    if not re.search(r"\bbeanies?\b", value, flags=re.I):
        value = f"Beanie {value}".strip()
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style y2k"
    return " ".join(value.split())


def _normalize_french_beanie_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_beanie_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    value = _style_tokens_after_style_word(value)
    words = value.split()
    beanie_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"bonnet", "bonnets"}
        ),
        None,
    )
    if beanie_index is None:
        value = f"Bonnet {value}".strip()
    else:
        remaining = [word for index, word in enumerate(words) if index != beanie_index]
        value = " ".join(["Bonnet", *remaining])
    value = " ".join(value.split())
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style y2k"
    return value


def _pad_beanie_title(
    title: str,
    language: str,
    product: dict[str, Any] | None = None,
) -> str:
    extras = (
        ["en peluche", "rayé", "avec pompon"]
        if language == "french"
        else ["fuzzy", "striped", "with a pom-pom"]
    )
    title = _ensure_one_size(title, language)
    if product:
        title = _ensure_one_size(
            _ensure_skirt_colour_clause(title, product, language),
            language,
        )
    for phrase in extras:
        if len(title) >= 65:
            break
        if _title_already_has(title, phrase):
            continue
        candidate = _insert_before_style_or_size(title, phrase, language)
        if len(candidate) <= 100 and len(candidate) > len(title):
            title = _ensure_one_size(candidate, language)
    return title


def _infer_lamp_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in LAMP_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _strip_lamp_style_phrasing(value: str) -> str:
    value = re.sub(r"[, ]*modern[- ]style\b", ", style modern", value, flags=re.I)
    value = re.sub(r"[, ]*vintage[- ]style\b", ", style vintage", value, flags=re.I)
    value = re.sub(r"(?<!,)\s+style\s+", ", style ", value, flags=re.I)
    return " ".join(value.split()).replace(" ,", ",")


def _normalize_english_lamp_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_lamp_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = re.sub(
        r"\b(?:glass|wood|wooden|resin|metal|ceramic|acrylic)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = _style_tokens_after_style_word(value)
    if not re.search(r"\blamps?\b", value, flags=re.I):
        value = f"Table lamp {value}".strip()
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style modern"
    return " ".join(value.split())


def _normalize_french_lamp_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_lamp_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = re.sub(
        r"\b(?:verre|bois|r[ée]sine|m[ée]tal|c[ée]ramique|acrylique)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    value = _style_tokens_after_style_word(value)
    words = value.split()
    lamp_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"lampe", "lamp"}
        ),
        None,
    )
    if lamp_index is None:
        value = f"Lampe de table {value}".strip()
    else:
        remaining = [word for index, word in enumerate(words) if index != lamp_index]
        value = " ".join(["Lampe", *remaining])
    value = " ".join(value.split())
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style modern"
    return value


def _pad_lamp_title(
    title: str,
    language: str,
    product: dict[str, Any] | None = None,
) -> str:
    extras = (
        ["torsadé", "imprimée en 3D", "avec base sculpturale"]
        if language == "french"
        else ["twisted", "3D-printed", "with a sculptural base"]
    )
    title = _ensure_one_size(title, language)
    if product:
        title = _ensure_one_size(
            _ensure_skirt_colour_clause(title, product, language),
            language,
        )
    for phrase in extras:
        if len(title) >= 65:
            break
        if _title_already_has(title, phrase):
            continue
        candidate = _insert_before_style_or_size(title, phrase, language)
        if len(candidate) <= 100 and len(candidate) > len(title):
            title = _ensure_one_size(candidate, language)
    return title


def _infer_chandelier_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in CHANDELIER_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _strip_chandelier_style_phrasing(value: str) -> str:
    value = re.sub(r"[, ]*decorative[- ]style\b", ", style decorative", value, flags=re.I)
    value = re.sub(r"[, ]*modern[- ]style\b", ", style modern", value, flags=re.I)
    value = re.sub(r"[, ]*vintage[- ]style\b", ", style vintage", value, flags=re.I)
    value = re.sub(r"(?<!,)\s+style\s+", ", style ", value, flags=re.I)
    return " ".join(value.split()).replace(" ,", ",")


def _normalize_english_chandelier_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_chandelier_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = " ".join(value.split())
    value = _style_tokens_after_style_word(value)
    if not re.search(r"\b(?:chandeliers?|pendant\s+lights?|ceiling\s+lights?)\b", value, flags=re.I):
        value = f"Chandelier {value}".strip()
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style decorative"
    return " ".join(value.split())


def _normalize_french_chandelier_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_chandelier_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    value = _style_tokens_after_style_word(value)
    words = value.split()
    chandelier_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"lustre", "lustres", "suspension", "suspensions", "plafonnier", "plafonniers"}
        ),
        None,
    )
    if chandelier_index is None:
        value = f"Lustre {value}".strip()
    else:
        remaining = [word for index, word in enumerate(words) if index != chandelier_index]
        value = " ".join(["Lustre", *remaining])
    value = " ".join(value.split())
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style decorative"
    return value


def _pad_chandelier_title(
    title: str,
    language: str,
    product: dict[str, Any] | None = None,
) -> str:
    extras = (
        ["à plusieurs niveaux", "avec touches de cristal", "en métal"]
        if language == "french"
        else ["multi-tiered", "with crystal accents", "metal"]
    )
    title = _ensure_one_size(title, language)
    if product:
        title = _ensure_one_size(
            _ensure_skirt_colour_clause(title, product, language),
            language,
        )
    for phrase in extras:
        if len(title) >= 65:
            break
        if _title_already_has(title, phrase):
            continue
        candidate = _insert_before_style_or_size(title, phrase, language)
        if len(candidate) <= 100 and len(candidate) > len(title):
            title = _ensure_one_size(candidate, language)
    return title


def _infer_carpet_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in CARPET_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _strip_carpet_style_phrasing(value: str) -> str:
    value = re.sub(r"[, ]*decorative[- ]style\b", ", style decorative", value, flags=re.I)
    value = re.sub(r"[, ]*modern[- ]style\b", ", style modern", value, flags=re.I)
    value = re.sub(r"(?<!,)\s+style\s+", ", style ", value, flags=re.I)
    return " ".join(value.split()).replace(" ,", ",")


def _normalize_english_carpet_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_carpet_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = re.sub(
        r"\b(?:wool|cotton|polyester|jute|nylon|polypropylene)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = _style_tokens_after_style_word(value)
    if not re.search(r"\b(?:carpets?|rugs?)\b", value, flags=re.I):
        value = f"Area rug {value}".strip()
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style decorative"
    return " ".join(value.split())


def _normalize_french_carpet_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_carpet_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = re.sub(
        r"\b(?:laine|coton|polyester|jute|nylon|polypropyl[eè]ne)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    value = _style_tokens_after_style_word(value)
    words = value.split()
    carpet_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"tapis"}
        ),
        None,
    )
    if carpet_index is None:
        value = f"Tapis {value}".strip()
    else:
        remaining = [word for index, word in enumerate(words) if index != carpet_index]
        value = " ".join(["Tapis", *remaining])
    value = " ".join(value.split())
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style decorative"
    return value


def _pad_carpet_title(
    title: str,
    language: str,
    product: dict[str, Any] | None = None,
) -> str:
    extras = (
        ["à motif géométrique", "tissé à la main"]
        if language == "french"
        else ["with a geometric pattern", "hand-woven"]
    )
    title = _ensure_one_size(title, language)
    if product:
        title = _ensure_one_size(
            _ensure_skirt_colour_clause(title, product, language),
            language,
        )
    for phrase in extras:
        if len(title) >= 65:
            break
        if _title_already_has(title, phrase):
            continue
        candidate = _insert_before_style_or_size(title, phrase, language)
        if len(candidate) <= 100 and len(candidate) > len(title):
            title = _ensure_one_size(candidate, language)
    return title


def _infer_cushion_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in CUSHION_DETAIL_WORDS:
        if detail in source and detail not in details:
            details.append(detail)
        if len(details) >= 3:
            break
    return details


def _strip_cushion_style_phrasing(value: str) -> str:
    value = re.sub(r"[, ]*decorative[- ]style\b", ", style decorative", value, flags=re.I)
    value = re.sub(r"[, ]*boho[- ]style\b", ", style boho", value, flags=re.I)
    value = re.sub(r"(?<!,)\s+style\s+", ", style ", value, flags=re.I)
    return " ".join(value.split()).replace(" ,", ",")


def _normalize_english_cushion_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_cushion_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = re.sub(
        r"\b(?:linen|cotton|velvet|polyester|silk|suede|leather)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = _style_tokens_after_style_word(value)
    if not re.search(r"\b(?:cushions?|pillows?)\b", value, flags=re.I):
        value = f"Decorative cushion {value}".strip()
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style decorative"
    return " ".join(value.split())


def _normalize_french_cushion_title(value: str) -> str:
    value = _strip_coat_belt_and_in_an(
        _strip_cushion_style_phrasing(_strip_materials(_strip_size_noise(value)))
    )
    value = re.sub(
        r"\b(?:lin|coton|velours|polyester|soie|daim|cuir)\b",
        "",
        value,
        flags=re.I,
    )
    value = " ".join(value.split())
    value = re.sub(r"\bstyle\s+elegant\b", "style élégant", value, flags=re.I)
    value = _translate_french_title_colours(value)
    value = _style_tokens_after_style_word(value)
    words = value.split()
    cushion_index = next(
        (
            index
            for index, word in enumerate(words)
            if word.casefold() in {"coussin"}
        ),
        None,
    )
    if cushion_index is None:
        value = f"Coussin {value}".strip()
    else:
        remaining = [word for index, word in enumerate(words) if index != cushion_index]
        value = " ".join(["Coussin", *remaining])
    value = " ".join(value.split())
    if not re.search(r"\bstyle\b", value, flags=re.I):
        value = f"{value.rstrip(' ,;:-')}, style decorative"
    return value


def _pad_cushion_title(
    title: str,
    language: str,
    product: dict[str, Any] | None = None,
) -> str:
    extras = (
        ["à texture travaillée", "avec pompons"]
        if language == "french"
        else ["with a textured weave", "with tassel trim"]
    )
    title = _ensure_one_size(title, language)
    if product:
        title = _ensure_one_size(
            _ensure_skirt_colour_clause(title, product, language),
            language,
        )
    for phrase in extras:
        if len(title) >= 65:
            break
        if _title_already_has(title, phrase):
            continue
        candidate = _insert_before_style_or_size(title, phrase, language)
        if len(candidate) <= 100 and len(candidate) > len(title):
            title = _ensure_one_size(candidate, language)
    return title


def _first_measurement_cm(measurements: dict[str, str], *tokens: str) -> float | None:
    for label, value in measurements.items():
        if not any(token in label.casefold() for token in tokens):
            continue
        match = re.search(r"\d+(?:[.,]\d+)?", str(value))
        if match:
            return float(match.group(0).replace(",", "."))
    return None


def _style_article(style: str) -> str:
    return "an" if style[:1].casefold() in {"a", "e", "i", "o", "u"} else "a"


def _dress_title_needs_repair(value: str) -> bool:
    cleaned = _strip_title_filler(value)
    return (
        bool(re.match(r"^(?:long\s+|midi\s+|mini\s+)?dress\s+in\b", cleaned, re.I))
        or "taille" in cleaned.casefold()
    )


def _repair_english_dress_title(value: str, product: dict[str, Any]) -> str:
    if not _dress_title_needs_repair(value):
        return _strip_title_filler(value)
    measurements = product.get("measurements") or {}
    length_cm = _first_measurement_cm(measurements, "longueur", "length")
    source = _words_from_product(product) + " " + value.casefold()
    colour = _infer_colour(product)
    if colour == "neutral":
        match = re.search(r"\bdress\s+in\s+([^,]+)", value, flags=re.I)
        if match:
            extracted = _english_colour(match.group(1).split()[0])
            known = set(COLOR_WORDS) | {english for _, english in FRENCH_TO_ENGLISH_COLOURS}
            if extracted in known:
                colour = extracted
        if colour == "neutral" and "nude" in source:
            colour = "nude"
    style = _infer_styles(product, "dress").split()[0] or "elegant"
    length_word = (
        "Long"
        if (length_cm and length_cm >= 115) or "long" in source or "maxi" in source
        else "Midi"
        if (length_cm and length_cm >= 85) or "midi" in source
        else ""
    )
    details = _infer_details(product)
    extra = details[0] if details else ""
    with_phrase = SKIRT_DETAIL_EN.get(extra.replace(" ", "-"), "") if extra else ""
    title = " ".join(
        part for part in (length_word, extra, "dress", with_phrase) if part
    )
    if colour and colour != "neutral":
        title = f"{title}, {colour}"
    title = f"{title}, style {style}"
    return " ".join(title.split()).strip()


def _clean_description_body(value: str, title: str) -> str:
    condition_phrases = ("Perfect condition.", "Parfait Ã©tat.")
    blocks = [
        " ".join(block.split())
        for block in value.replace("\r", "").split("\n")
        if block.strip()
    ]
    kept = [
        block
        for block in blocks
        if block.casefold() != title.casefold()
        and block not in condition_phrases
    ]
    body = " ".join(kept)
    for phrase in condition_phrases:
        body = body.replace(phrase, "").strip()
    return " ".join(body.split())


def _dress_fallback_paragraph(language: str) -> str:
    if language == "french":
        return (
            "Cette robe taille S offre une allure soignée et distinctive, "
            "fidèle aux détails visibles sur l'article original. Sa coupe, sa "
            "silhouette et sa couleur permettent de composer facilement une "
            "tenue élégante pour le quotidien, une soirée ou une occasion "
            "spéciale. Elle s'associe aussi bien à des accessoires discrets "
            "qu'à des pièces plus affirmées selon le style recherché. Sa "
            "silhouette équilibrée se porte facilement avec des talons, des "
            "bottines ou des bijoux discrets, tout en laissant les détails "
            "visibles occuper le premier plan de la tenue."
        )
    return (
        "This Size S dress offers a polished and distinctive look that stays "
        "faithful to the details visible on the original item. Its cut, "
        "silhouette, and colour make it easy to style for everyday outfits, "
        "evenings, or special occasions. Pair it with understated accessories "
        "for a refined finish or stronger statement pieces for a more "
        "expressive look. The balanced shape works easily with heels, boots, "
        "or minimal jewellery, while the visible design remains the main "
        "focus of the finished outfit."
    )


def _skirt_fallback_paragraph(language: str) -> str:
    if language == "french":
        return (
            "Cette jupe taille S prÃ©sente une allure soignÃ©e qui met en valeur "
            "les dÃ©tails visibles de l'article. Sa coupe, sa silhouette et sa "
            "couleur permettent de crÃ©er facilement une tenue pour le "
            "quotidien, une sortie ou une occasion plus habillÃ©e. Elle peut "
            "Ãªtre associÃ©e Ã  un haut sobre pour un rendu raffinÃ© ou Ã  des "
            "accessoires affirmÃ©s pour accentuer son style."
        )
    return (
        "This Size S skirt has a polished look that highlights the visible "
        "details of the original item. Its cut, silhouette, and colour make "
        "it easy to style for everyday outfits, evenings, or more dressed-up "
        "occasions. Pair it with a simple top for a refined finish or add "
        "stronger accessories to emphasize its chosen aesthetic."
    )


def apply_required_listing_format(
    listing: ListingPair,
    product: dict[str, Any],
    listing_type: str,
    skirt_length: str | None = None,
) -> ListingPair:
    measurements = product.get("measurements") or {}
    if listing_type not in {"dress", "dress_m", "skirt", "jeans", "coat"}:
        # These item types don't need clothing body measurements, but any
        # other extracted dimension (length, width, height, diameter,
        # depth, circumference, whatever the photo-extraction feature
        # reads off the image) should still reach the description.
        excluded_body_measurement_tokens = (
            "poitrine", "bust", "tour de taille", "waist", "hanche", "hip",
            "manche", "sleeve", "carrure", "shoulder width", "tour de bras",
            "arm circumference", "tour de poignet", "wrist", "cuisse",
            "thigh", "entrejambe", "inseam",
        )
        measurements = {
            label: value
            for label, value in measurements.items()
            if not any(
                token in label.casefold()
                for token in excluded_body_measurement_tokens
            )
        }

    if listing_type in {"dress", "dress_m"}:
        size = _listing_title_size(listing_type)
        listing.english.title = _ensure_letter_size(
            _repair_english_dress_title(listing.english.title, product),
            "english",
            size,
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _normalize_french_dress_title(
            listing.french.title
        )
        listing.french.title = _ensure_letter_size(
            listing.french.title,
            "french",
            size,
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type=listing_type,
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type=listing_type,
        )
    elif listing_type == "skirt":
        listing.english.title = _ensure_letter_size(
            _normalize_english_skirt_title(
                listing.english.title,
                skirt_length,
            ),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_skirt_title(
                listing.french.title,
                skirt_length,
            ),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            skirt_length,
            listing_type="skirt",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            skirt_length,
            listing_type="skirt",
        )
    elif listing_type == "jeans":
        listing.english.title = _ensure_letter_size(
            _normalize_english_jeans_title(listing.english.title),
            "english",
            "L",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_jeans_title(listing.french.title),
            "french",
            "L",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="jeans",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="jeans",
        )
    elif listing_type == "long_boots":
        listing.english.title = _ensure_letter_size(
            _normalize_english_boots_title(listing.english.title),
            "english",
            "38",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_boots_title(listing.french.title),
            "french",
            "38",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="long_boots",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="long_boots",
        )
    elif listing_type == "heels":
        listing.english.title = _ensure_letter_size(
            _normalize_english_heels_title(listing.english.title),
            "english",
            "38",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_heels_title(listing.french.title),
            "french",
            "38",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="heels",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="heels",
        )
    elif listing_type == "coat":
        listing.english.title = _ensure_letter_size(
            _normalize_english_coat_title(listing.english.title),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_coat_title(listing.french.title),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="coat",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="coat",
        )
    elif listing_type == "jacket":
        listing.english.title = _ensure_letter_size(
            _normalize_english_jacket_title(listing.english.title),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_jacket_title(listing.french.title),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="jacket",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="jacket",
        )
    elif listing_type == "organizer":
        listing.english.title = _ensure_letter_size(
            _normalize_english_organizer_title(listing.english.title),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_organizer_title(listing.french.title),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="organizer",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="organizer",
        )
    elif listing_type == "leg_warmer":
        listing.english.title = _ensure_letter_size(
            _normalize_english_leg_warmer_title(listing.english.title),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_leg_warmer_title(listing.french.title),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="leg_warmer",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="leg_warmer",
        )
    elif listing_type == "jewelry_box":
        listing.english.title = _ensure_letter_size(
            _normalize_english_jewelry_box_title(listing.english.title),
            "english",
            "L",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_jewelry_box_title(listing.french.title),
            "french",
            "L",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="jewelry_box",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="jewelry_box",
        )
    elif listing_type == "lace_umbrella":
        listing.english.title = _ensure_letter_size(
            _normalize_english_lace_umbrella_title(listing.english.title),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_lace_umbrella_title(listing.french.title),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="lace_umbrella",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="lace_umbrella",
        )
    elif listing_type == "belt":
        listing.english.title = _ensure_letter_size(
            _normalize_english_belt_title(listing.english.title),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_belt_title(listing.french.title),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="belt",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="belt",
        )
    elif listing_type == "mirror":
        listing.english.title = _ensure_letter_size(
            _normalize_english_mirror_title(listing.english.title),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_mirror_title(listing.french.title),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="mirror",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="mirror",
        )
    elif listing_type == "sculpture":
        listing.english.title = _ensure_letter_size(
            _normalize_english_sculpture_title(listing.english.title),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_sculpture_title(listing.french.title),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="sculpture",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="sculpture",
        )
    elif listing_type == "curtain":
        listing.english.title = _ensure_letter_size(
            _normalize_english_curtain_title(listing.english.title),
            "english",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.french.title = _ensure_letter_size(
            _normalize_french_curtain_title(listing.french.title),
            "french",
            "S",
            require_style=True,
            skirt_style=True,
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="curtain",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="curtain",
        )
    elif listing_type == "necktie":
        listing.english.title = _ensure_style_title_no_size(
            _normalize_english_necktie_title(listing.english.title),
            "english",
        )
        listing.french.title = _ensure_style_title_no_size(
            _normalize_french_necktie_title(listing.french.title),
            "french",
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="necktie",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="necktie",
        )
    elif listing_type == "bag":
        listing.english.title = _ensure_style_title_no_size(
            _normalize_english_bag_title(listing.english.title),
            "english",
        )
        listing.french.title = _ensure_style_title_no_size(
            _normalize_french_bag_title(listing.french.title),
            "french",
        )
        listing.english.title = _expand_skirt_title(
            listing.english.title,
            product,
            "english",
            None,
            listing_type="bag",
        )
        listing.french.title = _expand_skirt_title(
            listing.french.title,
            product,
            "french",
            None,
            listing_type="bag",
        )
        listing.english.title = _ensure_one_size(listing.english.title, "english")
        listing.french.title = _ensure_one_size(listing.french.title, "french")
    elif listing_type == "plant":
        listing.english.title = _ensure_style_title_no_size(
            listing.english.title,
            "english",
        )
        listing.french.title = _ensure_style_title_no_size(
            listing.french.title,
            "french",
        )
    elif listing_type == "hat":
        listing.english.title = _ensure_one_size(listing.english.title, "english")
        listing.french.title = _ensure_one_size(listing.french.title, "french")
    elif listing_type == "mask":
        listing.english.title = _pad_mask_title(
            _normalize_english_mask_title(listing.english.title),
            "english",
        )
        listing.french.title = _pad_mask_title(
            _normalize_french_mask_title(listing.french.title),
            "french",
        )
    elif listing_type == "shelf":
        listing.english.title = _pad_shelf_title(
            _normalize_english_shelf_title(listing.english.title),
            "english",
            product,
        )
        listing.french.title = _pad_shelf_title(
            _normalize_french_shelf_title(listing.french.title),
            "french",
            product,
        )
    elif listing_type == "beanie":
        listing.english.title = _pad_beanie_title(
            _normalize_english_beanie_title(listing.english.title),
            "english",
            product,
        )
        listing.french.title = _pad_beanie_title(
            _normalize_french_beanie_title(listing.french.title),
            "french",
            product,
        )
    elif listing_type == "lamp":
        listing.english.title = _pad_lamp_title(
            _normalize_english_lamp_title(listing.english.title),
            "english",
            product,
        )
        listing.french.title = _pad_lamp_title(
            _normalize_french_lamp_title(listing.french.title),
            "french",
            product,
        )
    elif listing_type == "chandelier":
        listing.english.title = _pad_chandelier_title(
            _normalize_english_chandelier_title(listing.english.title),
            "english",
            product,
        )
        listing.french.title = _pad_chandelier_title(
            _normalize_french_chandelier_title(listing.french.title),
            "french",
            product,
        )
    elif listing_type == "carpet":
        listing.english.title = _pad_carpet_title(
            _normalize_english_carpet_title(listing.english.title),
            "english",
            product,
        )
        listing.french.title = _pad_carpet_title(
            _normalize_french_carpet_title(listing.french.title),
            "french",
            product,
        )
    elif listing_type == "cushion":
        listing.english.title = _pad_cushion_title(
            _normalize_english_cushion_title(listing.english.title),
            "english",
            product,
        )
        listing.french.title = _pad_cushion_title(
            _normalize_french_cushion_title(listing.french.title),
            "french",
            product,
        )

    listing.english.title = clip_listing_title(listing.english.title)
    listing.french.title = clip_listing_title(listing.french.title)

    english_parts = [
        f"{ENGLISH_MEASUREMENT_LABELS.get(label, label)}: {value}"
        for label, value in measurements.items()
    ]
    french_parts = [f"{label} : {value}" for label, value in measurements.items()]

    english_blocks = [listing.english.title]
    french_blocks = [listing.french.title]
    if english_parts:
        english_blocks.append(" · ".join(english_parts))
        french_blocks.append(" · ".join(french_parts))
    if listing_type in {"dress", "dress_m", "skirt", "hat", "mask", "shelf", "jeans", "long_boots", "heels", "coat", "jacket", "bag", "plant", "organizer", "leg_warmer", "lace_umbrella", "beanie"}:
        english_blocks.append("prices are negotiable :)")
        french_blocks.append("prix négociable :)")
    elif listing_type in {"lamp", "mirror", "sculpture", "carpet", "cushion", "chandelier"}:
        english_blocks.append("price negotiable :)")
        french_blocks.append("prix négociable :)")
    elif listing_type in {"curtain", "necktie", "jewelry_box", "belt"}:
        english_blocks.append("price are negotiable :)")
        french_blocks.append("prix négociable :)")
    english_blocks.append("Perfect condition.")
    french_blocks.append(
        "État parfait" if listing_type == "skirt" else "Parfait état."
    )
    listing.english.description = "\n\n".join(english_blocks)
    listing.french.description = "\n\n".join(french_blocks)
    listing.english.hashtags = _ensure_twenty_hashtags(
        listing.english.hashtags, listing_type, "english", product
    )
    listing.french.hashtags = _ensure_twenty_hashtags(
        listing.french.hashtags, listing_type, "french", product
    )
    return listing


def validate_gemini_payload(payload: str | dict[str, Any]) -> ListingPair:
    if isinstance(payload, str):
        cleaned = payload.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```")
            cleaned = cleaned.removesuffix("```").strip()
        data = json.loads(cleaned)
    else:
        data = payload
    return ListingPair.model_validate(data)


def validate_fast_draft_payload(
    payload: str | dict[str, Any],
) -> FastListingDraft:
    if isinstance(payload, str):
        cleaned = payload.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```")
            cleaned = cleaned.removesuffix("```").strip()
        data = json.loads(cleaned)
    else:
        data = payload
    return FastListingDraft.model_validate(data)


def fast_draft_titles_are_detailed(
    draft: FastListingDraft,
    listing_type: str,
) -> bool:
    if listing_type in {"skirt", "jeans", "long_boots", "heels", "coat", "jacket", "bag", "plant", "mask", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"}:
        return (
            65 <= len(draft.english_title) <= 100
            and 65 <= len(draft.french_title) <= 100
            and "style" in draft.english_title.casefold()
            and "style" in draft.french_title.casefold()
        )
    minimum = 80 if listing_type in {"dress", "dress_m"} else 65
    return (
        minimum <= len(draft.english_title) <= 100
        and minimum <= len(draft.french_title) <= 100
    )


def listing_titles_are_detailed(
    listing: ListingPair,
    listing_type: str,
) -> bool:
    if listing_type == "jeans":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size l")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille l")
            and "l-size belt" not in listing.english.title.casefold()
            and "s-size belt" not in listing.english.title.casefold()
            and "ceinture" not in listing.french.title.casefold()
            and "baggy" in listing.english.title.casefold()
            and "baggy" in listing.french.title.casefold()
            and "wide" not in listing.english.title.casefold()
            and not re.search(r"\bjambes\s+larges\b", listing.french.title.casefold())
            and not re.search(r"\bmen'?s\b|\bmen\b|\bfor men\b", listing.english.title.casefold())
            and not re.search(r"\bhommes?\b", listing.french.title.casefold())
        )
    if listing_type == "skirt":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and "s-size belt" not in listing.english.title.casefold()
            and "l-size belt" not in listing.english.title.casefold()
            and "in an" not in listing.english.title.casefold()
            and "in a " not in listing.english.title.casefold()
            and "ceinture" not in listing.french.title.casefold()
        )
    if listing_type == "coat":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and "s-size belt" not in listing.english.title.casefold()
            and "ceinture" not in listing.french.title.casefold()
        )
    if listing_type == "jacket":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and "s-size belt" not in listing.english.title.casefold()
            and "ceinture" not in listing.french.title.casefold()
            and "jacket" in listing.english.title.casefold()
        )
    if listing_type in {"long_boots", "heels"}:
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size 38")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille 38")
        )
    if listing_type == "plant":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and "style" in listing.french.title.casefold()
        )
    if listing_type == "bag":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("one size fits all")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille unique")
            and any(
                word in listing.english.title.casefold()
                for word in ("bag", "handbag", "tote", "clutch", "crossbody")
            )
        )
    if listing_type == "mask":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("one size fits all")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille unique")
            and "mask" in listing.english.title.casefold()
            and "halloween costume style" not in listing.english.title.casefold()
        )
    if listing_type == "shelf":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("one size")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille unique")
            and ("shelf" in listing.english.title.casefold() or "shelves" in listing.english.title.casefold())
            and "vintage-style" not in listing.english.title.casefold()
        )
    if listing_type == "beanie":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("one size fits all")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille unique")
            and "beanie" in listing.english.title.casefold()
        )
    if listing_type == "organizer":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and "organizer" in listing.english.title.casefold()
        )
    if listing_type == "leg_warmer":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and "leg warmer" in listing.english.title.casefold()
        )
    if listing_type == "jewelry_box":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size l")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille l")
            and "jewelry box" in listing.english.title.casefold()
        )
    if listing_type == "lace_umbrella":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and any(
                word in listing.english.title.casefold()
                for word in ("parasol", "umbrella")
            )
        )
    if listing_type == "belt":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and "belt" in listing.english.title.casefold()
        )
    if listing_type == "mirror":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and "mirror" in listing.english.title.casefold()
        )
    if listing_type == "sculpture":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and any(
                word in listing.english.title.casefold()
                for word in ("sculpture", "statue", "statuette", "figurine")
            )
        )
    if listing_type == "lamp":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("one size fits all")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille unique")
            and "lamp" in listing.english.title.casefold()
        )
    if listing_type == "chandelier":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("one size fits all")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille unique")
            and any(
                word in listing.english.title.casefold()
                for word in ("chandelier", "pendant light", "ceiling light")
            )
        )
    if listing_type == "carpet":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("one size fits all")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille unique")
            and any(
                word in listing.english.title.casefold()
                for word in ("carpet", "rug")
            )
        )
    if listing_type == "cushion":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("one size fits all")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille unique")
            and any(
                word in listing.english.title.casefold()
                for word in ("cushion", "pillow")
            )
        )
    if listing_type == "curtain":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and listing.english.title.casefold().endswith("size s")
            and "style" in listing.french.title.casefold()
            and listing.french.title.casefold().endswith("taille s")
            and any(
                word in listing.english.title.casefold()
                for word in ("curtain", "drape")
            )
        )
    if listing_type == "necktie":
        return (
            65 <= len(listing.english.title) <= 100
            and 65 <= len(listing.french.title) <= 100
            and ", style " in listing.english.title.casefold()
            and "style" in listing.french.title.casefold()
            and any(
                word in listing.english.title.casefold()
                for word in ("necktie", "tie")
            )
        )
    if listing_type not in {"dress", "dress_m"}:
        return True
    size = _listing_title_size(listing_type).casefold()
    return (
        80 <= len(listing.english.title) <= 100
        and 80 <= len(listing.french.title) <= 100
        and listing.english.title.casefold().endswith(f"size {size}")
        and listing.french.title.casefold().endswith(f"taille {size}")
    )


_GEMINI_CLIENT: Any = None


def _get_gemini_client() -> Any:
    global _GEMINI_CLIENT
    if _GEMINI_CLIENT is None:
        from google import genai

        # Try API key first, fall back to ADC
        _GEMINI_CLIENT = genai.Client(api_key=settings.gemini_api_key)

    return _GEMINI_CLIENT


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _product_facts(product: dict[str, Any], listing_type: str) -> dict[str, Any]:
    details = product.get("additional_details") or {}
    facts = {
        "listing_type": listing_type,
        "sku": product.get("sku"),
        "title": product.get("title"),
        "category": product.get("category"),
        "colour": product.get("colour"),
        "measurements": product.get("measurements") or {},
        "available_sizes": details.get("available_sizes"),
        "selected_size": details.get("selected_size") or _listing_title_size(listing_type),
        "source_price_eur": (details.get("price_calculator") or {}).get(
            "source_price_eur"
        ),
        "product_url": product.get("product_url"),
        "selected_description_image_url": details.get(
            "selected_description_image_url"
        ),
        "ignore_page_colour_for_description": details.get(
            "ignored_page_colour_for_description"
        ),
    }
    if listing_type in {"heels", "long_boots", "plant", "jacket", "organizer", "mirror", "sculpture", "curtain", "leg_warmer", "jewelry_box", "lace_umbrella", "belt"}:
        facts["measurements"] = {}
    if listing_type == "mask":
        facts["selected_size"] = None
    if listing_type == "shelf":
        facts["selected_size"] = None
    if listing_type == "beanie":
        facts["selected_size"] = None
    if listing_type == "lamp":
        facts["selected_size"] = None
    if listing_type == "chandelier":
        facts["selected_size"] = None
    if listing_type == "carpet":
        facts["selected_size"] = None
    if listing_type == "cushion":
        facts["selected_size"] = None
    if listing_type == "necktie":
        facts["selected_size"] = None
    if listing_type in {"bag", "plant"}:
        facts["selected_size"] = None
    if listing_type == "plant":
        facts["measurements"] = {}
    return facts


async def _load_image_candidate(image_url: str) -> tuple[bytes, str] | None:
    if image_url.startswith("/downloads/"):
        loaded = await asyncio.to_thread(read_local_download_image, image_url)
        if loaded:
            return loaded
            return None
    if image_url.startswith("https://"):
        return await fetch_image(image_url)
    return None


async def load_image_by_url(image_url: str | None) -> tuple[bytes, str] | None:
    image_url = str(image_url or "").strip()
    if not image_url:
        return None
    for candidate in candidate_reference_image_urls(image_url):
        try:
            loaded = await _load_image_candidate(candidate)
        except AppError as exc:
            logger.warning("Reference image candidate failed (%s): %s", candidate, exc)
            continue
        if not loaded:
            continue
        prepared = prepare_image_for_gemini(*loaded)
        if prepared:
            return prepared
        logger.warning("Reference image was too small or unsupported: %s", candidate)
    return None


async def load_reference_image(product: dict[str, Any]) -> tuple[bytes, str] | None:
    return await load_image_by_url(product.get("main_image_url"))


def _reserved_brand_prompt(reserved_brands: set[str] | list[str] | None) -> str:
    names = sorted({str(name).strip() for name in (reserved_brands or []) if str(name).strip()})
    if not names:
        return ""
    return (
        "Already used brand/style names — do not reuse any of these: "
        + ", ".join(names)
        + ". Pick a different unused name."
    )


def _fast_prompt(
    product: dict[str, Any],
    listing_type: str,
    skirt_length: str | None,
    reserved_brands: set[str] | list[str] | None = None,
) -> str:
    instructions = FAST_TYPE_INSTRUCTIONS.get(
        listing_type,
        FAST_TYPE_INSTRUCTIONS["dress"],
    )
    facts = _product_facts(product, listing_type)
    if listing_type == "skirt" and skirt_length:
        facts["selected_skirt_length"] = skirt_length
    selected_image_rule = (
        "User-selected image override: selected_description_image_url is present. "
        "Use the attached selected image as the only visual source of truth for "
        "colour and design. Ignore title/colour conflicts from the product page."
        if facts.get("selected_description_image_url")
        else ""
    )
    return "\n\n".join(
        part
        for part in (
            FAST_BASE_INSTRUCTIONS,
            VISUAL_ACCURACY_INSTRUCTIONS,
            selected_image_rule,
            _reserved_brand_prompt(reserved_brands),
            instructions,
            "Verified product facts:",
            _safe_json(facts),
        )
        if part
    )


def _full_prompt(
    product: dict[str, Any],
    listing_type: str,
    skirt_length: str | None,
    reserved_brands: set[str] | list[str] | None = None,
) -> str:
    type_instructions = {
        "small_plant": SMALL_PLANT_INSTRUCTIONS,
        "dress": DRESS_INSTRUCTIONS,
        "dress_m": DRESS_M_INSTRUCTIONS,
        "earrings": EARRINGS_INSTRUCTIONS,
        "bag": BAG_INSTRUCTIONS,
        "plant": PLANT_INSTRUCTIONS,
        "skirt": SKIRT_INSTRUCTIONS,
        "jeans": JEANS_INSTRUCTIONS,
        "long_boots": LONG_BOOTS_INSTRUCTIONS,
        "heels": HEELS_INSTRUCTIONS,
        "coat": COAT_INSTRUCTIONS,
        "jacket": JACKET_INSTRUCTIONS,
        "hat": HAT_INSTRUCTIONS,
        "mask": MASK_INSTRUCTIONS,
        "shelf": SHELF_INSTRUCTIONS,
        "beanie": BEANIE_INSTRUCTIONS,
        "organizer": ORGANIZER_INSTRUCTIONS,
        "lamp": LAMP_INSTRUCTIONS,
        "chandelier": CHANDELIER_INSTRUCTIONS,
        "carpet": CARPET_INSTRUCTIONS,
        "cushion": CUSHION_INSTRUCTIONS,
        "mirror": MIRROR_INSTRUCTIONS,
        "sculpture": SCULPTURE_INSTRUCTIONS,
        "curtain": CURTAIN_INSTRUCTIONS,
        "necktie": NECKTIE_INSTRUCTIONS,
        "leg_warmer": LEG_WARMER_INSTRUCTIONS,
        "jewelry_box": JEWELRY_BOX_INSTRUCTIONS,
        "lace_umbrella": LACE_UMBRELLA_INSTRUCTIONS,
        "belt": BELT_INSTRUCTIONS,
    }.get(listing_type, DRESS_INSTRUCTIONS)
    facts = _product_facts(product, listing_type)
    if listing_type == "skirt" and skirt_length:
        facts["selected_skirt_length"] = skirt_length
    selected_image_rule = (
        "User-selected image override: selected_description_image_url is present. "
        "Use the attached selected image as the only visual source of truth for "
        "colour and design. Ignore title/colour conflicts from the product page."
        if facts.get("selected_description_image_url")
        else ""
    )
    return "\n\n".join(
        part
        for part in (
            BASE_INSTRUCTIONS,
            VISUAL_ACCURACY_INSTRUCTIONS,
            selected_image_rule,
            _reserved_brand_prompt(reserved_brands),
            type_instructions,
            "Verified product facts:",
            _safe_json(facts),
        )
        if part
    )


COLOR_WORDS = (
    "black", "white", "cream", "beige", "brown", "red", "pink", "blue",
    "green", "yellow", "gold", "silver", "grey", "gray", "purple",
    "orange", "burgundy", "navy", "turquoise", "khaki", "nude", "tobacco",
)

DETAIL_WORDS = (
    "floral", "lace", "ruffle", "ruffled", "pleated", "wrap", "halter",
    "off-shoulder", "strapless", "sleeveless", "backless", "v-neck",
    "square-neck", "mesh", "sequin", "sequins", "slit", "split", "buckle",
    "bodycon", "asymmetric", "printed", "tiered", "draped", "bow", "belted",
    "color-block", "colour-block",
)

STYLE_HINTS = (
    ("goth", "gothic"),
    ("skull", "gothic"),
    ("black", "gothic"),
    ("boho", "boho"),
    ("bohemian", "bohemian"),
    ("western", "western"),
    ("cowboy", "cowboy"),
    ("cowgirl", "cowgirl"),
    ("y2k", "y2k"),
    ("vintage", "y2k"),
    ("street", "streetwear"),
    ("grunge", "grunge"),
    ("elegant", "elegant"),
    ("formal", "elegant"),
    ("party", "elegant"),
)

SIZE_NOISE_PATTERN = re.compile(
    r"\b(?:size|taille)\s*:?\s*(?:XXS|XS|S|M|L|XL|XXL|XXXL|[2-5]XL|\d{2,3})\b",
    re.I,
)

FRENCH_TO_ENGLISH_COLOURS = (
    ("rouge fonce", "dark red"),
    ("vert fonce", "dark green"),
    ("rose poudre", "soft pink"),
    ("blanc casse", "off white"),
    ("blanc creme", "cream white"),
    ("creme", "cream"),
    ("dore", "gold"),
    ("argente", "silver"),
    ("rouge fonc\u00e9", "dark red"),
    ("vert fonc\u00e9", "dark green"),
    ("rose poudr\u00e9", "soft pink"),
    ("blanc cass\u00e9", "off white"),
    ("blanc cr\u00e8me", "cream white"),
    ("cr\u00e8me", "cream"),
    ("dor\u00e9", "gold"),
    ("argent\u00e9", "silver"),
    ("rouge foncé", "dark red"),
    ("rouge sombre", "dark red"),
    ("bleu marine", "navy blue"),
    ("bleu ciel", "sky blue"),
    ("vert foncé", "dark green"),
    ("rose poudré", "soft pink"),
    ("rose clair", "light pink"),
    ("blanc cassé", "off white"),
    ("blanc crème", "cream white"),
    ("crème", "cream"),
    ("marron", "brown"),
    ("beige", "beige"),
    ("rouge", "red"),
    ("noir", "black"),
    ("blanc", "white"),
    ("rose", "pink"),
    ("bleu", "blue"),
    ("vert", "green"),
    ("jaune", "yellow"),
    ("doré", "gold"),
    ("argenté", "silver"),
    ("gris", "grey"),
    ("violet", "purple"),
    ("orange", "orange"),
    ("tabac", "tobacco"),
)

ENGLISH_TO_FRENCH_COLOURS = (
    ("dark red", "rouge foncé"),
    ("dark green", "vert foncé"),
    ("navy blue", "bleu marine"),
    ("sky blue", "bleu ciel"),
    ("soft pink", "rose poudré"),
    ("light pink", "rose clair"),
    ("light grey", "gris clair"),
    ("light gray", "gris clair"),
    ("light blue", "bleu clair"),
    ("light green", "vert clair"),
    ("light brown", "marron clair"),
    ("cream white", "blanc crème"),
    ("off white", "blanc cassé"),
    ("multicolour", "multicolore"),
    ("multicolor", "multicolore"),
    ("black", "noir"),
    ("white", "blanc"),
    ("cream", "crème"),
    ("beige", "beige"),
    ("brown", "marron"),
    ("red", "rouge"),
    ("pink", "rose"),
    ("blue", "bleu"),
    ("green", "vert"),
    ("yellow", "jaune"),
    ("gold", "doré"),
    ("silver", "argenté"),
    ("grey", "gris"),
    ("gray", "gris"),
    ("purple", "violet"),
    ("burgundy", "bordeaux"),
    ("navy", "bleu marine"),
    ("turquoise", "turquoise"),
    ("khaki", "kaki"),
    ("nude", "nude"),
    ("tobacco", "tabac"),
    ("natural", "naturel"),
)


def _fold_for_matching(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    )


def _strip_size_noise(value: str) -> str:
    value = re.sub(r"(?i)([a-zÀ-ÿ])(?=taille\s*:)", r"\1 ", value)
    value = re.sub(r"(?i)(?<!\s)(taille\s*:)", r" \1", value)
    value = SIZE_NOISE_PATTERN.sub("", value)
    return " ".join(value.replace(" ,", ",").split()).strip(" ,;:-")


def _strip_title_filler(
    value: str,
    *,
    translate_french_colours: bool = False,
) -> str:
    value = _strip_size_noise(value)
    value = re.sub(
        r"\s*(?:,?\s*with a polished silhouette|,?\s*with a flowing full-length shape|,?\s*with a refined dress shape|,?\s*with an elegant style detail|,?\s*with refined cut details and a chic feminine finish|,?\s*with elegant details and a flattering fitted look|,?\s*avec coupe raffinée et finition chic|,?\s*avec détails élégants et coupe flatteuse|,?\s*for evening and occasion outfits|,?\s*for occasion outfit styling)\s*",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\s*,?\s*avec coupe raffin\S*e et finition chic\s*",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\s*,?\s*avec d\S*tails \S*l\S*gants et coupe flatteuse\s*",
        " ",
        value,
        flags=re.I,
    )
    if translate_french_colours:
        for french, english in FRENCH_TO_ENGLISH_COLOURS:
            value = re.sub(re.escape(french), english, value, flags=re.I)
    return " ".join(value.split()).strip(" ,;:-")


def _translate_french_title_colours(value: str) -> str:
    cleaned = value
    for english, french in ENGLISH_TO_FRENCH_COLOURS:
        cleaned = re.sub(
            rf"\b{re.escape(english)}\b",
            french,
            cleaned,
            flags=re.I,
        )
    return " ".join(cleaned.split())


def _english_colour(value: str) -> str:
    cleaned = _strip_size_noise(value).casefold()
    folded = _fold_for_matching(cleaned)
    for french, english in FRENCH_TO_ENGLISH_COLOURS:
        if _fold_for_matching(french) in folded:
            return english
    return _strip_size_noise(value).casefold()


def _words_from_product(product: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            product.get("title"),
            product.get("category"),
            product.get("colour"),
        )
    ).casefold()


def _infer_colour(product: dict[str, Any]) -> str:
    raw_colour = " ".join(str(product.get("colour") or "").split())
    if raw_colour:
        return _english_colour(raw_colour)
    source = _words_from_product(product)
    return next((colour for colour in COLOR_WORDS if colour in source), "neutral")


def _infer_styles(product: dict[str, Any], listing_type: str) -> str:
    source = _words_from_product(product)
    styles: list[str] = []
    for keyword, style in STYLE_HINTS:
        if keyword in source and style not in styles:
            styles.append(style)
    if listing_type in {"skirt", "hat", "mask", "jeans", "long_boots", "heels", "coat", "jacket", "bag", "plant", "organizer", "mirror", "sculpture", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "beanie"}:
        allowed = {"boho", "bohemian", "elegant", "old money", "y2k", "gothic", "streetwear", "grunge", "dark academia", "sophisticated"}
        if listing_type in {"hat", "mask", "necktie", "beanie"}:
            allowed = {"boho", "bohemian", "elegant", "western", "cowboy", "y2k", "gothic", "streetwear", "grunge", "cowgirl", "sophisticated"}
        if listing_type in {"long_boots", "heels"}:
            allowed = {"boho", "bohemian", "elegant", "old money", "y2k", "gothic", "grunge", "sophisticated"}
        styles = [style for style in styles if style in allowed]
        if listing_type == "mask":
            if any(token in source for token in ("halloween", "sequin", "masquerade", "costume")):
                if "y2k" not in styles:
                    styles.insert(0, "y2k")
                if "gothic" not in styles and "halloween" in source:
                    styles.append("gothic")
    if listing_type == "shelf":
        allowed = {"boho", "bohemian", "elegant", "vintage"}
        remapped: list[str] = []
        for style in styles:
            token = "vintage" if style == "y2k" and "vintage" in source else style
            if token in allowed and token not in remapped:
                remapped.append(token)
        if "vintage" in source and "vintage" not in remapped:
            remapped.insert(0, "vintage")
        styles = remapped
    if listing_type in {"lamp", "carpet", "cushion", "chandelier"}:
        allowed = {
            "decorative", "modern", "minimalist", "vintage", "industrial",
            "scandinavian", "artdeco", "boho",
        }
        remapped = []
        for style in styles:
            token = "vintage" if style == "y2k" and "vintage" in source else style
            if token in allowed and token not in remapped:
                remapped.append(token)
        for keyword, token in (
            ("modern", "modern"),
            ("minimalist", "minimalist"),
            ("industrial", "industrial"),
            ("scandinavian", "scandinavian"),
            ("scandi", "scandinavian"),
            ("artdeco", "artdeco"),
            ("art deco", "artdeco"),
            ("decorative", "decorative"),
            ("vintage", "vintage"),
        ):
            if keyword in source and token not in remapped:
                remapped.append(token)
        styles = remapped
    if not styles:
        styles = [
            "western" if listing_type == "hat"
            else "streetwear" if listing_type == "jeans"
            else "y2k" if listing_type in {"bag", "jacket", "mask", "organizer", "beanie"}
            else "boho" if listing_type in {"plant", "mirror", "leg_warmer"}
            else "vintage" if listing_type == "shelf"
            else "decorative" if listing_type in {"lamp", "carpet", "cushion", "chandelier"}
            else "gothic" if listing_type == "curtain"
            else "chic" if listing_type == "jewelry_box"
            else "elegant"
        ]
    return " ".join(styles[:2])


def _infer_details(product: dict[str, Any]) -> list[str]:
    source = _words_from_product(product)
    details: list[str] = []
    for detail in DETAIL_WORDS:
        if detail in source and detail.replace("-", " ") not in details:
            details.append(detail.replace("-", " "))
        if len(details) >= 3:
            break
    return details


def _item_category(product: dict[str, Any], listing_type: str, skirt_length: str | None) -> str:
    source = _words_from_product(product)
    details = _infer_details(product)
    if listing_type == "earrings":
        base = "drop earrings" if "drop" in source else "statement earrings"
    elif listing_type == "bag":
        if "crossbody" in source:
            base = "crossbody bag"
        elif "shoulder" in source:
            base = "shoulder bag"
        elif "tote" in source:
            base = "tote bag"
        else:
            base = "handbag"
    elif listing_type == "plant":
        if "cactus" in source:
            base = "cactus"
        elif "succulent" in source:
            base = "succulent"
        elif "hanging" in source:
            base = "hanging plant"
        else:
            base = "indoor plant"
    elif listing_type == "hat":
        if "cowboy" in source or "western" in source:
            base = "cowboy hat"
        elif "bucket" in source:
            base = "bucket hat"
        elif "cap" in source:
            base = "cap"
        else:
            base = "hat"
    elif listing_type == "mask":
        mask_details = _infer_mask_details(product)
        if "masquerade" in source or "masquerade" in mask_details:
            base = "masquerade mask"
        elif "sequin" in source or "sequins" in source or "sequin" in mask_details or "sequins" in mask_details:
            base = "sequin mask"
        elif "cat" in source or "cat" in mask_details:
            base = "cat mask"
        else:
            base = "mask"
        extra = [
            detail
            for detail in mask_details
            if detail not in {"masquerade", "sequin", "sequins", "cat"}
        ]
        return " ".join([*extra, base]).strip()
    elif listing_type == "shelf":
        shelf_details = _infer_shelf_details(product)
        if "set" in shelf_details or re.search(r"\b(?:set of 4|lot de 4)\b", source):
            base = "set of 4 wall shelves"
        elif "floating" in source or "floating" in shelf_details:
            base = "floating shelf"
        elif "wall" in source or "wall" in shelf_details:
            base = "wall shelf"
        else:
            base = "shelf"
        extra = [detail for detail in shelf_details if detail not in {"set", "wall"}]
        return " ".join([*extra, base]).strip()
    elif listing_type == "beanie":
        beanie_details = _infer_beanie_details(product)
        base = "beanie"
        extra = [detail for detail in beanie_details]
        return " ".join([*extra, base]).strip()
    elif listing_type == "lamp":
        lamp_details = _infer_lamp_details(product)
        if "gourd" in source or "gourd" in lamp_details:
            base = "gourd-shaped table lamp"
        elif "twisted" in source or "twisted" in lamp_details:
            base = "table lamp with a twisted design"
        else:
            base = "table lamp"
        extra = [detail for detail in lamp_details if detail not in {"gourd", "twisted"}]
        return " ".join([*extra, base]).strip()
    elif listing_type == "chandelier":
        chandelier_details = _infer_chandelier_details(product)
        if "pendant" in source:
            base = "pendant light"
        elif "flush" in source or "ceiling" in source:
            base = "ceiling light"
        else:
            base = "chandelier"
        extra = [detail for detail in chandelier_details]
        return " ".join([*extra, base]).strip()
    elif listing_type == "carpet":
        carpet_details = _infer_carpet_details(product)
        if "round" in source or "round" in carpet_details:
            base = "round rug"
        elif "runner" in source or "runner" in carpet_details:
            base = "runner rug"
        else:
            base = "area rug"
        extra = [detail for detail in carpet_details if detail not in {"round", "runner"}]
        return " ".join([*extra, base]).strip()
    elif listing_type == "cushion":
        cushion_details = _infer_cushion_details(product)
        base = "decorative cushion"
        extra = [detail for detail in cushion_details]
        return " ".join([*extra, base]).strip()
    elif listing_type == "skirt":
        length = skirt_length or ("midi" if "midi" in source else "long" if "long" in source or "maxi" in source else "")
        base = f"{length} skirt".strip()
    elif listing_type == "jeans":
        jeans_details = _infer_jeans_details(product)
        base = "baggy jeans"
        extra = [detail for detail in jeans_details if detail not in {"baggy", "wide", "wide-leg"}]
        return " ".join([*extra, base]).strip()
    elif listing_type == "long_boots":
        boots_details = _infer_boots_details(product)
        if "thigh-high" in source or "thigh-high" in boots_details:
            base = "thigh-high boots"
        elif "knee-high" in source or "knee-high" in boots_details:
            base = "knee-high boots"
        else:
            base = "knee-high boots"
        extra = [
            detail
            for detail in boots_details
            if detail not in {"knee-high", "thigh-high"}
        ]
        return " ".join([*extra, base]).strip()
    elif listing_type == "heels":
        heels_details = _infer_heels_details(product)
        if "pump" in source or "pump" in heels_details:
            base = "heeled pumps"
        elif "mule" in source or "mule" in heels_details:
            base = "heeled mules"
        else:
            base = "heeled sandals"
        extra = [detail for detail in heels_details if detail not in {"pump", "mule"}]
        return " ".join([*extra, base]).strip()
    elif listing_type == "jacket":
        jacket_details = _infer_jacket_details(product)
        if "biker" in source or "biker" in jacket_details or "moto" in source or "moto" in jacket_details:
            base = "biker jacket"
        elif "bomber" in source or "bomber" in jacket_details:
            base = "bomber jacket"
        elif "cropped" in source or "cropped" in jacket_details:
            base = "cropped jacket"
        else:
            base = "jacket"
        extra = [
            detail
            for detail in jacket_details
            if detail not in {"biker", "bomber", "moto"}
        ]
        return " ".join([*extra, base]).strip()
    elif listing_type == "organizer":
        organizer_details = _infer_organizer_details(product)
        if "rack" in source or "rack" in organizer_details:
            base = "rack organizer"
        elif "drawer" in source or "drawer" in organizer_details:
            base = "drawer organizer"
        elif "countertop" in source or "countertop" in organizer_details:
            base = "countertop organizer"
        else:
            base = "organizer"
        extra = [
            detail
            for detail in organizer_details
            if detail not in {"rack", "drawer", "countertop"}
        ]
        return " ".join([*extra, base]).strip()
    elif listing_type == "mirror":
        mirror_details = _infer_mirror_details(product)
        if "tabletop" in source or "tabletop" in mirror_details:
            base = "tabletop mirror"
        elif "asymmetrical" in source or "asymmetrical" in mirror_details:
            base = "asymmetrical wall mirror"
        else:
            base = "wall mirror"
        extra = [
            detail
            for detail in mirror_details
            if detail not in {"tabletop", "asymmetrical", "wall"}
        ]
        return " ".join([*extra, base]).strip()
    elif listing_type == "sculpture":
        sculpture_details = _infer_sculpture_details(product)
        if "figurine" in source or "figurine" in sculpture_details:
            base = "figurine sculpture"
        elif "abstract" in source or "abstract" in sculpture_details:
            base = "abstract sculpture"
        else:
            base = "sculpture"
        extra = [
            detail
            for detail in sculpture_details
            if detail not in {"figurine", "abstract"}
        ]
        return " ".join([*extra, base]).strip()
    elif listing_type == "curtain":
        curtain_details = _infer_curtain_details(product)
        if "blackout" in source or "blackout" in curtain_details:
            base = "blackout curtain"
        elif "sheer" in source or "sheer" in curtain_details:
            base = "sheer curtain"
        else:
            base = "curtain"
        extra = [
            detail
            for detail in curtain_details
            if detail not in {"blackout", "sheer"}
        ]
        return " ".join([*extra, base]).strip()
    elif listing_type == "necktie":
        necktie_details = _infer_necktie_details(product)
        base = "necktie"
        extra = [detail for detail in necktie_details]
        return " ".join([*extra, base]).strip()
    elif listing_type == "leg_warmer":
        leg_warmer_details = _infer_leg_warmer_details(product)
        base = "leg warmers"
        extra = [detail for detail in leg_warmer_details]
        return " ".join([*extra, base]).strip()
    elif listing_type == "jewelry_box":
        jewelry_box_details = _infer_jewelry_box_details(product)
        base = "jewelry box"
        extra = [detail for detail in jewelry_box_details]
        return " ".join([*extra, base]).strip()
    elif listing_type == "lace_umbrella":
        lace_umbrella_details = _infer_lace_umbrella_details(product)
        base = "lace parasol"
        extra = [detail for detail in lace_umbrella_details if detail != "lace"]
        return " ".join([*extra, base]).strip()
    elif listing_type == "belt":
        belt_details = _infer_belt_details(product)
        base = "belt"
        extra = [detail for detail in belt_details]
        return " ".join([*extra, base]).strip()
    elif listing_type == "coat":
        coat_details = _infer_coat_details(product)
        if "trench" in source or "trench" in coat_details:
            base = "trench coat"
        elif "cape" in source or "cape" in coat_details:
            base = "cape coat"
        elif "long" in source or "long" in coat_details:
            base = "long coat"
        else:
            base = "coat"
        extra = [detail for detail in coat_details if detail not in {"trench", "cape", "long"}]
        return " ".join([*extra, base]).strip()
    else:
        length = "long" if "long" in source or "maxi" in source else "midi" if "midi" in source else "mini" if "mini" in source else ""
        base = f"{length} dress".strip()
    return " ".join([*details, base]).strip()


def _pad_title(title: str, product: dict[str, Any], listing_type: str) -> str:
    title = _strip_title_filler(title)
    if listing_type in {"dress", "dress_m"}:
        return title[:100].rstrip(" ,")
    if listing_type in {"skirt", "jeans", "long_boots", "heels", "coat", "jacket", "bag", "plant", "hat", "mask", "shelf", "organizer", "lamp", "mirror", "sculpture", "carpet", "cushion", "curtain", "necktie", "leg_warmer", "jewelry_box", "lace_umbrella", "belt", "chandelier", "beanie"}:
        return title[:100].rstrip(" ,")
    if len(title) >= 65:
        return title[:100].rstrip(" ,")
    additions = {
        "dress": ["with an elegant style detail", "for occasion outfit styling"],
        "skirt": ["with a flattering shape", "for chic outfit styling"],
        "earrings": ["for polished accessory styling", "with a statement look"],
        "bag": ["for everyday outfit styling", "with a polished accessory look"],
        "hat": ["for bold accessory styling", "with a statement outfit look"],
    }[listing_type]
    for addition in additions:
        candidate = f"{title} {addition}"
        if len(candidate) <= 100:
            title = candidate
        if len(title) >= 65:
            break
    return title[:100].rstrip(" ,")


def local_listing_from_product(
    product: dict[str, Any],
    listing_type: str,
    skirt_length: str | None = None,
    reserved_brands: set[str] | list[str] | None = None,
) -> ListingPair:
    listing_type = listing_type if listing_type in FALLBACK_HASHTAGS else "dress"
    colour = _infer_colour(product)
    category = _item_category(product, listing_type, skirt_length)
    styles = _infer_styles(product, listing_type)
    if listing_type == "earrings":
        english_title = f"{category} in {colour}, style {styles}, one size fits all"
        french_title = f"Boucles d'oreilles pendantes {colour}, style {styles}, taille unique"
    elif listing_type == "bag":
        bag_details = _infer_bag_details(product)
        extra = next(
            (
                detail
                for detail in bag_details
                if detail not in {"shoulder", "crossbody", "tote", "clutch", "handbag"}
            ),
            "strap",
        )
        with_phrase = BAG_DETAIL_EN.get(extra, "with strap details")
        if "crossbody" in bag_details or "crossbody" in category:
            shaft = "Women's crossbody bag"
            french_lead = "Sac croisé femme"
        elif "shoulder" in bag_details or "shoulder" in category:
            shaft = "Women's shoulder bag"
            french_lead = "Sac bandoulière femme"
        elif "tote" in bag_details or "tote" in category:
            shaft = "Women's tote bag"
            french_lead = "Cabas femme"
        else:
            shaft = "Women's handbag"
            french_lead = "Sac à main femme"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {styles}".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = BAG_DETAIL_FR.get(extra, "avec bandoulière")
        french_title = f"{french_lead} {french_with}, {french_colour}, style {styles}"
    elif listing_type == "plant":
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        if "cactus" in category:
            shaft = "Decorative cactus with ceramic planter"
            french_lead = "Cactus décoratif avec cache-pot"
        elif "succulent" in category:
            shaft = "Artificial succulent with ceramic planter"
            french_lead = "Succulente artificielle avec cache-pot"
        elif "hanging" in category:
            shaft = "Hanging plant with trailing leaves"
            french_lead = "Plante retombante avec feuilles"
        else:
            shaft = "Indoor plant with decorative pot"
            french_lead = "Plante d'intérieur avec pot décoratif"
        english_title = " ".join(
            f"{shaft}, {colour}, style {styles}".split()
        )
        french_title = f"{french_lead}, {french_colour}, style {styles}"
    elif listing_type == "hat":
        english_title = f"{category} in {colour}, style {styles}, one size fits all"
        french_title = f"Chapeau {colour}, style {styles}, taille unique"
    elif listing_type == "mask":
        mask_details = _infer_mask_details(product)
        extra = next(
            (
                detail
                for detail in mask_details
                if detail not in {"masquerade", "sequin", "sequins"}
            ),
            "cutout",
        )
        with_phrase = MASK_DETAIL_EN.get(extra, "with cutout details")
        if "masquerade" in mask_details or "masquerade" in category:
            shaft = "Sequin masquerade mask"
            french_lead = "Masque de soirée à paillettes"
        elif "sequin" in mask_details or "sequins" in mask_details or "sequin" in category:
            shaft = "Sequin party mask"
            french_lead = "Masque festif à paillettes"
        else:
            shaft = "Women's masquerade mask"
            french_lead = "Masque de soirée femme"
        first_style = _extract_title_style(styles) or "y2k"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style}, one size fits all".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = MASK_DETAIL_FR.get(extra, "avec découpes")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style}, taille unique"
        )
    elif listing_type == "shelf":
        shelf_details = _infer_shelf_details(product)
        extra = next(
            (detail for detail in shelf_details if detail not in {"set", "wall"}),
            "simple",
        )
        with_phrase = SHELF_DETAIL_EN.get(extra, "with a simple cut")
        if "set" in shelf_details or "set of 4" in category:
            shaft = "Set of 4 wall shelves"
            french_lead = "Lot de 4 étagères murales"
        elif "floating" in shelf_details:
            shaft = "Floating wall shelf"
            french_lead = "Étagère murale flottante"
        else:
            shaft = "Wall shelf"
            french_lead = "Étagère murale"
        first_style = _extract_title_style(styles) or "vintage"
        if first_style == "y2k":
            first_style = "vintage"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style}, one size".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = SHELF_DETAIL_FR.get(extra, "simples")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style}, taille unique"
        )
    elif listing_type == "beanie":
        beanie_details = _infer_beanie_details(product)
        extra = next(iter(beanie_details), "fuzzy")
        with_phrase = BEANIE_DETAIL_EN.get(extra, "fuzzy")
        first_style = _extract_title_style(styles) or "y2k"
        english_title = " ".join(
            f"Beanie {with_phrase}, {colour}, style {first_style}, one size fits all".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = BEANIE_DETAIL_FR.get(extra, "en peluche")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"Bonnet {french_with}, {french_colour}, style {french_style}, taille unique"
        )
    elif listing_type == "lamp":
        lamp_details = _infer_lamp_details(product)
        extra = next(
            (detail for detail in lamp_details if detail not in {"gourd", "twisted"}),
            "twisted",
        )
        with_phrase = LAMP_DETAIL_EN.get(extra, "with a twisted design")
        if "gourd" in lamp_details or "gourd" in category:
            shaft = "Gourd-shaped table lamp"
            french_lead = "Lampe de table forme calebasse"
        else:
            shaft = "Table lamp"
            french_lead = "Lampe de table"
        first_style = _extract_title_style(styles) or "decorative"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style}, one size fits all".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = LAMP_DETAIL_FR.get(extra, "avec design torsadé")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style}, taille unique"
        )
    elif listing_type == "chandelier":
        chandelier_details = _infer_chandelier_details(product)
        extra = next(iter(chandelier_details), "multi-tiered")
        with_phrase = CHANDELIER_DETAIL_EN.get(extra, "multi-tiered")
        if "pendant" in category:
            shaft = "Pendant light"
            french_lead = "Suspension"
        elif "flush" in category or "ceiling" in category:
            shaft = "Ceiling light"
            french_lead = "Plafonnier"
        else:
            shaft = "Chandelier"
            french_lead = "Lustre"
        first_style = _extract_title_style(styles) or "decorative"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style}, one size fits all".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = CHANDELIER_DETAIL_FR.get(extra, "à plusieurs niveaux")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style}, taille unique"
        )
    elif listing_type == "carpet":
        carpet_details = _infer_carpet_details(product)
        extra = next(
            (detail for detail in carpet_details if detail not in {"round", "runner"}),
            "geometric",
        )
        with_phrase = CARPET_DETAIL_EN.get(extra, "with a geometric pattern")
        if "round" in carpet_details or "round" in category:
            shaft = "Round rug"
            french_lead = "Tapis rond"
        elif "runner" in carpet_details or "runner" in category:
            shaft = "Runner rug"
            french_lead = "Tapis de couloir"
        else:
            shaft = "Area rug"
            french_lead = "Tapis"
        first_style = _extract_title_style(styles) or "decorative"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style}, one size fits all".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = CARPET_DETAIL_FR.get(extra, "à motif géométrique")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style}, taille unique"
        )
    elif listing_type == "cushion":
        cushion_details = _infer_cushion_details(product)
        extra = next(iter(cushion_details), "textured")
        with_phrase = CUSHION_DETAIL_EN.get(extra, "with a textured weave")
        first_style = _extract_title_style(styles) or "decorative"
        english_title = " ".join(
            f"Decorative cushion {with_phrase}, {colour}, style {first_style}, one size fits all".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = CUSHION_DETAIL_FR.get(extra, "à texture travaillée")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"Coussin {french_with}, {french_colour}, style {french_style}, taille unique"
        )
    elif listing_type == "skirt":
        length = skirt_length or (
            "midi" if "midi" in category else "long" if "long" in category else ""
        )
        details = _infer_details(product)
        lead = details[0] if details else ""
        extra = details[1] if len(details) > 1 else (details[0] if details else "")
        with_phrase = SKIRT_DETAIL_EN.get(extra.replace(" ", "-"), "") if extra else ""
        if lead and extra and lead.replace(" ", "-") == extra.replace(" ", "-"):
            lead = ""
        length_word = "Long" if length == "long" else "Midi" if length == "midi" else ""
        if lead in {"lace", "asymmetric"} and length == "long":
            category_bit = f"{lead.capitalize()} maxi skirt"
        else:
            category_bit = " ".join(part for part in (length_word, lead, "skirt") if part)
        first_style = _extract_title_style(styles) or "elegant"
        english_title = " ".join(
            f"{category_bit} {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_length = "longue" if length == "long" else "midi" if length == "midi" else ""
        french_with = (
            SKIRT_DETAIL_FR.get(extra.replace(" ", "-"), "") if extra else ""
        )
        french_core = " ".join(
            part for part in (f"Jupe {french_length}".strip(), french_with) if part
        )
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = f"{french_core}, {french_colour}, style {french_style} taille S"
    elif listing_type == "jeans":
        jeans_details = _infer_jeans_details(product)
        extra = next(
            (
                detail
                for detail in jeans_details
                if detail not in {"baggy", "wide", "wide-leg"}
            ),
            None,
        )
        with_phrase = JEANS_DETAIL_EN.get(extra, "with a relaxed fit") if extra else "with a relaxed fit"
        category_bit = "Baggy jeans"
        first_style = _extract_title_style(styles) or "streetwear"
        english_title = " ".join(
            f"{category_bit} {with_phrase}, {colour}, style {first_style} size L".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = JEANS_DETAIL_FR.get(extra, "coupe relaxed") if extra else "coupe relaxed"
        french_core = "Jean baggy"
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = f"{french_core} {french_with}, {french_colour}, style {french_style} taille L"
    elif listing_type == "long_boots":
        boots_details = _infer_boots_details(product)
        extra = next(
            (
                detail
                for detail in boots_details
                if detail not in {"knee-high", "thigh-high"}
            ),
            "heeled",
        )
        with_phrase = BOOTS_DETAIL_EN.get(extra, "with a high heel")
        shaft = "Thigh-high boots" if "thigh-high" in boots_details else "Knee-high boots"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {styles} size 38".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = BOOTS_DETAIL_FR.get(extra, "à talons")
        french_lead = "Cuissardes" if "thigh-high" in boots_details else "Bottes hautes"
        french_title = f"{french_lead} {french_with}, {french_colour}, style {styles} taille 38"
    elif listing_type == "heels":
        heels_details = _infer_heels_details(product)
        extra = next(
            (detail for detail in heels_details if detail not in {"pump", "mule"}),
            "sequin",
        )
        with_phrase = HEELS_DETAIL_EN.get(extra, "with sequins")
        if "pump" in heels_details:
            shaft = "Heeled pumps"
            french_lead = "Escarpins"
        elif "mule" in heels_details:
            shaft = "Heeled mules"
            french_lead = "Mules à talons"
        else:
            shaft = "Heeled sandals"
            french_lead = "Sandales à talons"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {styles} size 38".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = HEELS_DETAIL_FR.get(extra, "avec sequins")
        french_title = f"{french_lead} {french_with}, {french_colour}, style {styles} taille 38"
    elif listing_type == "coat":
        coat_details = _infer_coat_details(product)
        extra = next(
            (
                detail
                for detail in coat_details
                if detail not in {"trench", "cape", "long", "belt", "belted"}
            ),
            "collar",
        )
        with_phrase = COAT_DETAIL_EN.get(extra, "with collar details")
        if "trench" in coat_details or "trench" in category:
            shaft = "Long trench coat"
            french_lead = "Trench long"
        elif "cape" in coat_details:
            shaft = "Long cape coat"
            french_lead = "Manteau cape"
        else:
            shaft = "Long coat"
            french_lead = "Manteau long"
        first_style = _extract_title_style(styles) or "elegant"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = COAT_DETAIL_FR.get(extra, "avec col")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style} taille S"
        )
    elif listing_type == "jacket":
        jacket_details = _infer_jacket_details(product)
        extra_skip = {"biker", "bomber", "moto", "patterned"}
        extra = next(
            (detail for detail in jacket_details if detail not in extra_skip),
            "zipper",
        )
        with_phrase = JACKET_DETAIL_EN.get(extra, "with zipper details")
        if "biker" in jacket_details or "moto" in jacket_details or "biker" in category:
            shaft = "Patterned biker jacket"
            french_lead = "Veste biker à motif"
        elif "bomber" in jacket_details or "bomber" in category:
            shaft = "Oversized bomber jacket"
            french_lead = "Blouson bomber oversize"
        elif "cropped" in jacket_details:
            shaft = "Cropped moto jacket"
            french_lead = "Veste cropped moto"
        else:
            shaft = "Women's biker jacket"
            french_lead = "Veste biker femme"
        first_style = _extract_title_style(styles) or "y2k"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = (
            "avec détails zip et col"
            if extra in {"zipper", "zip"}
            else JACKET_DETAIL_FR.get(extra, "avec zip")
        )
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style} taille S"
        )
    elif listing_type == "organizer":
        organizer_details = _infer_organizer_details(product)
        extra = next(
            (
                detail
                for detail in organizer_details
                if detail not in {"rack", "drawer", "countertop"}
            ),
            "tiered",
        )
        with_phrase = ORGANIZER_DETAIL_EN.get(extra, "with tiered shelves")
        if "rack" in organizer_details or "rack" in category:
            shaft = "Rotating rack organizer"
            french_lead = "Organiseur rotatif"
        elif "drawer" in organizer_details or "drawer" in category:
            shaft = "Stackable drawer organizer"
            french_lead = "Organiseur à tiroirs empilable"
        elif "countertop" in organizer_details or "countertop" in category:
            shaft = "Countertop organizer"
            french_lead = "Organiseur de comptoir"
        else:
            shaft = "Kitchen organizer"
            french_lead = "Organiseur de cuisine"
        first_style = _extract_title_style(styles) or "y2k"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = ORGANIZER_DETAIL_FR.get(extra, "à niveaux")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style} taille S"
        )
    elif listing_type == "mirror":
        mirror_details = _infer_mirror_details(product)
        extra = next(
            (
                detail
                for detail in mirror_details
                if detail not in {"tabletop", "asymmetrical", "wall"}
            ),
            "organic",
        )
        with_phrase = MIRROR_DETAIL_EN.get(extra, "with an organic silhouette")
        if "tabletop" in mirror_details or "tabletop" in category:
            shaft = "Tabletop mirror"
            french_lead = "Miroir de table"
        elif "asymmetrical" in mirror_details or "asymmetrical" in category:
            shaft = "Asymmetrical wall mirror"
            french_lead = "Miroir mural asymétrique"
        else:
            shaft = "Wall mirror"
            french_lead = "Miroir mural"
        first_style = _extract_title_style(styles) or "boho"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = MIRROR_DETAIL_FR.get(extra, "de forme organique")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style} taille S"
        )
    elif listing_type == "sculpture":
        sculpture_details = _infer_sculpture_details(product)
        extra = next(
            (
                detail
                for detail in sculpture_details
                if detail not in {"figurine", "abstract"}
            ),
            "delicate",
        )
        with_phrase = SCULPTURE_DETAIL_EN.get(extra, "delicate")
        if "figurine" in sculpture_details or "figurine" in category:
            shaft = "Figurine sculpture"
            french_lead = "Sculpture figurine"
        elif "abstract" in sculpture_details or "abstract" in category:
            shaft = "Abstract sculpture"
            french_lead = "Sculpture abstraite"
        else:
            shaft = "Sculpture"
            french_lead = "Sculpture"
        first_style = _extract_title_style(styles) or "elegant"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = SCULPTURE_DETAIL_FR.get(extra, "délicate")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style} taille S"
        )
    elif listing_type == "curtain":
        curtain_details = _infer_curtain_details(product)
        extra = next(
            (
                detail
                for detail in curtain_details
                if detail not in {"blackout", "sheer"}
            ),
            "lace",
        )
        with_phrase = CURTAIN_DETAIL_EN.get(extra, "lace")
        if "blackout" in curtain_details or "blackout" in category:
            shaft = "Blackout curtain"
            french_lead = "Rideau occultant"
        elif "sheer" in curtain_details or "sheer" in category:
            shaft = "Sheer curtain"
            french_lead = "Rideau voile"
        else:
            shaft = "Curtain"
            french_lead = "Rideau"
        first_style = _extract_title_style(styles) or "gothic"
        english_title = " ".join(
            f"{shaft} {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = CURTAIN_DETAIL_FR.get(extra, "en dentelle")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"{french_lead} {french_with}, {french_colour}, style {french_style} taille S"
        )
    elif listing_type == "necktie":
        necktie_details = _infer_necktie_details(product)
        extra = next(iter(necktie_details), "paisley")
        with_phrase = NECKTIE_DETAIL_EN.get(extra, "paisley-patterned")
        first_style = _extract_title_style(styles) or "elegant"
        english_title = " ".join(
            f"Necktie {with_phrase}, {colour}, style {first_style}".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = NECKTIE_DETAIL_FR.get(extra, "à motifs cachemire")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = f"Cravate {french_with}, {french_colour}, style {french_style}"
    elif listing_type == "leg_warmer":
        leg_warmer_details = _infer_leg_warmer_details(product)
        extra = next(iter(leg_warmer_details), "fluffy")
        with_phrase = LEG_WARMER_DETAIL_EN.get(extra, "fluffy")
        first_style = _extract_title_style(styles) or "boho"
        english_title = " ".join(
            f"Leg warmers {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = LEG_WARMER_DETAIL_FR.get(extra, "moelleuses")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"Jambières {french_with}, {french_colour}, style {french_style} taille S"
        )
    elif listing_type == "jewelry_box":
        jewelry_box_details = _infer_jewelry_box_details(product)
        extra = next(iter(jewelry_box_details), "textured")
        with_phrase = JEWELRY_BOX_DETAIL_EN.get(extra, "textured")
        first_style = _extract_title_style(styles) or "chic"
        english_title = " ".join(
            f"Jewelry box {with_phrase}, {colour}, style {first_style} size L".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = JEWELRY_BOX_DETAIL_FR.get(extra, "texturé")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"Coffret à bijoux {french_with}, {french_colour}, style {french_style} taille L"
        )
    elif listing_type == "lace_umbrella":
        lace_umbrella_details = _infer_lace_umbrella_details(product)
        extra = next(
            (detail for detail in lace_umbrella_details if detail != "lace"),
            "embroidered",
        )
        with_phrase = LACE_UMBRELLA_DETAIL_EN.get(extra, "embroidered")
        first_style = _extract_title_style(styles) or "elegant"
        english_title = " ".join(
            f"Lace parasol {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = LACE_UMBRELLA_DETAIL_FR.get(extra, "en dentelle")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"Ombrelle {french_with}, {french_colour}, style {french_style} taille S"
        )
    elif listing_type == "belt":
        belt_details = _infer_belt_details(product)
        extra = next(iter(belt_details), "carved")
        with_phrase = BELT_DETAIL_EN.get(extra, "carved")
        first_style = _extract_title_style(styles) or "boho"
        english_title = " ".join(
            f"Belt {with_phrase}, {colour}, style {first_style} size S".split()
        )
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_with = BELT_DETAIL_FR.get(extra, "sculptée")
        french_style = "élégant" if first_style == "elegant" else first_style
        french_title = (
            f"Ceinture {french_with}, {french_colour}, style {french_style} taille S"
        )
    else:
        length_word = "Long" if "long" in category or "maxi" in category else "Midi" if "midi" in category else ""
        details = _infer_details(product)
        extra = details[0] if details else ""
        with_phrase = SKIRT_DETAIL_EN.get(extra.replace(" ", "-"), "") if extra else ""
        english_core = " ".join(part for part in (length_word, extra, "dress", with_phrase) if part)
        size = _listing_title_size(listing_type)
        english_title = f"{english_core}, {colour}, style {styles} size {size}"
        french_colour = next(
            (french for english, french in ENGLISH_TO_FRENCH_COLOURS if english == colour),
            colour,
        )
        french_length = "longue" if length_word == "Long" else "midi" if length_word == "Midi" else ""
        french_with = SKIRT_DETAIL_FR.get(extra.replace(" ", "-"), "") if extra else ""
        french_core = " ".join(part for part in (f"Robe {french_length}".strip(), french_with) if part)
        french_title = f"{french_core}, {french_colour}, style {styles} taille {size}"

    english_title = _pad_title(english_title, product, listing_type)
    french_title = _pad_title(french_title, product, listing_type)
    if listing_type == "skirt" and skirt_length:
        product = {**product, "selected_skirt_length": skirt_length}
    listing = ListingPair(
        fictional_brand=_fallback_output_label(product, listing_type, reserved_brands),
        english=ListingVersion(
            title=english_title,
            description="",
            hashtags=FALLBACK_HASHTAGS[listing_type]["english"],
        ),
        french=ListingVersion(
            title=french_title,
            description="",
            hashtags=FALLBACK_HASHTAGS[listing_type]["french"],
        ),
    )
    formatted = apply_required_listing_format(
        listing,
        product,
        listing_type,
        skirt_length=skirt_length,
    )
    return _replace_generic_output_label(
        formatted,
        product,
        listing_type,
        reserved_brands,
    )


async def _generate_json_text(
    prompt: str,
    reference_image: tuple[bytes, str] | None,
) -> str:
    if not settings.gemini_api_key:
        raise AppError(
            "gemini_api_key_missing",
            "Add GEMINI_API_KEY to .env before generating descriptions.",
            status_code=400,
        )

    from google.genai import types

    parts: list[Any] = [types.Part.from_text(text=prompt)]
    if reference_image:
        image_bytes, mime_type = reference_image
        parts.append(types.Part.from_text(
            text=(
                "Attached product image: this is the exact current item. "
                "Use it as the highest authority for colour and visible design."
            )
        ))
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    def call_gemini() -> Any:
        client = _get_gemini_client()
        return client.models.generate_content(
            model=settings.gemini_model,
            contents=parts,
            config=types.GenerateContentConfig(
                responseMimeType="application/json",
                temperature=0.15,
                top_p=0.85,
            ),
        )

    response = await asyncio.to_thread(call_gemini)
    text = getattr(response, "text", None)
    if text:
        return text
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return part_text
    raise AppError(
        "gemini_empty_response",
        "Gemini returned an empty description response.",
        status_code=502,
        retryable=True,
    )


async def generate_listing(
    product: dict[str, Any],
    listing_type: str,
    *,
    skirt_length: str | None = None,
    reserved_brands: set[str] | list[str] | None = None,
) -> ListingPair:
    listing_type = listing_type if listing_type in FALLBACK_HASHTAGS else "dress"
    if listing_type == "skirt" and skirt_length:
        product = {**product, "selected_skirt_length": skirt_length}
    try:
        reference_image = await load_reference_image(product)
    except AppError as exc:
        logger.warning("Reference image could not be loaded: %s", exc)
        reference_image = None

    prompt = _full_prompt(product, listing_type, skirt_length, reserved_brands)
    if settings.gemini_fast_descriptions:
        try:
            payload = await _generate_json_text(
                _fast_prompt(product, listing_type, skirt_length, reserved_brands),
                reference_image,
            )
            draft = validate_fast_draft_payload(payload)
            if fast_draft_titles_are_detailed(draft, listing_type):
                listing = listing_from_fast_draft(draft, listing_type)
                formatted = apply_required_listing_format(
                    listing,
                    product,
                    listing_type,
                    skirt_length=skirt_length,
                )
                return _replace_generic_output_label(
                    formatted,
                    product,
                    listing_type,
                    reserved_brands,
                )
        except (Exception, ValidationError, json.JSONDecodeError) as exc:
            logger.warning("Fast Gemini listing failed: %s", exc)

    try:
        payload = await _generate_json_text(prompt, reference_image)
        listing = validate_gemini_payload(payload)
        formatted = apply_required_listing_format(
            listing,
            product,
            listing_type,
            skirt_length=skirt_length,
        )
        if not listing_titles_are_detailed(formatted, listing_type):
            retry_prompt = "\n\n".join(
                [
                    prompt,
                    (
                        "The previous title was too short. Rewrite the same "
                        "listing with English and French titles between 80 "
                        "and 100 characters. Do not add generic filler. Use "
                        "only visible item details from the attached image. "
                        "Keep the selected image colour, not page text colour."
                    ),
                ]
            )
            payload = await _generate_json_text(retry_prompt, reference_image)
            listing = validate_gemini_payload(payload)
            formatted = apply_required_listing_format(
                listing,
                product,
                listing_type,
                skirt_length=skirt_length,
            )
        return _replace_generic_output_label(
            formatted,
            product,
            listing_type,
            reserved_brands,
        )
    except Exception as exc:
        if reference_image is not None:
            logger.warning(
                "Gemini listing failed with reference image; retrying without it: %s",
                exc,
            )
            try:
                payload = await _generate_json_text(prompt, None)
                listing = validate_gemini_payload(payload)
                formatted = apply_required_listing_format(
                    listing,
                    product,
                    listing_type,
                    skirt_length=skirt_length,
                )
                return _replace_generic_output_label(
                    formatted,
                    product,
                    listing_type,
                    reserved_brands,
                )
            except Exception as retry_exc:
                logger.warning(
                    "Gemini listing failed; using local fallback: %s",
                    retry_exc,
                )
        else:
            logger.warning("Gemini listing failed; using local fallback: %s", exc)
        return local_listing_from_product(
            product,
            listing_type,
            skirt_length=skirt_length,
            reserved_brands=reserved_brands,
        )


_KNOWN_MEASUREMENT_LABELS = (
    "Poitrine",
    "Tour de taille",
    "Hanches",
    "Longueur",
    "Largeur",
    "Longueur des manches",
    "Longueur des brides",
    "Carrure",
    "Tour de bras",
    "Tour de poignet",
    "Cuisse",
    "Entrejambe",
)

_MEASUREMENTS_EXTRACTION_PROMPT = """
You are reading a product measurement diagram image (the kind SHEIN/Temu
attach to a listing, with dimension lines, arrows, and printed numbers in
cm and/or inches).

Read only the numbers actually printed on this exact image. Never invent,
guess, estimate, or round a value that is not clearly printed.

For each dimension you can read, pick the closest matching French label
from this list: {labels}. If none of those fit (for example a lamp's or
chandelier's height, a bag's depth, a diameter, or a circumference not
covered above), invent a short, plain French label for it (for example
"Hauteur", "Profondeur", "Diamètre", "Circonférence"). Never use a
numeric or English label.

Always report the value in centimetres, formatted like "26 cm". If only
inches are printed for a dimension, convert to centimetres and round to
the nearest whole number. If both cm and inches are printed, always use
the printed cm value directly, never recompute it.

If a weight is printed (kg or g), report it separately, converted to kg,
formatted like "0.20 kg". If no weight is printed, use null.

If this image is not a measurement diagram and has no readable dimension
values printed on it, return empty measurements and a null weight.

Return only this exact JSON shape, with no extra commentary:
{{"measurements": {{"<french label>": "<value> cm"}}, "weight": "<value> kg" | null}}
"""


async def extract_measurements_from_image(
    image_url: str,
) -> dict[str, Any]:
    reference_image = await load_image_by_url(image_url)
    if reference_image is None:
        raise AppError(
            "measurement_image_unavailable",
            "That image could not be loaded for measurement extraction.",
            status_code=422,
        )
    prompt = _MEASUREMENTS_EXTRACTION_PROMPT.format(
        labels=", ".join(_KNOWN_MEASUREMENT_LABELS)
    )
    payload = await _generate_measurements_json(prompt, reference_image)
    try:
        cleaned = payload.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removeprefix("```")
            cleaned = cleaned.removesuffix("```").strip()
        data = json.loads(cleaned)
    except (json.JSONDecodeError, AttributeError) as exc:
        raise AppError(
            "measurement_extraction_failed",
            "Gemini did not return readable measurement data for this image.",
            status_code=502,
            retryable=True,
        ) from exc
    raw_measurements = data.get("measurements") if isinstance(data, dict) else None
    measurements = {
        str(label).strip(): str(value).strip()
        for label, value in (raw_measurements or {}).items()
        if str(label).strip() and str(value).strip()
    }
    weight = data.get("weight") if isinstance(data, dict) else None
    weight = str(weight).strip() if weight else None
    return {"measurements": measurements, "weight": weight}


def refresh_listing_with_measurements(product: dict[str, Any]) -> ListingPair | None:
    listing_type = product.get("listing_type")
    english = product.get("english_listing")
    french = product.get("french_listing")
    if not listing_type or not english or not french:
        return None
    # apply_required_listing_format fully rebuilds title/description/hashtags,
    # so the saved listing only needs to supply raw values here; it does not
    # need to satisfy ListingVersion's strict generation-time validation
    # (e.g. >=15 hashtags), which a manual edit may have relaxed since the
    # listing was first generated.
    english_version = ListingVersion.model_construct(
        title=str(english.get("title") or ""),
        description=str(english.get("description") or ""),
        hashtags=list(english.get("hashtags") or []),
    )
    french_version = ListingVersion.model_construct(
        title=str(french.get("title") or ""),
        description=str(french.get("description") or ""),
        hashtags=list(french.get("hashtags") or []),
    )
    listing = ListingPair.model_construct(
        fictional_brand=str(product.get("fictional_brand") or "brand"),
        english=english_version,
        french=french_version,
    )
    return apply_required_listing_format(listing, product, listing_type)


async def _generate_measurements_json(
    prompt: str,
    reference_image: tuple[bytes, str],
) -> str:
    if not settings.gemini_api_key:
        raise AppError(
            "gemini_api_key_missing",
            "Add GEMINI_API_KEY to .env before extracting measurements.",
            status_code=400,
        )

    from google.genai import types

    image_bytes, mime_type = reference_image
    parts: list[Any] = [
        types.Part.from_text(text=prompt),
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
    ]

    def call_gemini() -> Any:
        client = _get_gemini_client()
        return client.models.generate_content(
            model=settings.gemini_model,
            contents=parts,
            config=types.GenerateContentConfig(
                responseMimeType="application/json",
                temperature=0.0,
                top_p=0.85,
            ),
        )

    response = await asyncio.to_thread(call_gemini)
    text = getattr(response, "text", None)
    if text:
        return text
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                return part_text
    raise AppError(
        "gemini_empty_response",
        "Gemini returned an empty measurement extraction response.",
        status_code=502,
        retryable=True,
    )

