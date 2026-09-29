"""The label designer's bitmap is the whole label, so it must start at the paper edge."""

from PIL import Image

from app.drivers.printer_serial import PrinterDriver


def _printer():
    printer = PrinterDriver(width=42, init_serial=False)
    printer.PRINTER_WIDTH_DOTS = 432
    return printer


def _render(align):
    printer = _printer()
    canvas = Image.new("1", (printer.PRINTER_WIDTH_DOTS, 400), 1)
    label = Image.new("1", (320, 240), 0)
    op = {"image": label}
    if align is not None:
        op["align"] = align
    printer._render_op_image(canvas, 0, op, dry_run=False)
    return canvas


def _first_black_column(canvas, row):
    for x in range(canvas.width):
        if canvas.getpixel((x, row)) == 0:
            return x
    return None


def _first_black_row(canvas, column):
    for y in range(canvas.height):
        if canvas.getpixel((column, y)) == 0:
            return y
    return None


def test_a_left_aligned_label_starts_at_the_paper_edge():
    canvas = _render("left")

    assert _first_black_column(canvas, 0) == 0
    assert _first_black_row(canvas, 0) == 0


def test_a_left_aligned_label_is_not_narrowed_by_the_content_margin():
    printer = _printer()
    canvas = Image.new("1", (printer.PRINTER_WIDTH_DOTS, 500), 1)
    label = Image.new("1", (printer.PRINTER_WIDTH_DOTS, 240), 0)

    printer._render_op_image(canvas, 0, {"image": label, "align": "left"}, dry_run=False)

    assert _first_black_column(canvas, 0) == 0
    assert canvas.getpixel((printer.PRINTER_WIDTH_DOTS - 1, 0)) == 0


def test_receipt_images_stay_centred():
    canvas = _render(None)

    # 2px left margin + (428 - 320) // 2
    assert _first_black_column(canvas, 4) == 56


def _ink_rows(canvas):
    rows = [y for y in range(canvas.height) if any(canvas.getpixel((x, y)) == 0 for x in range(canvas.width))]
    return rows[0], rows[-1]


def test_a_label_bitmap_is_exactly_the_label_height():
    """Tear-edge clearance would push the design past the label's trailing edge."""
    printer = _printer()
    label = Image.new("1", (320, 240), 0)

    bitmap = printer._render_unified_bitmap([("image", {"image": label, "align": "left"})])

    assert bitmap.height == 240
    assert _ink_rows(bitmap) == (0, 239)


def test_receipts_keep_their_tear_edge_clearance():
    printer = _printer()
    photo = Image.new("1", (320, 240), 0)

    bitmap = printer._render_unified_bitmap([("image", {"image": photo})])

    # 4 dot top margin + 240 content + SPACING_LARGE * 2
    assert bitmap.height == 4 + 240 + printer.SPACING_LARGE * 2
