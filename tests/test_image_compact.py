from io import BytesIO

from PIL import Image

from backend.services.image_compact_service import compact_image_bytes


def _png_bytes(width: int = 80, height: int = 60) -> bytes:
    image = Image.new("RGB", (width, height), (210, 40, 70))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_compact_keeps_pixel_dimensions_and_png_or_jpeg() -> None:
    original = _png_bytes(128, 96)
    result = compact_image_bytes(original, "png")
    assert result.width == 128
    assert result.height == 96
    assert result.content_type in {"image/png", "image/jpeg"}
    with Image.open(BytesIO(result.data)) as image:
        assert image.size == (128, 96)
        assert image.format in {"PNG", "JPEG"}


def test_png_is_not_converted_to_jpeg() -> None:
    original = _png_bytes()
    result = compact_image_bytes(original, "png")
    assert result.content_type == "image/png"
