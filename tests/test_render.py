from __future__ import annotations

import pytest
from PIL import Image

from thermal.render import PrintRequest, render


@pytest.mark.parametrize(
    ("style", "message"),
    [
        ("note", "# Build complete\n\nThe build is **green**.\n\n- tests pass\n- docs updated"),
        ("receipt", "feature work        1\ntests               12\nbugs introduced      0"),
        ("quote", "Small steps, shipped consistently, beat big plans."),
        ("task", "[x] review the diff\n[ ] fix the flaky test\n[ ] take a walk"),
        ("sticker", "SHIP\nIT"),
    ],
)
def test_styles_render_384px(style: str, message: str) -> None:
    job = render(PrintRequest(message=message, title="TEST", style=style))  # type: ignore[arg-type]
    assert job.width == 384
    assert job.height > 0
    assert job.image.mode == "1"
    assert len(job.image.tobytes()) == 48 * job.height


def test_qr_and_image() -> None:
    photo = Image.new("RGB", (200, 100), "white")
    job = render(
        PrintRequest(
            message="Scan this",
            title="QR",
            qr="https://example.com/some/path",
            image=photo,
        )
    )
    assert job.has_qr
    assert job.has_image
    assert job.width == 384
    assert job.height > 100


def test_empty_request_raises() -> None:
    with pytest.raises(ValueError):
        render(PrintRequest(message="   "))


def test_footer_can_be_hidden() -> None:
    with_footer = render(PrintRequest(message="hello", footer="custom"))
    without_footer = render(PrintRequest(message="hello", footer=""))
    assert without_footer.height < with_footer.height


def test_markdown_links_and_unicode() -> None:
    job = render(
        PrintRequest(
            message="A [link](https://example.com) and `code` and *italic* and 日本語.",
            title="Unicode",
        )
    )
    assert job.width == 384
    assert job.height > 0


def test_words_share_a_baseline() -> None:
    """Words without ascenders ("aaa") must sit as low as words with them ("haa")."""
    job = render(PrintRequest(message="aaa haa", footer=""))
    pixels = job.image.load()

    rows = range(10, 45)
    ink_columns = [any(pixels[x, y] == 0 for y in rows) for x in range(job.width)]

    groups: list[tuple[int, int]] = []
    for x, has_ink in enumerate(ink_columns):
        if not has_ink:
            continue
        # Merge columns that belong to the same word; a real space is wider.
        if groups and x - groups[-1][1] <= 3:
            groups[-1] = (groups[-1][0], x + 1)
        else:
            groups.append((x, x + 1))

    assert len(groups) == 2, "expected two separate words"
    bottoms = [
        max(y for y in rows for x in range(x0, x1) if pixels[x, y] == 0) for x0, x1 in groups
    ]
    assert bottoms[0] == bottoms[1]
