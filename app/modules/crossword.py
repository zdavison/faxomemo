import csv
import random
import os
import logging
from typing import List, Dict, Tuple, Optional, Any
import string
from PIL import Image, ImageDraw, ImageFont
from app.module_registry import register_module
from app.config import format_print_datetime

logger = logging.getLogger(__name__)

GRID_WIDTH = 8
DEFAULT_NUM_WORDS = 10
MIN_NUM_WORDS = 3
MAX_NUM_WORDS = 18

# No-auth outlets verified working 2026-08-28, label -> xword-dl keyword.
# Labels are the config values because the settings dropdown shows enum values verbatim.
# Excluded: NYT family (needs subscription cookie), Guardian Prize (solution
# withheld until the prize deadline), USA Today / Billboard / Observer (broken
# upstream in xword-dl).
OUTLETS = {
    "New Yorker Mini": "tnym",
    "LA Times Mini": "latm",
    "Guardian Quick": "grdq",
    "Simply Daily Quick": "sdpq",
    "New Yorker": "tny",
    "LA Times": "lat",
    "Atlantic": "atl",
    "Crossword Club": "club",
    "Daily Beast": "db",
    "Daily Pop": "pop",
    "Der Standard": "std",
    "Guardian Cryptic": "grdc",
    "Guardian Everyman": "grde",
    "Guardian Quiptic": "grdu",
    "Guardian Speedy": "grds",
    "Guardian Weekend": "grdw",
    "Newsday": "nd",
    "Princetonian": "prince",
    "Princetonian Mini": "prince-mini",
    "Puzzmo": "pzm",
    "Puzzmo Big": "pzmb",
    "Simply Daily": "sdp",
    "Simply Daily Cryptic": "sdpc",
    "Universal": "uni",
    "Vox": "vox",
    "Vulture": "vult",
    "The Walrus": "wal",
    "Washington Post": "wp",
}
DEFAULT_OUTLET = "New Yorker Mini"


class PuzzleDownloadError(Exception):
    pass


def fetch_downloaded_puzzle(outlet: str):
    """Fetch today's puzzle for an xword-dl outlet keyword as a puzpy Puzzle."""
    import xword_dl

    try:
        puzzle, _filename = xword_dl.by_keyword(outlet)
        return puzzle
    except Exception as e:
        raise PuzzleDownloadError(str(e)) from e


def _clamp_num_words(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_NUM_WORDS
    return max(MIN_NUM_WORDS, min(MAX_NUM_WORDS, parsed))

class CrosswordGenerator:
    def __init__(self, width: int, initial_height: int):
        self.width = width
        self.height = initial_height # Initial height, will grow if needed
        self.grid = [[' ' for _ in range(width)] for _ in range(initial_height)]
        self.words_placed = []  # List of (word, hint, x, y, direction, number)

    def can_place(self, word: str, x: int, y: int, direction: str) -> bool:
        if x < 0 or y < 0: return False
        if direction == "across":
            if x + len(word) > self.width or y >= self.height: return False
            for i, char in enumerate(word):
                current = self.grid[y][x + i]
                if current != ' ' and current != char: return False
                # Check for adjacent letters to avoid creating new invalid words
                if current == ' ':
                    if (y > 0 and self.grid[y-1][x+i] != ' ') or \
                       (y < self.height - 1 and self.grid[y+1][x+i] != ' '):
                        return False
            # Check start/end neighbors
            if x > 0 and self.grid[y][x-1] != ' ': return False
            if x + len(word) < self.width and self.grid[y][x+len(word)] != ' ': return False
        else:  # "down"
            if y + len(word) > self.height or x >= self.width: return False
            for i, char in enumerate(word):
                current = self.grid[y + i][x]
                if current != ' ' and current != char: return False
                if current == ' ':
                    if (x > 0 and self.grid[y+i][x-1] != ' ') or \
                       (x < self.width - 1 and self.grid[y+i][x+1] != ' '):
                        return False
            if y > 0 and self.grid[y-1][x] != ' ': return False
            if y + len(word) < self.height and self.grid[y+len(word)][x] != ' ': return False
        return True

    def place(self, word: str, hint: str, x: int, y: int, direction: str, number: int):
        for i, char in enumerate(word):
            if direction == "across":
                self.grid[y][x + i] = char
            else:
                self.grid[y + i][x] = char
        self.words_placed.append({'word': word, 'hint': hint, 'x': x, 'y': y, 'dir': direction, 'num': number})

    def _calculate_overlap(self) -> float:
        """Calculates the percentage of placed words that overlap with at least one other word."""
        if len(self.words_placed) < 2: return 0.0
        
        occupied_cells = set()
        word_cells = {} # {word_index: set_of_cells}

        for idx, placement in enumerate(self.words_placed):
            current_word_cells = set()
            for k in range(len(placement['word'])):
                if placement['dir'] == "across":
                    cell = (placement['x'] + k, placement['y'])
                else:
                    cell = (placement['x'], placement['y'] + k)
                occupied_cells.add(cell)
                current_word_cells.add(cell)
            word_cells[idx] = current_word_cells

        total_word_length = sum(len(p['word']) for p in self.words_placed)
        if total_word_length == 0: return 0.0

        # Count actual overlaps
        overlap_count = 0
        for i in range(len(self.words_placed)):
            for j in range(i + 1, len(self.words_placed)):
                overlap_count += len(word_cells[i].intersection(word_cells[j]))

        unique_cells = len(occupied_cells)
        return (total_word_length - unique_cells) / total_word_length

    def _calculate_puzzle_score(self, num_words_placed: int, overlap_percentage: float, grid_height: int) -> float:
        """
        Calculates a score for the puzzle based on words placed, overlap, and grid size.
        Prioritizes more words, higher overlap, and smaller grid height.
        """
        # Scale factors
        score = (num_words_placed * 1000) + (overlap_percentage * 500) - (grid_height * 10)
        return score

    def generate(self, word_pool: List[Tuple[str, str]], num_words: int):
        # Sort word_pool by length descending to prioritize placing longer words first
        word_pool.sort(key=lambda x: len(x[0]), reverse=True)
        
        max_grid_height = self.width * 4 # Allow height to grow up to 4 times the width
        best_grid = None
        best_words_placed = []
        best_score = -1.0
        best_height = self.height

        # Growth loop: increase height if words don't fit
        current_height_attempt = self.width # Start with height equal to width
        
        while current_height_attempt <= max_grid_height:
            # Pre-calculate the grid and properties for this height level
            current_height_grid = [[' ' for _ in range(self.width)] for _ in range(current_height_attempt)]
            placed_all_at_this_height = False
            
            # Multiple attempts per height to maximize overlap and find good layouts
            for attempt in range(40): # 40 attempts as requested
                # Temporary instance state for this attempt
                self.grid = [row[:] for row in current_height_grid]
                self.words_placed = []
                self.height = current_height_attempt
                
                # Create a pool of words to try placing for this attempt.
                # Try to place more words than the target `num_words` to give more options,
                # as some words might be difficult to place.
                # Cap the consideration pool to avoid excessive computation.
                words_to_consider_for_placement = list(word_pool)
                random.shuffle(words_to_consider_for_placement)
                # Consider up to 2x the target number of words, or all available if fewer.
                words_to_consider_for_placement = words_to_consider_for_placement[:min(len(word_pool), num_words * 2)]
                
                # Keep track of words successfully placed in this attempt (by their original tuple)
                placed_words_in_this_attempt_set = set()

                # 1. Place the first word (seed) from the consideration pool
                if words_to_consider_for_placement:
                    first_word_tuple = words_to_consider_for_placement.pop(0)
                    first_word, first_hint = first_word_tuple
                    # Try to place in the center
                    start_x = (self.width - len(first_word)) // 2
                    start_y = current_height_attempt // 2
                    
                    if start_x >= 0 and self.can_place(first_word, start_x, start_y, "across"):
                        self.place(first_word, first_hint, start_x, start_y, "across", 0) # Number will be finalized later
                        placed_words_in_this_attempt_set.add(first_word_tuple)
                    elif len(first_word) <= current_height_attempt: # Try down if across fails or doesn't fit
                        start_x = self.width // 2
                        start_y = (current_height_attempt - len(first_word)) // 2
                        if start_y >= 0 and self.can_place(first_word, start_x, start_y, "down"):
                            self.place(first_word, first_hint, start_x, start_y, "down", 0)
                            placed_words_in_this_attempt_set.add(first_word_tuple)
                
                # 2. Try to place remaining words by intersection
                # Iterate over the remaining words in the consideration pool,
                # prioritizing those not yet placed.
                # We'll re-shuffle the remaining words to introduce more randomness for intersection attempts.
                remaining_words_to_try = [w_tuple for w_tuple in words_to_consider_for_placement if w_tuple not in placed_words_in_this_attempt_set]
                random.shuffle(remaining_words_to_try)

                for word_tuple in remaining_words_to_try:
                    if len(self.words_placed) >= num_words:
                        break

                    word, hint = word_tuple

                    # Try to find an intersection point with an already placed word
                    possible_placements = []
                    for placed_word_info in self.words_placed:
                        for i, char_placed in enumerate(placed_word_info['word']):
                            for j, char_current in enumerate(word):
                                if char_placed == char_current:
                                    # Calculate potential start position for the current word
                                    if placed_word_info['dir'] == "across":
                                        # Current word will go down
                                        nx = placed_word_info['x'] + i
                                        ny = placed_word_info['y'] - j
                                        new_dir = "down"
                                    else:
                                        # Current word will go across
                                        nx = placed_word_info['x'] - j
                                        ny = placed_word_info['y'] + i
                                        new_dir = "across"

                                    if nx >= 0 and ny >= 0 and self.can_place(word, nx, ny, new_dir):
                                        # Count how many characters overlap with this specific placement
                                        intersections = 0
                                        for k, char in enumerate(word):
                                            tx, ty = (nx + k, ny) if new_dir == "across" else (nx, ny + k)
                                            if self.grid[ty][tx] == char:
                                                intersections += 1
                                        possible_placements.append((nx, ny, new_dir, intersections))
                    
                    if possible_placements:
                        # Prioritize placements with the most intersections
                        # Use distance to center as a tie-breaker for better density
                        center_x, center_y = self.width // 2, self.height // 2
                        possible_placements.sort(key=lambda p: (p[3], -(abs(p[0] - center_x) + abs(p[1] - center_y))), reverse=True)
                        
                        px, py, pdir, _ = possible_placements[0]
                        self.place(word, hint, px, py, pdir, 0)
                        placed_words_in_this_attempt_set.add(word_tuple)
                
                current_overlap = self._calculate_overlap()
                current_score = self._calculate_puzzle_score(len(self.words_placed), current_overlap, self.height)

                if len(self.words_placed) == num_words:
                    placed_all_at_this_height = True

                if current_score > best_score:
                    best_score = current_score
                    best_grid = [row[:] for row in self.grid]
                    best_words_placed = list(self.words_placed)
                    best_height = self.height
                    logger.debug(f"H={self.height}, Attempt={attempt}: New best score {best_score:.2f} with {len(best_words_placed)} words.")
            
            if placed_all_at_this_height:
                break # All words placed at this height, no need to grow further
            
            current_height_attempt += 1 # Increment height for next iteration
            logger.debug(f"Growing crossword grid to height {current_height_attempt}")

        # After all attempts, apply the best found puzzle
        if best_grid is not None:
            self.grid = best_grid
            self.words_placed = best_words_placed
            self.height = best_height
            logger.info(f"Final crossword generated with height {self.height}, {len(self.words_placed)} words, score {best_score:.2f}")
        else:
            logger.warning(f"Could not generate a satisfactory crossword after growing to height {max_grid_height}.")
            # Fallback to an empty grid or a minimal one if nothing was placed
            self.grid = [[' ' for _ in range(self.width)] for _ in range(self.width)] # Reset to initial height
            self.words_placed = []
            self.height = self.width

        self.finalize_numbers()

    def finalize_numbers(self):
        # Identify cells that start a word
        starters = {}
        for wp in self.words_placed:
            pos = (wp['x'], wp['y'])
            if pos not in starters:
                starters[pos] = []
            starters[pos].append(wp)
            
        # Sort positions to assign numbers in order
        sorted_pos = sorted(starters.keys(), key=lambda p: (p[1], p[0]))
        
        final_words = []
        for i, pos in enumerate(sorted_pos, 1):
            for wp in starters[pos]:
                wp['num'] = i
                final_words.append(wp)
        self.words_placed = final_words

def render_crossword_grid(generator: CrosswordGenerator, cell_size: int, font_path: str = None) -> Image.Image:
    """Renders the crossword grid as a 1-bit bitmap."""
    # Calculate used bounds to crop empty space
    min_y = generator.height
    max_y = 0
    for wp in generator.words_placed:
        min_y = min(min_y, wp['y'])
        h = len(wp['word']) if wp['dir'] == "down" else 1
        max_y = max(max_y, wp['y'] + h)
    
    # Add 1 row margin
    start_y = max(0, min_y - 1)
    end_y = min(generator.height, max_y + 1)
    used_rows = end_y - start_y

    img_w = generator.width * cell_size + 2
    img_h = used_rows * cell_size + 2
    
    # Start with a black image (0 = black) to form the blocks and grid lines
    img = Image.new("1", (img_w, img_h), 0)
    draw = ImageDraw.Draw(img)
    
    # Load a small font for numbers
    try:
        num_font = ImageFont.truetype(font_path, max(8, cell_size // 3)) if font_path else ImageFont.load_default()
    except:
        num_font = ImageFont.load_default()

    # Draw white squares for letters
    for y in range(start_y, end_y):
        for x in range(generator.width):
            if generator.grid[y][x] != ' ':
                # Draw white inset (1 = white)
                # This leaves a black border around each white square
                x0 = x * cell_size + 2
                y0 = (y - start_y) * cell_size + 2
                x1 = (x + 1) * cell_size - 1
                y1 = (y - start_y + 1) * cell_size - 1
                draw.rectangle([x0, y0, x1, y1], fill=1)

    # Draw numbers
    # We need to know which cells have a number
    num_map = {}
    for wp in generator.words_placed:
        num_map[(wp['x'], wp['y'] - start_y)] = wp['num']
        
    for (x, y), num in num_map.items():
        draw.text((x * cell_size + 4, y * cell_size + 3), str(num), font=num_font, fill=0)
        
    return img

def render_downloaded_grid(puzzle, cell_size: int, font_path: str = None) -> Image.Image:
    """Renders a puzpy Puzzle grid as a 1-bit bitmap ('.' in fill = block)."""
    img_w = puzzle.width * cell_size + 2
    img_h = puzzle.height * cell_size + 2
    img = Image.new("1", (img_w, img_h), 0)
    draw = ImageDraw.Draw(img)

    try:
        num_font = ImageFont.truetype(font_path, max(8, cell_size // 3)) if font_path else ImageFont.load_default()
    except Exception:
        num_font = ImageFont.load_default()

    for y in range(puzzle.height):
        for x in range(puzzle.width):
            if puzzle.fill[y * puzzle.width + x] != '.':
                x0 = x * cell_size + 2
                y0 = y * cell_size + 2
                x1 = (x + 1) * cell_size - 1
                y1 = (y + 1) * cell_size - 1
                draw.rectangle([x0, y0, x1, y1], fill=1)

    numbering = puzzle.clue_numbering()
    num_cells = {}
    for clue in numbering.across + numbering.down:
        num_cells[clue['cell']] = clue['num']
    for cell, num in num_cells.items():
        x = cell % puzzle.width
        y = cell // puzzle.width
        draw.text((x * cell_size + 4, y * cell_size + 3), str(num), font=num_font, fill=0)

    return img


def render_upside_down_text(text: str, width_dots: int, font_path: str = None) -> Image.Image:
    """Renders text as a 1-bit bitmap rotated 180 degrees (read by flipping the paper)."""
    font_size = 18
    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    margin = 2
    available = width_dots - margin * 2
    lines = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if font.getbbox(candidate)[2] <= available or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    ascent, descent = (font.getmetrics() if hasattr(font, "getmetrics") else (10, 3))
    line_height = ascent + descent + 2
    img = Image.new("1", (width_dots, line_height * max(1, len(lines)) + margin * 2), 1)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((margin, margin + i * line_height), line, font=font, fill=0)
    return img.rotate(180)


def _print_answers_upside_down(printer, answers: List[str]):
    printer.print_line()
    width = getattr(printer, "PRINTER_WIDTH_DOTS", 384)
    img = render_upside_down_text(
        "SOLUTION: " + ", ".join(answers), width, _get_driver_font_path(printer)
    )
    printer.print_image(img)
    printer.feed(1)


def _slot_answer(puzzle, clue: Dict[str, Any], direction: str) -> str:
    step = 1 if direction == "across" else puzzle.width
    return "".join(
        puzzle.solution[clue['cell'] + i * step] for i in range(clue['len'])
    )


def _get_driver_font_path(printer) -> Optional[str]:
    if hasattr(printer, "_get_font"):
        f = printer._get_font("regular")
        if hasattr(f, "path"):
            return f.path
    return None


def _print_downloaded(printer, puzzle, outlet: str, module_name: str = None):
    printer.print_header(module_name or "CROSSWORD", icon="grid-nine")
    printer.print_caption(format_print_datetime())
    if puzzle.title:
        printer.print_subheader(puzzle.title)
    byline = " - ".join(p for p in [puzzle.author, outlet] if p)
    if byline:
        printer.print_caption(byline)
    printer.feed(1)

    printer_width = getattr(printer, "PRINTER_WIDTH_DOTS", 384)
    cell_size = (printer_width - 2) // puzzle.width
    grid_img = render_downloaded_grid(puzzle, cell_size, _get_driver_font_path(printer))
    printer.print_image(grid_img)
    printer.feed(1)

    numbering = puzzle.clue_numbering()
    if numbering.across:
        printer.print_bold("ACROSS")
        for clue in numbering.across:
            printer.print_body(f"{clue['num']}. {clue['clue']}")
        printer.feed(1)

    if numbering.down:
        printer.print_bold("DOWN")
        for clue in numbering.down:
            printer.print_body(f"{clue['num']}. {clue['clue']}")
        printer.feed(1)

    answers = [
        f"{clue['num']}A:{_slot_answer(puzzle, clue, 'across')}" for clue in numbering.across
    ] + [
        f"{clue['num']}D:{_slot_answer(puzzle, clue, 'down')}" for clue in numbering.down
    ]
    _print_answers_upside_down(printer, answers)


@register_module(
    type_id="crossword",
    label="Crossword",
    description="Generates or downloads a crossword puzzle",
    icon="grid-nine",
    offline=True,
    category="puzzles",
    config_schema={
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "title": "Source",
                "enum": ["Generated", "Downloaded"],
                "default": "Generated"
            },
            "num_words": {
                "type": "integer",
                "title": "Number of Words",
                "default": DEFAULT_NUM_WORDS,
                "minimum": MIN_NUM_WORDS,
                "maximum": MAX_NUM_WORDS
            },
            "difficulty": {
                "type": "string",
                "title": "Difficulty",
                "enum": ["Easy", "Medium", "Hard"],
                "default": "Easy"
            },
            "outlet": {
                "type": "string",
                "title": "Daily Puzzle",
                "enum": list(OUTLETS.keys()),
                "default": DEFAULT_OUTLET
            }
        }
    },
    ui_schema={
        "num_words": {"ui:showWhen": {"field": "source", "value": "Generated"}},
        "difficulty": {"ui:showWhen": {"field": "source", "value": "Generated"}},
        "outlet": {"ui:showWhen": {"field": "source", "value": "Downloaded"}},
    }
)
def execute_crossword(printer, config: Dict[str, Any], module_name: str = None):
    """Module entry point."""
    if config.get("source", "Generated") == "Downloaded":
        outlet = config.get("outlet", DEFAULT_OUTLET)
        if outlet not in OUTLETS:
            outlet = DEFAULT_OUTLET
        try:
            puzzle = fetch_downloaded_puzzle(OUTLETS[outlet])
        except PuzzleDownloadError as e:
            logger.warning(f"Crossword download failed for '{outlet}': {e}")
            printer.print_header(module_name or "CROSSWORD", icon="grid-nine")
            printer.print_caption(format_print_datetime())
            printer.print_body(f"Download failed for {outlet}.")
            printer.feed(1)
            return
        _print_downloaded(printer, puzzle, outlet, module_name)
        return

    _print_generated(printer, config, module_name)


def _print_generated(printer, config: Dict[str, Any], module_name: str = None):
    num_words = _clamp_num_words(config.get("num_words", DEFAULT_NUM_WORDS))
    difficulty = config.get("difficulty", "Easy")

    # Calculate dynamic block size to fill the paper width (384 dots)
    printer_width = getattr(printer, "PRINTER_WIDTH_DOTS", 384)
    block_size = (printer_width - 2) // GRID_WIDTH

    # 1. Load word list
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(app_dir, "data", "crossword_words.csv")
    
    easy_pool = []
    hard_pool = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                w = row['word'].strip().upper()
                h = row['hint'].strip()
                if w and h and w.isalpha() and 2 <= len(w) <= (GRID_WIDTH * 3):
                    d = row.get('difficulty', 'easy').strip().lower()
                    if d == 'hard':
                        hard_pool.append((w, h))
                    else:
                        easy_pool.append((w, h))
    except Exception as e:
        logger.error(f"Failed to load crossword words: {e}")
        printer.print_header(module_name or "CROSSWORD", icon="grid-nine")
        printer.print_body("Error: Could not load word list.")
        return
    
    # 2. Select words based on difficulty
    word_pool = []
    if difficulty == "Easy":
        word_pool = easy_pool
    elif difficulty == "Hard":
        word_pool = hard_pool
    else:  # Medium: 50/50 mix
        # We balance the pools to ensure an even distribution when the generator picks
        sample_size = min(len(easy_pool), len(hard_pool))
        word_pool = random.sample(easy_pool, sample_size) + random.sample(hard_pool, sample_size)

    if not word_pool:
        printer.print_header(module_name or "CROSSWORD", icon="grid-nine")
        printer.print_body("Error: Word list is empty.")
        return

    # 3. Generate Puzzle
    # CrosswordGenerator.generate now handles internal attempts and height growth
    gen = CrosswordGenerator(GRID_WIDTH, GRID_WIDTH)
    gen.generate(list(word_pool), num_words)
    
    if not gen.words_placed:
        printer.print_header(module_name or "CROSSWORD", icon="grid-nine")
        printer.print_body("Could not generate a puzzle layout.")
        return

    # 4. Print Header
    printer.print_header(module_name or "CROSSWORD", icon="grid-nine")
    printer.print_caption(format_print_datetime())
    printer.print_subheader(f"Level: {difficulty}")
    printer.feed(1)

    # 5. Print Grid
    grid_img = render_crossword_grid(gen, block_size, _get_driver_font_path(printer))
    printer.print_image(grid_img)
    printer.feed(1)

    # 6. Print Clues
    across = [w for w in gen.words_placed if w['dir'] == "across"]
    down = [w for w in gen.words_placed if w['dir'] == "down"]

    if across:
        printer.print_bold("ACROSS")
        for clue in sorted(across, key=lambda x: x['num']):
            printer.print_body(f"{clue['num']}. {clue['hint']}")
        printer.feed(1)

    if down:
        printer.print_bold("DOWN")
        for clue in sorted(down, key=lambda x: x['num']):
            printer.print_body(f"{clue['num']}. {clue['hint']}")
        printer.feed(1)

    # 7. Print Answer Key
    answers = []
    for clue in sorted(gen.words_placed, key=lambda x: x['num']):
        answers.append(f"{clue['num']}{clue['dir'][0].upper()}:{clue['word']}")

    _print_answers_upside_down(printer, answers)
