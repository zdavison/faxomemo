"""Daily chess puzzle from Lichess, printed as a newspaper-style diagram."""

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import chess
import requests
from PIL import Image, ImageDraw, ImageFont

from app.module_registry import register_module

LICHESS_DAILY_URL = "https://lichess.org/api/puzzle/daily"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "chess_puzzle_cache"
PIECE_FONT_PATH = Path(__file__).resolve().parent.parent / "data" / "fonts" / "DejaVuSans.ttf"

# White pieces render as outlined glyphs, black as filled — on both square colors.
PIECE_GLYPHS = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
}


class PuzzleFetchError(Exception):
    pass


def fetch_daily_puzzle() -> Dict[str, Any]:
    """Fetch today's puzzle, caching one response per day with stale fallback."""
    import json

    cache_file = CACHE_DIR / f"daily-{date.today().isoformat()}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    try:
        response = requests.get(LICHESS_DAILY_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        stale = sorted(CACHE_DIR.glob("daily-*.json"))
        if stale:
            return json.loads(stale[-1].read_text(encoding="utf-8"))
        raise PuzzleFetchError(f"Lichess unavailable: {exc}") from exc

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


def parse_daily_puzzle(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the printable facts: position, side to move, solution in SAN."""
    puzzle = data["puzzle"]
    board = chess.Board(puzzle["fen"])
    side_to_move = "White" if board.turn == chess.WHITE else "Black"

    solution_san: List[str] = []
    for uci in puzzle["solution"]:
        move = chess.Move.from_uci(uci)
        solution_san.append(board.san(move))
        board.push(move)

    return {
        "id": puzzle["id"],
        "rating": puzzle.get("rating"),
        "themes": puzzle.get("themes", []),
        "fen": puzzle["fen"],
        "side_to_move": side_to_move,
        "solution_san": solution_san,
    }


def format_solution_text(solution_san: List[str], side_to_move: str) -> str:
    """Number SAN moves as printed lines, e.g. "1... Bd4+ 2. Kf1 Rxf4+"."""
    parts = []
    move_number = 1
    index = 0
    if side_to_move == "Black":
        parts.append(f"{move_number}... {solution_san[0]}")
        index = 1
        move_number += 1
    while index < len(solution_san):
        parts.append(f"{move_number}. {solution_san[index]}")
        if index + 1 < len(solution_san):
            parts.append(solution_san[index + 1])
        index += 2
        move_number += 1
    return " ".join(parts)


def _piece_font(size: int) -> Optional[ImageFont.FreeTypeFont]:
    try:
        return ImageFont.truetype(str(PIECE_FONT_PATH), size)
    except OSError:
        return None


def draw_board_image(fen: str, width: int, flipped: bool = False) -> Image.Image:
    """Draw a newspaper-style diagram: hatched dark squares, coordinates, double rule."""
    board = chess.Board(fen)

    coord_font = _piece_font(14)
    coord_space = 16
    border = 3
    cell = (width - coord_space - 2 * border) // 8
    board_px = 8 * cell
    total_w = coord_space + board_px + 2 * border
    total_h = board_px + 2 * border + coord_space

    image = Image.new("1", (total_w, total_h), 1)
    draw = ImageDraw.Draw(image)

    x0 = coord_space
    y0 = 0
    # Double rule around the board
    draw.rectangle([x0, y0, x0 + board_px + 2 * border - 1, y0 + board_px + 2 * border - 1], outline=0)
    draw.rectangle([x0 + 2, y0 + 2, x0 + board_px + 2 * border - 3, y0 + board_px + 2 * border - 3], outline=0)

    piece_font = _piece_font(int(cell * 0.82))

    for row in range(8):
        for col in range(8):
            if flipped:
                file_idx, rank_idx = 7 - col, row
            else:
                file_idx, rank_idx = col, 7 - row
            square = chess.square(file_idx, rank_idx)

            sx = x0 + border + col * cell
            sy = y0 + border + row * cell
            dark = (file_idx + rank_idx) % 2 == 0

            if dark:
                # Diagonal hatching instead of solid fill keeps pieces legible on newsprint
                for offset in range(-cell, cell, 4):
                    draw.line(
                        [sx + max(0, offset), sy + max(0, -offset),
                         sx + min(cell, cell + offset), sy + min(cell, cell - offset)],
                        fill=0,
                        width=1,
                    )

            piece = board.piece_at(square)
            if piece:
                glyph = PIECE_GLYPHS[piece.symbol()]
                # Clear a disc behind the piece so hatching doesn't cut through the glyph
                if dark:
                    pad = max(2, cell // 10)
                    draw.ellipse([sx + pad, sy + pad, sx + cell - pad, sy + cell - pad], fill=1)
                if piece_font:
                    bbox = draw.textbbox((0, 0), glyph, font=piece_font)
                    gw = bbox[2] - bbox[0]
                    gh = bbox[3] - bbox[1]
                    gx = sx + (cell - gw) // 2 - bbox[0]
                    gy = sy + (cell - gh) // 2 - bbox[1]
                    draw.text((gx, gy), glyph, font=piece_font, fill=0)
                else:
                    draw.text((sx + cell // 3, sy + cell // 3), piece.symbol(), fill=0)

    if coord_font:
        for row in range(8):
            rank_idx = row if flipped else 7 - row
            label = str(rank_idx + 1)
            ly = y0 + border + row * cell + (cell - 14) // 2
            draw.text((2, ly), label, font=coord_font, fill=0)
        for col in range(8):
            file_idx = 7 - col if flipped else col
            label = chr(ord("a") + file_idx)
            lx = x0 + border + col * cell + (cell - 8) // 2
            draw.text((lx, y0 + board_px + 2 * border + 1), label, font=coord_font, fill=0)

    return image


def render_solution_image(text: str, width: int) -> Image.Image:
    """Render the solution in small type, rotated 180° (turn the paper to read)."""
    font = _piece_font(15)
    probe = Image.new("1", (1, 1), 1)
    probe_draw = ImageDraw.Draw(probe)

    words = text.split(" ")
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if font and probe_draw.textlength(candidate, font=font) > width - 8:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    line_height = 19
    image = Image.new("1", (width, line_height * len(lines) + 8), 1)
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        if font:
            draw.text((4, 4 + i * line_height), line, font=font, fill=0)
        else:
            draw.text((4, 4 + i * line_height), line, fill=0)
    return image.rotate(180)


@register_module(
    type_id="chess_puzzle",
    label="Daily Chess Puzzle",
    description="The Lichess daily puzzle as a newspaper-style chess diagram, with the solution printed upside-down",
    icon="crown-simple",
    offline=False,
    category="games",
    config_schema={
        "type": "object",
        "properties": {
            "show_rating": {
                "type": "boolean",
                "title": "Show puzzle rating",
                "default": True,
            },
            "show_themes": {
                "type": "boolean",
                "title": "Show puzzle themes (may spoil the solution)",
                "default": False,
            },
            "flip_board": {
                "type": "boolean",
                "title": "Side to move at the bottom",
                "default": True,
            },
        },
    },
)
def format_chess_puzzle_receipt(
    printer, config: Dict[str, Any] = None, module_name: str = None
):
    """Prints the Lichess daily puzzle as a diagram with an upside-down solution."""
    from app.config import format_print_datetime

    config = config or {}
    printer.print_header(module_name or "CHESS PUZZLE", icon="crown-simple")
    printer.print_caption(format_print_datetime())

    try:
        data = fetch_daily_puzzle()
        puzzle = parse_daily_puzzle(data)
    except (PuzzleFetchError, KeyError, ValueError) as exc:
        printer.print_body("Puzzle unavailable")
        printer.print_caption(str(exc))
        return

    caption = f"{puzzle['side_to_move']} to move"
    if config.get("show_rating", True) and puzzle["rating"]:
        caption += f" - Rating {puzzle['rating']}"
    printer.print_subheader(caption)
    if config.get("show_themes", False) and puzzle["themes"]:
        printer.print_caption(", ".join(puzzle["themes"][:4]))

    width = getattr(printer, "PRINTER_WIDTH_DOTS", 384) - 4
    flipped = config.get("flip_board", True) and puzzle["side_to_move"] == "Black"
    printer.print_image(draw_board_image(puzzle["fen"], width=width, flipped=flipped))

    printer.print_line()
    printer.print_caption("Solution (turn over)")
    solution = format_solution_text(puzzle["solution_san"], puzzle["side_to_move"])
    printer.print_image(render_solution_image(solution, width=width))
